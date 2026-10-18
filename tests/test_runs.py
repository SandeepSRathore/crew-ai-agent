import json

import pytest

from app.runs import RunBusyError
from tests.conftest import REVIEW, wait_until_finished


def test_successful_run_records_steps_results_and_files(make_manager, tmp_path):
    manager = make_manager()
    run_id = manager.start("  palindrome checker  ")
    run = wait_until_finished(manager, run_id)

    assert run["status"] == "done"
    assert run["topic"] == "palindrome checker"
    assert [run["steps"][s]["status"] for s in ("research", "coding", "review")] == ["done"] * 3
    assert run["steps"]["research"]["activity"][0]["text"] == "Using search: palindrome checker"
    assert run["results"]["research"] == "## Summary\nnotes"
    assert run["results"]["code"] == "print('hi')\n"
    assert run["results"]["review"] == REVIEW

    folder = tmp_path / run_id
    assert (folder / "main.py").read_text() == "print('hi')\n"
    assert json.loads((folder / "review.json").read_text()) == REVIEW
    assert json.loads((folder / "run.json").read_text())["status"] == "done"
    assert manager.current_run_id is None


def test_event_log_is_ordered_and_complete(make_manager):
    manager = make_manager()
    run_id = manager.start("x")
    wait_until_finished(manager, run_id)
    events = manager.events_since(run_id, 0)
    assert [e["seq"] for e in events] == list(range(len(events)))
    assert events[0]["type"] == "run_started"
    assert events[-1]["type"] == "run_completed"


def test_runner_exception_fails_running_step(make_manager):
    def runner(topic, manager):
        manager.step_started("research")
        manager.step_completed("research", "notes")
        manager.step_started("coding")
        raise ConnectionError("ollama down")

    manager = make_manager(runner)
    run = wait_until_finished(manager, manager.start("x"))
    assert run["status"] == "failed"
    assert "ollama down" in run["error"]
    assert run["steps"]["coding"]["status"] == "failed"
    assert run["steps"]["review"]["status"] == "pending"


def test_out_of_order_events_do_not_move_status_backwards(make_manager):
    def runner(topic, manager):
        manager.step_completed("research", "notes")
        manager.step_started("research")  # late "started" handler

    manager = make_manager(runner)
    run = wait_until_finished(manager, manager.start("x"))
    assert run["steps"]["research"]["status"] == "done"
    assert run["status"] == "failed"  # coding/review never ran
    assert "coding" in run["error"]


def test_second_run_while_busy_is_rejected(make_manager, blocking_runner):
    manager = make_manager(blocking_runner)
    run_id = manager.start("first")
    with pytest.raises(RunBusyError):
        manager.start("second")
    blocking_runner.release.set()
    wait_until_finished(manager, run_id)
    wait_until_finished(manager, manager.start("third"))


def test_blank_topic_rejected(make_manager):
    with pytest.raises(ValueError):
        make_manager().start("   ")


def test_finished_runs_listed_from_disk(make_manager):
    first = make_manager()
    run_id = first.start("persist me")
    wait_until_finished(first, run_id)

    fresh = make_manager()  # new process, nothing in memory
    assert any(r["id"] == run_id and r["topic"] == "persist me" for r in fresh.list_runs())
    assert fresh.get(run_id)["results"]["code"] == "print('hi')\n"
