"""Exact, whitespace-normalized source citation verification."""

from __future__ import annotations

import re

from app.ingest import Bundle


def _normalize(text: str) -> str:
    return " ".join(text.split())


def verify_quote(bundle: Bundle, file: str, line_start: int, line_end: int, quote: str) -> bool:
    if not isinstance(quote, str) or not quote or len(quote) > 200:
        return False
    if line_start < 1 or line_end < line_start:
        return False
    source = bundle.by_path.get(file)
    if source is None or line_end > len(source.lines):
        return False
    selected = _normalize(" ".join(source.lines[line_start - 1:line_end]))
    return _normalize(quote) in selected
