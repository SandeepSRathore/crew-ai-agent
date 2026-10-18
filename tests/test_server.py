import json

from fastapi.testclient import TestClient

from app.server import create_app
from tests.conftest import wait_until_finished


def _sse_messages(body: str) -> list[tuple[str, dict]]:
    messages = []
    for chunk in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in chunk.splitlines() if not line.startswith(":"))
        if lines:
            messages.append((lines["event"], json.loads(lines["data"])))
    return messages


def test_run_lifecycle_over_http(make_manager):
    manager = make_manager()
    client = TestClient(create_app(manager))

    resp = client.post("/api/runs", json={"topic": "fizzbuzz"})
    assert resp.status_code == 201
    run_id = resp.json()["run_id"]
    wait_until_finished(manager, run_id)

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "done"
    assert run["results"]["review"]["issues"][0]["title"] == "No input check"
    assert client.get("/api/runs").json()["runs"][0]["id"] == run_id

    messages = _sse_messages(client.get(f"/api/runs/{run_id}/events").text)
    assert messages[0][0] == "snapshot"
    assert messages[-1][0] == "end"
    assert messages[-1][1]["run"]["status"] == "done"


def test_sse_streams_live_progress(make_manager, blocking_runner):
    manager = make_manager(blocking_runner)
    client = TestClient(create_app(manager))
    run_id = client.post("/api/runs", json={"topic": "x"}).json()["run_id"]

    assert client.post("/api/runs", json={"topic": "y"}).status_code == 409

    with client.stream("GET", f"/api/runs/{run_id}/events") as resp:
        blocking_runner.release.set()
        messages = _sse_messages(resp.read().decode())
    event_types = [m[1]["event"]["type"] for m in messages if m[0] == "progress"]
    assert "step_completed" in event_types
    assert event_types[-1] == "run_completed"


def test_validation_and_not_found(make_manager):
    client = TestClient(create_app(make_manager()))
    assert client.post("/api/runs", json={"topic": "   "}).status_code == 422
    assert client.post("/api/runs", json={"topic": ""}).status_code == 422
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/runs/nope/events").status_code == 404
