"""Runs the real crew: starts the DuckDuckGo MCP server, kicks off the crew and reports results."""

import contextlib
import logging
import os
import shutil
import sys
from pathlib import Path

from crewai.events import crewai_event_bus
from crewai_tools import MCPServerAdapter
from mcp import StdioServerParameters

import app.listener  # noqa: F401  (registers the event listener)
from app.config import OLLAMA_BASE_URL
from app.crew import build_crew
from app.runs import RunManager

log = logging.getLogger(__name__)


def _ddg_command() -> str:
    local = Path(sys.executable).parent / "duckduckgo-mcp-server"
    return str(local) if local.exists() else (shutil.which("duckduckgo-mcp-server") or "duckduckgo-mcp-server")


@contextlib.contextmanager
def search_tools(manager: RunManager):
    """Yield the DuckDuckGo MCP tools, or [] (with a warning in the UI) if the server can't start."""
    params = StdioServerParameters(command=_ddg_command(), args=[], env=dict(os.environ))
    try:
        adapter = MCPServerAdapter(params)
    except Exception as exc:
        log.exception("DuckDuckGo MCP server failed to start")
        manager.warn(f"Web search unavailable ({exc}); the researcher will use the model's own knowledge.")
        yield []
        return
    try:
        manager.step_activity("research", f"Connected to DuckDuckGo MCP server ({', '.join(t.name for t in adapter.tools)})")
        yield list(adapter.tools)
    finally:
        adapter.stop()


def run_pipeline(topic: str, manager: RunManager) -> None:
    with search_tools(manager) as tools:
        try:
            result = build_crew(tools).kickoff(inputs={"topic": topic})
        except ConnectionError as exc:
            # CrewAI's client reports this as an "OpenAI API" error; name the server the user actually runs.
            raise ConnectionError(f"Could not reach Ollama at {OLLAMA_BASE_URL}. Is `ollama serve` running? ({exc})") from exc
    # Event handlers run on a thread pool; let them finish, then report the authoritative outputs.
    crewai_event_bus.flush(timeout=10)
    for task_output in result.tasks_output:
        parsed = task_output.pydantic.model_dump() if task_output.pydantic else None
        manager.step_completed(task_output.name, task_output.raw, parsed)
