"""Conservative token-cap based estimate for the planned evaluation systems."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings
from app.ingest import chunks_for_bundle, load_bundle


def estimate(incident_ids: list[str], incidents_dir: Path) -> tuple[float, bool]:
    total = 0.0
    image_usage_omitted = False
    scan_prompt = len((Path(__file__).resolve().parents[1] / "app/prompts/scan.txt").read_text()) // 4
    reduce_prompt = len((Path(__file__).resolve().parents[1] / "app/prompts/reduce.txt").read_text()) // 4
    baseline_prompt = len((Path(__file__).resolve().parents[1] / "app/prompts/tail_baseline.txt").read_text()) // 4
    eyes_prompt = len((Path(__file__).resolve().parents[1] / "app/prompts/dash.txt").read_text()) // 4 + 20

    def cost(prompt: int, completion: int, input_price: float, output_price: float) -> float:
        return prompt * input_price / 1_000_000 + completion * output_price / 1_000_000

    for incident_id in incident_ids:
        bundle = load_bundle(incidents_dir / incident_id)
        chunks = chunks_for_bundle(bundle)
        image_usage_omitted |= any(item.kind == "dashboard" for item in bundle.files)
        # Bound normal and malformed-JSON retry attempts by the configured output caps.
        swarm_input = sum((len(chunk.text) // 4 + scan_prompt) * 2 for chunk in chunks)
        swarm_output = len(chunks) * 2 * 700
        swarm_cost = cost(swarm_input, swarm_output, settings.price_swarm_input, settings.price_swarm_output)

        rank0 = bundle.by_path.get("logs/rank_000.log")
        tail_input = baseline_prompt + (sum(len(line) for line in rank0.lines[-200:]) // 4 if rank0 else 0)
        baseline_cost = cost(tail_input, 600, settings.price_reduce_input, settings.price_reduce_output)

        # Reduce can make a citation repair call. Include the prompt budget twice and both output caps.
        reduce_input = 2 * (reduce_prompt + 3000)
        reduce_output = 2 * 4000
        reduce_cost = cost(reduce_input, reduce_output, settings.price_reduce_input, settings.price_reduce_output)
        # The final dissent call uses at most the configured max_tokens value.
        skeptic_cost = cost(4000, 400, settings.price_skeptic_input, settings.price_skeptic_output)
        vision_cost = cost(eyes_prompt, 600, settings.price_eyes_input, settings.price_eyes_output) \
            if any(item.kind == "dashboard" for item in bundle.files) else 0
        pipeline_cost = swarm_cost + baseline_cost + reduce_cost + skeptic_cost + vision_cost
        total += 2 * pipeline_cost + baseline_cost  # crashcart, no_eyes, and the tail-only baseline

    return total, image_usage_omitted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--incidents", default="all")
    parser.add_argument("--incidents-dir", type=Path, default=Path(settings.incidents_dir))
    args = parser.parse_args()
    if args.incidents == "all":
        ids = sorted(path.name for path in args.incidents_dir.iterdir()
                     if path.is_dir() and (path / "truth.json").is_file())
    else:
        ids = [value.strip() for value in args.incidents.split(",") if value.strip()]
    amount, image_omitted = estimate(ids, args.incidents_dir)
    note = " (image-token charges excluded)" if image_omitted else ""
    print(f"Estimated maximum text-token cost: ${amount:.4f}{note}")


if __name__ == "__main__":
    main()
