from typing import Literal

from pydantic import BaseModel, Field


class ReviewIssue(BaseModel):
    severity: Literal["critical", "major", "minor"] = Field(
        description="critical = wrong result/crash/security hole, major = likely bug or missing handling, minor = style/readability"
    )
    title: str = Field(description="Short name of the problem")
    detail: str = Field(description="What is wrong and why it matters")
    line_hint: str | None = Field(default=None, description="Function name or code snippet where it occurs")
    suggestion: str = Field(description="How to fix it")


class ReviewReport(BaseModel):
    summary: str = Field(description="Two or three sentences on overall code quality")
    verdict: Literal["approve", "needs_changes"]
    issues: list[ReviewIssue] = Field(default_factory=list)
