import re

_THINK_RE = re.compile(r"<think>.*?(</think>|$)", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```[ \t]*(\w+)?[ \t]*\n(.*?)```", re.DOTALL)


def strip_think(text: str | None) -> str:
    """Remove qwen3 <think>...</think> reasoning blocks (including an unterminated one)."""
    if not text:
        return ""
    return _THINK_RE.sub("", text).strip()


def extract_python(text: str | None) -> str:
    """Return the Python source from an LLM answer.

    Prefers the longest ```python fenced block, then the longest fenced block of any
    language, then the whole (think-stripped) text.
    """
    cleaned = strip_think(text)
    blocks = _FENCE_RE.findall(cleaned)
    python_blocks = [body for lang, body in blocks if (lang or "").lower() in ("python", "py")]
    candidates = python_blocks or [body for _, body in blocks]
    if candidates:
        return max(candidates, key=len).strip() + "\n"
    return cleaned + "\n" if cleaned else ""
