"""Live Token Factory/provider gate smoke test. Run manually with a live API key."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from openai import AsyncOpenAI


ROOT = Path(__file__).resolve().parents[1]


def load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _friendly_provider_failure(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    detail = str(exc).lower()
    if status == 401:
        return "Token Factory rejected the API key (401). Check .env, paste a valid key, and rerun ./run.sh."
    if status == 402 or any(term in detail for term in ("quota exceeded", "insufficient credit", "no credits", "credit balance")):
        return "Token Factory reports no credits or an exhausted quota. Add credits or raise the quota, then rerun ./run.sh."
    if status == 429:
        return "Token Factory rate limited the Gate 0 check. Wait, lower CONCURRENCY in .env if needed, and rerun ./run.sh."
    return "Gate 0 could not reach the configured Token Factory model service. Check the base URL and retry."


async def main() -> None:
    load_dotenv()
    if os.getenv("LLM_MODE", "live").lower() != "live":
        raise SystemExit("Set LLM_MODE=live in .env before running this live-only script.")
    api_key = os.getenv("LLM_API_KEY") or os.getenv("NEBIUS_API_KEY")
    if not api_key:
        raise SystemExit("Set LLM_API_KEY (or NEBIUS_API_KEY) in .env first.")
    base_url = os.getenv("LLM_BASE_URL", "https://api.tokenfactory.nebius.com/v1/")
    model_vars = {
        "reduce": "LLM_MODEL_REDUCE", "baseline": "LLM_MODEL_BASELINE",
        "swarm": "LLM_MODEL_SWARM", "eyes": "LLM_MODEL_EYES", "skeptic": "LLM_MODEL_SKEPTIC",
    }
    configured_models = {role: os.getenv(var, "") for role, var in model_vars.items()}
    print(json.dumps({"base_url": base_url, "models": configured_models}, indent=2))
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    report: dict = {"base_url": base_url, "models": configured_models, "model_list": [], "role_calls": {}, "json_mode": {}, "reasoning": {}, "concurrency": {}, "vision": {}}

    listed = await client.models.list()
    ids = [item.id for item in listed.data]
    report["model_list"] = ids
    print("Models:", json.dumps(ids))
    suggestions = {}
    for needle in ("ultra", "lightning", "omni", "super"):
        matches = [item for item in ids if needle in item.lower()]
        suggestions[needle] = matches[0] if matches else ""
        print(f"Suggested {needle} model:", matches[:5])
    generated = {
        "LLM_MODEL_REDUCE": suggestions["ultra"],
        "LLM_MODEL_BASELINE": suggestions["ultra"],
        "LLM_MODEL_SWARM": suggestions["lightning"],
        "LLM_MODEL_EYES": suggestions["omni"],
        "LLM_MODEL_SKEPTIC": suggestions["super"],
    }
    (ROOT / ".env.generated").write_text(
        "# Suggested provider model IDs from live GET /v1/models; review before copying.\n"
        + "\n".join(f"{key}={value}" for key, value in generated.items()) + "\n"
    )

    models = {
        "reduce": suggestions["ultra"] or configured_models["reduce"],
        "baseline": suggestions["ultra"] or configured_models["baseline"],
        "swarm": suggestions["lightning"] or configured_models["swarm"],
        "eyes": suggestions["omni"] or configured_models["eyes"],
        "skeptic": suggestions["super"] or configured_models["skeptic"],
    }
    report["models"] = models
    print("Models selected for verification:", json.dumps(models))

    for role, model in models.items():
        if not model:
            report["role_calls"][role] = {"skipped": "no model configured"}
            continue
        start = time.perf_counter()
        try:
            response = await client.chat.completions.create(model=model, messages=[{"role": "user", "content": "Reply OK."}], max_tokens=16)
            report["role_calls"][role] = {"latency_s": time.perf_counter() - start, "usage": response.usage.model_dump() if response.usage else None}
        except Exception as exc:
            report["role_calls"][role] = {"error": type(exc).__name__, "status_code": getattr(exc, "status_code", None)}
    for with_json in (False, True):
        try:
            args = {"model": models["swarm"], "messages": [{"role": "user", "content": "Return {\\\"ok\\\":true}."}], "max_tokens": 16}
            if with_json:
                args["response_format"] = {"type": "json_object"}
            response = await client.chat.completions.create(**args)
            report["json_mode"]["with_response_format" if with_json else "without_response_format"] = {"accepted": True, "content": response.choices[0].message.content}
        except Exception as exc:
            report["json_mode"]["with_response_format" if with_json else "without_response_format"] = {"accepted": False, "error": type(exc).__name__, "status_code": getattr(exc, "status_code", None)}
    for role in ("swarm", "reduce"):
        for label, extra in (("none", {}), ("chat_template_kwargs", {"chat_template_kwargs": {"enable_thinking": False}}), ("enable_thinking", {"enable_thinking": False})):
            try:
                response = await client.chat.completions.create(model=models[role], messages=[{"role": "user", "content": "Reply OK."}], max_tokens=16, extra_body=extra or None)
                message = response.choices[0].message
                content = message.content or ""
                report["reasoning"][f"{role}:{label}"] = {"accepted": True, "reasoning_content": bool(getattr(message, "reasoning_content", None)), "think_tag": "<think>" in content}
            except Exception as exc:
                report["reasoning"][f"{role}:{label}"] = {"accepted": False, "error": type(exc).__name__, "status_code": getattr(exc, "status_code", None)}
    for concurrency in (8, 16, 24, 32, 48):
        latencies: list[float] = []
        statuses: list[int | None] = []
        async def tiny_call() -> None:
            start = time.perf_counter()
            try:
                await client.chat.completions.create(model=models["swarm"], messages=[{"role": "user", "content": "OK"}], max_tokens=4)
                statuses.append(None)
            except Exception as exc:
                statuses.append(getattr(exc, "status_code", None))
            latencies.append(time.perf_counter() - start)
        await asyncio.gather(*(tiny_call() for _ in range(concurrency)))
        ordered = sorted(latencies)
        percentile = lambda p: ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))] if ordered else None
        report["concurrency"][str(concurrency)] = {"success_rate": statuses.count(None) / concurrency, "rate_limits": statuses.count(429), "p50_s": percentile(.50), "p95_s": percentile(.95)}
    safe_concurrency = [int(value) for value, result in report["concurrency"].items()
                        if result["success_rate"] >= 0.95 and result["rate_limits"] == 0]
    report["recommended_concurrency"] = max(safe_concurrency) if safe_concurrency else 4
    fig, ax = plt.subplots(figsize=(2, 1))
    ax.text(.5, .5, "RANK 13", ha="center", va="center", fontsize=20)
    ax.axis("off")
    png = ROOT / "rank13_smoke.png"
    fig.savefig(png, format="png", bbox_inches="tight")
    plt.close(fig)
    encoded = base64.b64encode(png.read_bytes()).decode("ascii")
    try:
        response = await client.chat.completions.create(model=models["eyes"], max_tokens=32, messages=[{"role": "user", "content": [{"type": "text", "text": "What text is visible?"}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}]}])
        vision_text = response.choices[0].message.content or ""
        report["vision"] = {"accepted": True, "verified": "RANK 13" in vision_text.upper(),
                            "response": vision_text}
    except Exception as exc:
        report["vision"] = {"accepted": False, "verified": False,
                            "error": type(exc).__name__, "status_code": getattr(exc, "status_code", None)}
    png.unlink(missing_ok=True)
    output = ROOT / "gate0_report.json"
    output.write_text(json.dumps(report, indent=2) + "\n")
    print("Gate 0 summary:", json.dumps({"base_url": base_url, "models": models,
          "role_calls": report["role_calls"], "json_mode": report["json_mode"],
          "recommended_concurrency": report["recommended_concurrency"], "vision": report["vision"]}, indent=2))
    print(f"Full report: {output}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(_friendly_provider_failure(exc), file=sys.stderr)
        raise SystemExit(1)
