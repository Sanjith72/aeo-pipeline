"""
Optional LLM layer.

The crawler is deterministic-first: 7 of the 8 rubric criteria are scored
purely from parsed signals. The LLM is used only as a *second opinion* for
content depth (criterion 6) and to disqualify spurious statistics
(criterion 3). Everything here degrades gracefully — if Ollama is unreachable
or disabled, callers get ``None`` and fall back to deterministic scoring.
"""

from __future__ import annotations

from pathlib import Path

_PROMPT_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    """Read a prompt template from nlp/prompts/. Filename without extension."""
    path = _PROMPT_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8")
