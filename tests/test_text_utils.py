from app.runs import parse_review
from app.text_utils import extract_python, strip_think


def test_strip_think_removes_blocks():
    assert strip_think("<think>hmm\nlong</think>\nAnswer") == "Answer"


def test_strip_think_removes_unterminated_block():
    assert strip_think("Answer <think>never closed") == "Answer"


def test_strip_think_handles_none():
    assert strip_think(None) == ""


def test_extract_python_prefers_longest_python_block():
    text = "<think>```python\nnope()\n```</think>Here:\n```bash\nls\n```\n```python\nprint(1)\nprint(2)\n```\n```python\nx=1\n```"
    assert extract_python(text) == "print(1)\nprint(2)\n"


def test_extract_python_falls_back_to_any_fence():
    assert extract_python("```\nprint('hi')\n```") == "print('hi')\n"


def test_extract_python_unfenced_returns_text():
    assert extract_python("print('hi')") == "print('hi')\n"


def test_parse_review_from_raw_json_with_think():
    raw = '<think>x</think>Sure: {"summary": "ok", "verdict": "needs_changes", "issues": [{"severity": "major", "title": "t", "detail": "d", "suggestion": "s"}]}'
    review = parse_review(raw)
    assert review["verdict"] == "needs_changes"
    assert review["issues"][0]["line_hint"] is None


def test_parse_review_returns_none_for_prose():
    assert parse_review("The code looks fine.") is None
