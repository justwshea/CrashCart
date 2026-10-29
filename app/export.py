"""Write verified postmortem and dry-run runbook markdown exports."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


_ALWAYS_REJECT = re.compile(r"\brm\s+-rf\b|\bmkfs(?:\.|\s)|\bdd\s+if=|\bshutdown\b|\bkill\s+-9\b", re.I)
_REBOOT = re.compile(r"\breboot\b", re.I)


def safe_runbook_steps(result: dict) -> list[dict]:
    safe = []
    for item in result.get("runbook", []):
        if not isinstance(item, dict):
            continue
        command = str(item.get("command", "")).strip()
        suggestion = bool(item.get("suggestion")) or command.lower().startswith("suggestion:")
        if not command or _ALWAYS_REJECT.search(command):
            continue
        if _REBOOT.search(command) and not suggestion:
            continue
        safe.append(item)
    return safe


def render_exports(result: dict, narrative: str = "") -> tuple[str, str]:
    root = result.get("root_cause") or {}
    evidence = result.get("evidence") or []
    runbook = safe_runbook_steps(result)
    postmortem = ["# CrashCart Postmortem", "", "## Summary", "",
                  narrative or root.get("summary", "No verified diagnosis."), "",
                  f"**Verdict:** {result.get('verdict', 'abstain')}", "",
                  f"**Root cause:** {root.get('summary', 'Unknown')} ({root.get('category', 'other')})", "",
                  "## Timeline", ""]
    for item in result.get("timeline", []):
        postmortem.append(f"- {item.get('ts', '')}: {item.get('event', '')}")
    postmortem += ["", "## Evidence", "", "| ID | Source | Quote | Supports |", "|---|---|---|---|"]
    for item in evidence:
        source = item.get("file", "")
        if item.get("line_start"):
            source += f":L{item['line_start']}"
            if item.get("line_end") != item.get("line_start"):
                source += f"-L{item.get('line_end')}"
        elif item.get("observation_id"):
            source += f" ({item['observation_id']})"
        quote = str(item.get("quote", "")).replace("|", "\\|")
        postmortem.append(f"| {item.get('id', '')} | `{source}` | {quote} | {item.get('supports', '')} |")
    postmortem += ["", "## Ruled out", ""]
    postmortem.extend(f"- {item.get('hypothesis', '')}: {item.get('why', '')}"
                      for item in result.get("ruled_out", []))
    postmortem += ["", "## Runbook", ""]
    postmortem.extend(f"{index}. {step.get('purpose', '')}: {step.get('command', '')}"
                      for index, step in enumerate(runbook, 1))

    runbook_lines = ["# Dry-run runbook", ""]
    for index, step in enumerate(runbook, 1):
        runbook_lines += [f"## Step {index}: {step.get('purpose', '')}", "",
                          "**DRY-RUN, review before running.**", "", "```sh",
                          str(step.get("command", "")), "```", ""]
    if not runbook:
        runbook_lines.append("No safe runbook commands were provided.")
    return "\n".join(postmortem).rstrip() + "\n", "\n".join(runbook_lines).rstrip() + "\n"


def write_exports(directory: str | Path, result: dict, narrative: str = "") -> tuple[Path, Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    postmortem_text, runbook_text = render_exports(result, narrative)
    postmortem = directory / "postmortem.md"
    runbook = directory / "runbook.md"
    postmortem.write_text(postmortem_text, encoding="utf-8")
    runbook.write_text(runbook_text, encoding="utf-8")
    return postmortem, runbook
