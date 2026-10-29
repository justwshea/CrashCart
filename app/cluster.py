"""Normalize and group grounded findings into deterministic incident clusters."""

from __future__ import annotations

import hashlib
import re
from typing import Optional

from app.ingest import Bundle


_HEX = re.compile(r"\b0x[0-9a-f]+\b", re.IGNORECASE)
_IDENTIFIER = re.compile(r"\b(?:rank|node)\s*[-_:=#]?\s*\d+\b", re.IGNORECASE)
_DIGITS = re.compile(r"\d+")


def normalize_quote(quote: str) -> str:
    value = " ".join((quote or "").split()).lower()
    value = _IDENTIFIER.sub(" ", value)
    value = _HEX.sub("0x#", value)
    return " ".join(_DIGITS.sub("#", value).split())


def _finding_ts(finding: dict, bundle: Optional[Bundle]) -> Optional[str]:
    value = finding.get("timestamp") or finding.get("ts")
    if value:
        return str(value)
    if bundle is not None:
        source = bundle.by_path.get(str(finding.get("file", "")))
        line = finding.get("line_start")
        if source is not None and isinstance(line, int) and 1 <= line <= len(source.timestamps):
            return source.timestamps[line - 1]
    return None


def _earliest_key(finding: dict, bundle: Optional[Bundle]):
    return (_finding_ts(finding, bundle) or "9999", str(finding.get("file", "")),
            int(finding.get("line_start", 0) or 0))


def cluster_findings(findings: list[dict], bundle: Optional[Bundle] = None, limit: int = 80) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for finding in findings:
        key = (str(finding.get("kind", "unknown")), normalize_quote(str(finding.get("quote", ""))))
        groups.setdefault(key, []).append(finding)

    clusters = []
    for (kind, normalized), items in groups.items():
        ordered = sorted(items, key=lambda item: _earliest_key(item, bundle))
        timestamps = [ts for item in items if (ts := _finding_ts(item, bundle))]
        files = sorted({str(item.get("file", "")) for item in items if item.get("file")})
        exemplar_items = ordered[:3]
        cluster_id = hashlib.sha1(f"{kind}:{normalized}".encode("utf-8")).hexdigest()[:12]
        clusters.append({
            "id": cluster_id,
            "kind": kind,
            "key": normalized,
            "count": len(items),
            "files": files[:10],
            "files_total": len(files),
            "first_ts": min(timestamps) if timestamps else None,
            "last_ts": max(timestamps) if timestamps else None,
            "severity": max(int(item.get("severity", 1) or 1) for item in items),
            "exemplars": [dict(item) for item in exemplar_items],
        })

    clusters.sort(key=lambda item: (-item["severity"], item["first_ts"] or "9999", item["id"]))
    if len(clusters) <= limit:
        return clusters
    selected = clusters[:limit]
    selected_ids = {item["id"] for item in selected}
    protected = [item for item in clusters[limit:]
                 if item["count"] == 1 and item["severity"] >= 4 and item["id"] not in selected_ids]
    selected.extend(protected)
    selected.sort(key=lambda item: (-item["severity"], item["first_ts"] or "9999", item["id"]))
    return selected
