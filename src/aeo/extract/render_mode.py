"""
Detect heavy JS-rendering. A page where the initial HTML body has far less
text than the rendered body is JS-dependent — AI crawlers without JS
execution will miss its content.

Heuristic: parse a 'noscript-stripped' shallow tree from raw HTML and
compare its text length to the rendered text length.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from ..settings import load_yaml_file
from ..utils.html import body_text

_SCRIPT_RE = re.compile(r"<script[\s\S]*?</script>", re.IGNORECASE)
_STYLE_RE = re.compile(r"<style[\s\S]*?</style>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def extract(html: str, soup: BeautifulSoup, url: str) -> dict[str, Any]:
    cfg = load_yaml_file("extractors.yaml").get("render_mode", {})
    ratio_threshold = float(cfg.get("js_inflation_ratio", 3.0))
    min_initial = int(cfg.get("min_initial_text_chars", 200))

    # Approximate "initial HTML text" by stripping script/style and tags
    stripped = _SCRIPT_RE.sub("", html or "")
    stripped = _STYLE_RE.sub("", stripped)
    initial_text = _TAG_RE.sub(" ", stripped)
    initial_len = len(re.sub(r"\s+", " ", initial_text).strip())

    rendered_text = body_text(soup)
    rendered_len = len(rendered_text)

    inflation = rendered_len / max(initial_len, 1)
    js_only = initial_len < min_initial and rendered_len > min_initial
    js_dependent = inflation >= ratio_threshold or js_only

    return {
        "initial_text_chars": initial_len,
        "rendered_text_chars": rendered_len,
        "inflation_ratio": round(inflation, 2),
        "js_only_content": js_only,
        "js_dependent": js_dependent,
    }
