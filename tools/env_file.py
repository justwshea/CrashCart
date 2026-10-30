"""Small .env reader/updater that preserves unrelated lines and comments."""

from __future__ import annotations

import re
import shlex
from pathlib import Path


_ASSIGNMENT = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=)(.*)$")


def read_env(path: Path) -> dict[str, str]:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ASSIGNMENT.match(line)
        if not match:
            continue
        raw, _ = _split_inline_comment(match.group(4))
        raw = raw.strip()
        if raw.startswith("{") and raw.endswith("}"):
            values[match.group(2)] = raw
            continue
        try:
            parts = shlex.split(raw, comments=False, posix=True)
        except ValueError:
            parts = [raw]
        values[match.group(2)] = " ".join(parts)
    return values


def update_env(path: Path, values: dict[str, str]) -> None:
    """Update selected keys while retaining all other lines verbatim."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    output = []
    for line in lines:
        match = _ASSIGNMENT.match(line)
        if not match or match.group(2) not in remaining:
            output.append(line)
            continue
        key = match.group(2)
        _, comment = _split_inline_comment(match.group(4))
        output.append(f"{match.group(1)}{key}{match.group(3)}{_format_value(key, str(remaining.pop(key)))}{comment}")
    for key, value in remaining.items():
        output.append(f"{key}={_format_value(key, str(value))}")
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def _format_value(key: str, value: str) -> str:
    if key.startswith("LLM_EXTRA_BODY_") and value.startswith("{") and value.endswith("}"):
        return value
    if re.fullmatch(r"[A-Za-z0-9_./:@+-]*", value):
        return value
    return shlex.quote(value)


def _split_inline_comment(value: str) -> tuple[str, str]:
    quote = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
        elif char == "#" and (index == 0 or value[index - 1].isspace()):
            start = index
            while start > 0 and value[start - 1].isspace():
                start -= 1
            return value[:start], value[start:]
    return value, ""
