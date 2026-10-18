"""Forwards CrewAI events to the RunManager so the UI can show live progress."""

from crewai.events import (
    BaseEventListener,
    TaskCompletedEvent,
    TaskFailedEvent,
    TaskStartedEvent,
    ToolUsageErrorEvent,
    ToolUsageFinishedEvent,
    ToolUsageStartedEvent,
)

from app.runs import manager


def _step_id(event) -> str | None:
    task = getattr(event, "task", None) or getattr(event, "from_task", None)
    name = getattr(task, "name", None) or getattr(event, "task_name", None)
    if name:
        return name
    # Tool events don't always carry the task; attribute them to the step that is running.
    run = manager.get(manager.current_run_id) if manager.current_run_id else None
    return run["current_step"] if run else None


def _describe_args(args) -> str:
    if isinstance(args, dict):
        query = args.get("query") or args.get("url")
        if query:
            return str(query)
    return str(args)[:200]


class PipelineListener(BaseEventListener):
    def setup_listeners(self, crewai_event_bus):
        @crewai_event_bus.on(TaskStartedEvent)
        def on_task_started(source, event):
            if step := _step_id(event):
                manager.step_started(step)

        @crewai_event_bus.on(TaskCompletedEvent)
        def on_task_completed(source, event):
            if step := _step_id(event):
                output = event.output
                parsed = output.pydantic.model_dump() if getattr(output, "pydantic", None) else None
                manager.step_completed(step, output.raw, parsed)

        @crewai_event_bus.on(TaskFailedEvent)
        def on_task_failed(source, event):
            if step := _step_id(event):
                manager.step_failed(step, str(event.error))

        @crewai_event_bus.on(ToolUsageStartedEvent)
        def on_tool_started(source, event):
            if step := _step_id(event):
                manager.step_activity(step, f"Using {event.tool_name}: {_describe_args(event.tool_args)}")

        @crewai_event_bus.on(ToolUsageFinishedEvent)
        def on_tool_finished(source, event):
            if step := _step_id(event):
                size = len(str(event.output or ""))
                manager.step_activity(step, f"{event.tool_name} returned {size} characters")

        @crewai_event_bus.on(ToolUsageErrorEvent)
        def on_tool_error(source, event):
            if step := _step_id(event):
                manager.step_activity(step, f"{event.tool_name} failed: {event.error}")


pipeline_listener = PipelineListener()
