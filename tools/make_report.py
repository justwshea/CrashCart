"""Create Markdown and chart summaries exclusively from eval/results.json."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def summarize(results: dict) -> list[dict]:
    output = []
    for system in results.get("systems", []):
        rows = [row for row in results.get("runs", [])
                if row.get("system") == system and not row.get("skipped")]
        if not rows:
            continue
        category = [row for row in rows if row.get("category_correct") is not None]
        location = [row for row in rows if row.get("location_correct") is not None]
        verified = sum(row.get("verified_citations", 0) for row in rows)
        citations = sum(row.get("total_citations", 0) for row in rows)
        output.append({
            "system": system,
            "accuracy": sum(bool(row.get("category_correct")) for row in category) / len(category) if category else None,
            "location_accuracy": sum(bool(row.get("location_correct")) for row in location) / len(location) if location else None,
            "median_time_s": statistics.median(row.get("elapsed_s", 0) for row in rows),
            "mean_cost_usd": sum(row.get("estimated_cost_usd", 0) for row in rows) / len(rows),
            "citation_validity": verified / citations if citations else None,
            "abstain_rate": sum(row.get("verdict") == "abstain" for row in rows) / len(rows),
            "n": len(rows),
        })
    return output


def _percent(value):
    return "—" if value is None else f"{value:.1%}"


def make_report(results_path: Path, markdown_path: Path, chart_path: Path) -> None:
    results = json.loads(results_path.read_text(encoding="utf-8"))
    rows = summarize(results)
    lines = ["# Evaluation Results", "", f"Mode: `{results.get('mode', 'unknown')}`", "",
             "| System | Accuracy | Location accuracy | Median time (s) | Mean cost (USD) | Citation validity | Abstain rate | N |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        lines.append(f"| {row['system']} | {_percent(row['accuracy'])} | {_percent(row['location_accuracy'])} | "
                     f"{row['median_time_s']:.2f} | ${row['mean_cost_usd']:.4f} | "
                     f"{_percent(row['citation_validity'])} | {_percent(row['abstain_rate'])} | {row['n']} |")
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    chart_path.parent.mkdir(parents=True, exist_ok=True)
    labels = [row["system"] for row in rows]
    accuracy = [row["accuracy"] or 0 for row in rows]
    location = [row["location_accuracy"] or 0 for row in rows]
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(max(7, len(labels) * 1.6), 4.5), dpi=140)
    width = .36
    ax.bar(x - width / 2, accuracy, width, label="Category accuracy", color="#4c9f70")
    ax.bar(x + width / 2, location, width, label="Location accuracy", color="#5287b5")
    ax.set_ylim(0, 1)
    ax.set_ylabel("Fraction correct")
    ax.set_xticks(x, labels)
    ax.legend(frameon=False)
    ax.grid(axis="y", alpha=.2)
    fig.tight_layout()
    fig.savefig(chart_path, metadata={"Software": "CrashCart evaluation report"})
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=Path("eval/results.json"))
    parser.add_argument("--markdown", type=Path, default=Path("eval/RESULTS.md"))
    parser.add_argument("--chart", type=Path, default=Path("eval/chart.png"))
    args = parser.parse_args()
    make_report(args.results, args.markdown, args.chart)
    print(f"Wrote {args.markdown} and {args.chart}")


if __name__ == "__main__":
    main()
