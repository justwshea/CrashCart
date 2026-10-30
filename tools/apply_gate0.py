"""Apply settings that passed the human-run Gate 0 smoke test."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.env_file import update_env


ROOT = Path(__file__).resolve().parents[1]
ROLE_ENV = {
    "reduce": "LLM_MODEL_REDUCE",
    "swarm": "LLM_MODEL_SWARM",
    "eyes": "LLM_MODEL_EYES",
    "skeptic": "LLM_MODEL_SKEPTIC",
}
_REASONING_TRIALS = (
    ("chat_template_kwargs", {"chat_template_kwargs": {"enable_thinking": False}}),
    ("enable_thinking", {"enable_thinking": False}),
)


class Gate0ApplyError(ValueError):
    pass


def _role_passed(report: dict, role: str) -> bool:
    result = report.get("role_calls", {}).get(role, {})
    return bool(report.get("models", {}).get(role)) and not result.get("error") and not result.get("skipped")


def _reasoning_body(report: dict, role: str) -> str:
    trials = report.get("reasoning", {})
    for label, body in _REASONING_TRIALS:
        result = trials.get(f"{role}:{label}", {})
        if result.get("accepted") and not result.get("reasoning_content") and not result.get("think_tag"):
            return json.dumps(body, separators=(",", ":"))
    return ""


def _recommended_concurrency(report: dict) -> int:
    recommended = report.get("recommended_concurrency")
    if recommended is None:
        data = report.get("concurrency", {})
        candidates = []
        for value, result in data.items():
            try:
                amount = int(value)
            except (TypeError, ValueError):
                continue
            if result.get("success_rate", 0) >= 0.95 and result.get("rate_limits", 0) == 0:
                candidates.append(amount)
        recommended = max(candidates) if candidates else 4
    try:
        value = int(recommended)
    except (TypeError, ValueError):
        value = 4
    return max(4, min(48, value))


def apply_report(report: dict, env_path: Path) -> dict:
    failed = [role for role in ("swarm", "reduce") if not _role_passed(report, role)]
    if failed:
        statuses = {report.get("role_calls", {}).get(role, {}).get("status_code") for role in failed}
        roles = " and ".join(failed)
        if 401 in statuses:
            raise Gate0ApplyError(f"Token Factory rejected the API key (401) while verifying {roles}. Check .env, paste a valid key, and rerun ./run.sh.")
        if 402 in statuses:
            raise Gate0ApplyError(f"Token Factory reports no credits or an exhausted quota while verifying {roles}. Add credits or raise the quota, then rerun ./run.sh.")
        if 429 in statuses:
            raise Gate0ApplyError(f"Token Factory rate limited the Gate 0 calls for {roles}. Wait, lower CONCURRENCY in .env if needed, and rerun ./run.sh.")
        raise Gate0ApplyError(
            f"Gate 0 verification failed for {roles}; check the model ID, API key, and Token Factory access, then rerun tools/smoke_test.py."
        )

    values = {}
    models = report.get("models", {})
    for role, key in ROLE_ENV.items():
        if _role_passed(report, role):
            values[key] = str(models[role])
    if "LLM_MODEL_REDUCE" in values:
        values["LLM_MODEL_BASELINE"] = values["LLM_MODEL_REDUCE"]

    json_test = report.get("json_mode", {}).get("with_response_format", {})
    values["LLM_JSON_MODE"] = "true" if json_test.get("accepted") else "false"
    values["CONCURRENCY"] = str(_recommended_concurrency(report))
    values["LLM_EXTRA_BODY_SWARM"] = _reasoning_body(report, "swarm")
    values["LLM_EXTRA_BODY_REDUCE"] = _reasoning_body(report, "reduce")

    eyes_ok = (_role_passed(report, "eyes")
               and bool(report.get("vision", {}).get("verified")))
    values["USE_EYES"] = "true" if eyes_ok else "false"
    update_env(env_path, values)
    return {"vision_enabled": eyes_ok, "values": values}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=ROOT / "gate0_report.json")
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        outcome = apply_report(report, args.env)
    except (OSError, json.JSONDecodeError, Gate0ApplyError) as exc:
        print(f"Could not apply Gate 0 settings: {exc}", file=sys.stderr)
        return 1
    if not outcome["vision_enabled"]:
        print("Warning: no vision-capable model passed Gate 0; dashboard reading is disabled (USE_EYES=false).")
    print(f"Applied verified Gate 0 settings to {args.env}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
