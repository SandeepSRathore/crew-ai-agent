import threading
import time

import pytest

from app.runs import RunManager

REVIEW = {
    "summary": "Works but fragile.",
    "verdict": "needs_changes",
    "issues": [{"severity": "major", "title": "No input check", "detail": "d", "line_hint": "main", "suggestion": "s"}],
}


def fake_runner(topic, manager):
    """Reports progress the way the CrewAI listener does, without calling a model."""
    manager.step_started("research")
    manager.step_activity("research", "Using search: " + topic)
    manager.step_completed("research", "<think>...</think>## Summary\nnotes")
    manager.step_started("coding")
    manager.step_completed("coding", "```python\nprint('hi')\n```")
    manager.step_started("review")
    manager.step_completed("review", "raw", REVIEW)


def wait_until_finished(manager: RunManager, run_id: str, timeout: float = 5) -> dict:
    deadline = time.time() + timeout
    while not manager.is_finished(run_id):
        assert time.time() < deadline, "run did not finish"
        time.sleep(0.02)
    return manager.get(run_id)


@pytest.fixture
def make_manager(tmp_path):
    def make(runner=fake_runner):
        return RunManager(outputs_dir=tmp_path, runner=runner)

    return make


@pytest.fixture
def blocking_runner():
    """A runner that waits until the test releases it."""
    release = threading.Event()

    def runner(topic, manager):
        manager.step_started("research")
        release.wait(5)
        fake_runner(topic, manager)

    runner.release = release
    return runner
