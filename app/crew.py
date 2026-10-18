from crewai import Agent, Crew, Process, Task

from app.config import get_llm
from app.models import ReviewReport

# Step ids double as task names so event handlers can map a task back to its step.
STEPS = [
    {"id": "research", "agent": "Researcher", "label": "Research"},
    {"id": "coding", "agent": "Coder", "label": "Coding"},
    {"id": "review", "agent": "Reviewer", "label": "Review"},
]


def build_crew(search_tools: list | None = None) -> Crew:
    """Build the sequential Research -> Coding -> Review crew. Kick off with inputs={"topic": ...}."""
    llm = get_llm()
    search_tools = search_tools or []

    researcher = Agent(
        role="Researcher",
        goal="Find accurate, practical information that a Python developer needs to implement: {topic}",
        backstory=(
            "You are a careful technical researcher. You search the web, read the most relevant "
            "results and boil them down into short, concrete notes a programmer can act on."
        ),
        tools=search_tools,
        llm=llm,
        max_iter=5,
        allow_delegation=False,
        verbose=True,
    )
    coder = Agent(
        role="Coder",
        goal="Write clean, correct, runnable Python 3 code for: {topic}",
        backstory=(
            "You are a senior Python developer. You write small, self-contained programs that use "
            "the standard library where possible, with clear names, docstrings and error handling."
        ),
        llm=llm,
        max_iter=3,
        allow_delegation=False,
        verbose=True,
    )
    reviewer = Agent(
        role="Reviewer",
        goal="Find every real problem in the submitted Python code and explain how to fix it",
        backstory=(
            "You are a strict code reviewer. You look for bugs, unhandled edge cases, bad input "
            "handling, security issues, performance problems and readability issues. You never "
            "invent problems that are not in the code."
        ),
        llm=llm,
        max_iter=3,
        allow_delegation=False,
        verbose=True,
    )

    search_hint = (
        "Use the search tool (1 to 3 searches) to find relevant information. "
        if search_tools
        else "Web search is unavailable, so rely on your own knowledge. "
    )
    research = Task(
        name="research",
        description=(
            "The user wants this:\n\n{topic}\n\n"
            + search_hint
            + "Then write research notes for the developer who will implement it in Python."
        ),
        expected_output=(
            "Markdown notes with these sections: '## Summary' (what to build), '## Key concepts', "
            "'## Suggested approach' (numbered steps), '## Useful Python libraries', "
            "'## Sources' (URLs you used, if any)."
        ),
        agent=researcher,
    )
    coding = Task(
        name="coding",
        description=(
            "Using the research notes, write a Python 3 program for:\n\n{topic}\n\n"
            "Rules: one self-contained file; prefer the standard library; include docstrings, input "
            "validation and error handling; finish with an `if __name__ == \"__main__\":` block that "
            "demonstrates the code with sample data."
        ),
        expected_output="The complete program inside a single ```python code block, followed by one short paragraph explaining it.",
        agent=coder,
        context=[research],
    )
    review = Task(
        name="review",
        description=(
            "Review the Python code written for this request:\n\n{topic}\n\n"
            "Check correctness, edge cases, error handling, security, performance and readability. "
            "List each problem you find as a separate issue with a severity "
            "(critical, major or minor), a short title, details, where it occurs and a suggested fix. "
            "Set verdict to 'approve' only if there are no critical or major issues."
        ),
        expected_output="A review report with a summary, a verdict and the list of issues found.",
        agent=reviewer,
        context=[coding],
        output_pydantic=ReviewReport,
    )

    return Crew(
        agents=[researcher, coder, reviewer],
        tasks=[research, coding, review],
        process=Process.sequential,
        verbose=True,
    )
