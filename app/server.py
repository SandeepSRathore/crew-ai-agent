import asyncio
import json
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import ALLOWED_HOSTS, MODEL, OLLAMA_BASE_URL, ollama_model_name
from app.runs import RunBusyError, RunManager, manager

STATIC_DIR = Path(__file__).parent / "static"
POLL_SECONDS = 0.5


class StartRun(BaseModel):
    topic: str = Field(min_length=1, max_length=4000)


def create_app(run_manager: RunManager = manager, allowed_hosts: list[str] = ALLOWED_HOSTS) -> FastAPI:
    app = FastAPI(title="CrewAI Research → Code → Review")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @app.get("/api/health")
    async def health():
        model = ollama_model_name()
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
                resp.raise_for_status()
            names = {m["name"] for m in resp.json().get("models", [])}
        except (httpx.HTTPError, ValueError) as exc:
            return {"ok": False, "model": MODEL, "detail": f"Ollama not reachable at {OLLAMA_BASE_URL}: {exc}"}
        if model not in names:
            return {"ok": False, "model": MODEL, "detail": f"Model {model} not pulled; run: ollama pull {model}"}
        return {"ok": True, "model": MODEL, "detail": f"Ollama at {OLLAMA_BASE_URL}"}

    @app.post("/api/runs", status_code=201)
    def start_run(body: StartRun):
        if not body.topic.strip():
            raise HTTPException(422, "Topic must not be blank")
        try:
            run_id = run_manager.start(body.topic)
        except RunBusyError as exc:
            raise HTTPException(409, {"message": "A run is already in progress", "run_id": str(exc)})
        return {"run_id": run_id}

    @app.get("/api/runs")
    def list_runs():
        return {"runs": run_manager.list_runs(), "active_run_id": run_manager.current_run_id}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        run = run_manager.get(run_id)
        if run is None:
            raise HTTPException(404, "Run not found")
        return run

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str):
        if run_manager.get(run_id) is None:
            raise HTTPException(404, "Run not found")

        async def stream():
            # Every message carries the latest run snapshot, so the client just re-renders it.
            yield _sse("snapshot", {"run": run_manager.get(run_id)})
            seq = len(run_manager.events_since(run_id, 0))
            idle = 0
            while True:
                events = run_manager.events_since(run_id, seq)
                if events:
                    seq += len(events)
                    idle = 0
                    run = run_manager.get(run_id)
                    for event in events:
                        yield _sse("progress", {"event": event, "run": run})
                elif run_manager.is_finished(run_id):
                    yield _sse("end", {"run": run_manager.get(run_id)})
                    return
                else:
                    idle += 1
                    if idle % 30 == 0:
                        yield ": keep-alive\n\n"
                await asyncio.sleep(POLL_SECONDS)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


app = create_app()
