import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = os.getenv("MODEL", "ollama/qwen3:1.7b")
OUTPUTS_DIR = Path(os.getenv("OUTPUTS_DIR", Path(__file__).resolve().parent.parent / "outputs"))


def ollama_model_name() -> str:
    """Model name as Ollama knows it, e.g. 'qwen3:1.7b'."""
    return MODEL.removeprefix("ollama/")


def get_llm():
    from crewai import LLM

    return LLM(model=MODEL, base_url=OLLAMA_BASE_URL, temperature=0.3)
