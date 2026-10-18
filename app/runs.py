"""Run state for the pipeline: one active run at a time, an append-only event log per run
(used by the SSE stream) and persistence of finished runs to outputs/<run_id>/."""

import copy
import json
import logging
import secrets
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from app.config import OUTPUTS_DIR
from app.crew import STEPS
from app.models import ReviewReport
from app.text_utils import extract_python, strip_think

log = logging.getLogger(__name__)

STEP_IDS = [s["id"] for s in STEPS]
# Event handlers run on a thread pool, so events can arrive out of order; a step's status only moves forward.
_STATUS_RANK = {"pending": 0, "running": 1, "done": 2, "failed": 2}


class RunBusyError(Exception):
    pass


def parse_review(raw: str, parsed: dict | None = None) -> dict | None:
    """Return the review as a ReviewReport dict, or None if the model's output isn't usable JSON."""
    if parsed:
        return ReviewReport.model_validate(parsed).model_dump()
    text = strip_think(raw)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return ReviewReport.model_validate_json(text[start : end + 1]).model_dump()
    except ValueError:
        return None


def _new_run(run_id: str, topic: str) -> dict:
    return {
        "id": run_id,
        "topic": topic,
        "status": "running",
        "created_at": time.time(),
        "finished_at": None,
        "error": None,
        "warnings": [],
        "steps": {
            s["id"]: {
                **s,
                "status": "pending",
                "started_at": None,
                "finished_at": None,
                "activity": [],
                "output": None,
                "error": None,
            }
            for s in STEPS
        },
        "results": {"research": None, "code": None, "review": None, "review_raw": None},
    }


class RunManager:
    def __init__(self, outputs_dir: Path = OUTPUTS_DIR, runner: Callable[[str, "RunManager"], None] | None = None):
        """`runner(topic, manager)` executes the crew and reports progress through the manager's
        step_* methods; it defaults to the real CrewAI pipeline."""
        self.outputs_dir = Path(outputs_dir)
        self._runner = runner
        self._lock = threading.RLock()
        self._runs: dict[str, dict] = {}
        self._events: dict[str, list[dict]] = {}
        self.current_run_id: str | None = None

    # ---- lifecycle -------------------------------------------------------------------------

    def start(self, topic: str) -> str:
        topic = topic.strip()
        if not topic:
            raise ValueError("topic must not be empty")
        with self._lock:
            if self.current_run_id:
                raise RunBusyError(self.current_run_id)
            run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
            self._runs[run_id] = _new_run(run_id, topic)
            self._events[run_id] = []
            self.current_run_id = run_id
            self._emit(run_id, "run_started", topic=topic)
        threading.Thread(target=self._execute, args=(run_id, topic), name=f"run-{run_id}", daemon=True).start()
        return run_id

    def _execute(self, run_id: str, topic: str) -> None:
        runner = self._runner
        if runner is None:
            from app.pipeline import run_pipeline

            runner = run_pipeline
        try:
            runner(topic, self)
        except Exception as exc:  # the run must always end up in a terminal state
            log.exception("run %s failed", run_id)
            self._finish(run_id, error=f"{type(exc).__name__}: {exc}")
        else:
            self._finish(run_id)

    def _finish(self, run_id: str, error: str | None = None) -> None:
        with self._lock:
            run = self._runs[run_id]
            for step in run["steps"].values():
                if step["status"] == "running":
                    if error:
                        step.update(status="failed", error=error, finished_at=time.time())
                    elif step["output"] is not None:
                        step.update(status="done", finished_at=time.time())
            if not error:
                missing = [s for s in STEP_IDS if run["steps"][s]["status"] != "done"]
                if missing:
                    error = f"Pipeline ended before these steps finished: {', '.join(missing)}"
            run.update(status="failed" if error else "done", error=error, finished_at=time.time())
            self._emit(run_id, "run_failed" if error else "run_completed", error=error)
            self.current_run_id = None
            self._save(run_id)

    # ---- progress reporting (called from CrewAI event handlers / the pipeline) ---------------

    def _step(self, step_id: str) -> tuple[str, dict] | None:
        run_id = self.current_run_id
        if run_id is None or step_id not in STEP_IDS:
            return None
        return run_id, self._runs[run_id]["steps"][step_id]

    def _advance(self, step: dict, status: str) -> bool:
        if _STATUS_RANK[status] < _STATUS_RANK[step["status"]]:
            return False
        step["status"] = status
        return True

    def step_started(self, step_id: str) -> None:
        with self._lock:
            if not (found := self._step(step_id)):
                return
            run_id, step = found
            if step["started_at"] is None:
                step["started_at"] = time.time()
            if self._advance(step, "running"):
                self._emit(run_id, "step_started", step=step_id)

    def step_activity(self, step_id: str, text: str) -> None:
        with self._lock:
            if not (found := self._step(step_id)):
                return
            run_id, step = found
            step["activity"].append({"ts": time.time(), "text": text})
            self._emit(run_id, "step_activity", step=step_id, text=text)

    def step_completed(self, step_id: str, raw: str, parsed: dict | None = None) -> None:
        with self._lock:
            if not (found := self._step(step_id)):
                return
            run_id, step = found
            output = strip_think(raw)
            if step["status"] == "done" and step["output"] == output:
                return  # the pipeline re-reports final outputs after the event handlers did
            step.update(output=output, finished_at=time.time())
            step["started_at"] = step["started_at"] or step["finished_at"]
            self._advance(step, "done")
            results = self._runs[run_id]["results"]
            if step_id == "research":
                results["research"] = output
            elif step_id == "coding":
                results["code"] = extract_python(raw)
            elif step_id == "review":
                results["review_raw"] = output
                results["review"] = parse_review(raw, parsed)
            self._emit(run_id, "step_completed", step=step_id)

    def step_failed(self, step_id: str, error: str) -> None:
        with self._lock:
            if not (found := self._step(step_id)):
                return
            run_id, step = found
            step.update(error=error, finished_at=time.time())
            self._advance(step, "failed")
            self._emit(run_id, "step_failed", step=step_id, error=error)

    def warn(self, text: str) -> None:
        with self._lock:
            if (run_id := self.current_run_id) is None:
                return
            self._runs[run_id]["warnings"].append(text)
            self._emit(run_id, "warning", text=text)

    # ---- reading -------------------------------------------------------------------------

    def _emit(self, run_id: str, type_: str, **data) -> None:
        events = self._events[run_id]
        events.append({"seq": len(events), "type": type_, "ts": time.time(), **data})

    def get(self, run_id: str) -> dict | None:
        with self._lock:
            if run_id in self._runs:
                return self._snapshot(run_id)
        path = self.outputs_dir / run_id / "run.json"
        if path.is_file():
            return json.loads(path.read_text())
        return None

    def events_since(self, run_id: str, seq: int) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._events.get(run_id, [])[seq:])

    def is_finished(self, run_id: str) -> bool:
        run = self.get(run_id)
        return run is None or run["status"] != "running"

    def list_runs(self, limit: int = 50) -> list[dict]:
        runs: dict[str, dict] = {}
        if self.outputs_dir.is_dir():
            for path in self.outputs_dir.glob("*/run.json"):
                try:
                    runs[path.parent.name] = json.loads(path.read_text())
                except (OSError, ValueError):
                    log.warning("skipping unreadable %s", path)
        with self._lock:
            for run_id in self._runs:
                runs[run_id] = self._snapshot(run_id)
        summaries = [
            {k: r[k] for k in ("id", "topic", "status", "created_at", "finished_at")} for r in runs.values()
        ]
        return sorted(summaries, key=lambda r: r["created_at"], reverse=True)[:limit]

    def _snapshot(self, run_id: str) -> dict:
        run = copy.deepcopy(self._runs[run_id])
        run["current_step"] = next((s for s in STEP_IDS if run["steps"][s]["status"] == "running"), None)
        return run

    def _save(self, run_id: str) -> None:
        run = self._snapshot(run_id)
        folder = self.outputs_dir / run_id
        try:
            folder.mkdir(parents=True, exist_ok=True)
            results = run["results"]
            if results["research"]:
                (folder / "research.md").write_text(results["research"])
            if results["code"]:
                (folder / "main.py").write_text(results["code"])
            if results["review"]:
                (folder / "review.json").write_text(json.dumps(results["review"], indent=2))
            elif results["review_raw"]:
                (folder / "review.md").write_text(results["review_raw"])
            (folder / "run.json").write_text(json.dumps(run, indent=2))
        except OSError:
            log.exception("could not save run %s", run_id)


manager = RunManager()
