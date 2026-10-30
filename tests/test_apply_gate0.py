import json

import pytest

from tools.apply_gate0 import Gate0ApplyError, apply_report
from tools.env_file import read_env


def passing_report():
    return {
        "models": {"reduce": "provider/ultra", "swarm": "provider/lightning",
                   "eyes": "provider/omni", "skeptic": "provider/super"},
        "role_calls": {role: {"usage": {"prompt_tokens": 2}} for role in
                       ("reduce", "swarm", "eyes", "skeptic")},
        "json_mode": {"with_response_format": {"accepted": True}},
        "reasoning": {
            "swarm:chat_template_kwargs": {"accepted": True, "reasoning_content": False, "think_tag": False},
            "reduce:enable_thinking": {"accepted": True, "reasoning_content": False, "think_tag": False},
        },
        "recommended_concurrency": 60,
        "vision": {"verified": True},
    }


def test_apply_gate0_updates_verified_settings_and_preserves_comments(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# keep this comment\nOTHER_SETTING=leave-me\nLLM_MODEL_EYES=old-model # preserve this inline comment\n")

    result = apply_report(passing_report(), env_file)

    values = read_env(env_file)
    assert values["LLM_MODEL_REDUCE"] == "provider/ultra"
    assert values["LLM_MODEL_BASELINE"] == "provider/ultra"
    assert values["LLM_MODEL_SWARM"] == "provider/lightning"
    assert values["LLM_MODEL_EYES"] == "provider/omni"
    assert values["LLM_MODEL_SKEPTIC"] == "provider/super"
    assert values["CONCURRENCY"] == "48"
    assert values["LLM_JSON_MODE"] == "true"
    assert json.loads(values["LLM_EXTRA_BODY_SWARM"]) == {
        "chat_template_kwargs": {"enable_thinking": False}
    }
    assert values["USE_EYES"] == "true"
    assert result["vision_enabled"] is True
    text = env_file.read_text()
    assert "# keep this comment" in text
    assert "# preserve this inline comment" in text
    assert "OTHER_SETTING=leave-me" in text


def test_apply_gate0_disables_eyes_when_omni_is_not_verified(tmp_path):
    report = passing_report()
    report["models"]["eyes"] = ""
    report["role_calls"]["eyes"] = {"skipped": "no model configured"}
    report["vision"] = {"accepted": False, "verified": False}
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_MODEL_EYES=\nUSE_EYES=true\n")

    result = apply_report(report, env_file)

    assert read_env(env_file)["USE_EYES"] == "false"
    assert result["vision_enabled"] is False


def test_apply_gate0_fails_clearly_when_reduce_failed(tmp_path):
    report = passing_report()
    report["role_calls"]["reduce"] = {"error": "APIStatusError", "status_code": 401}
    env_file = tmp_path / ".env"
    env_file.write_text("# unchanged on failure\n")

    with pytest.raises(Gate0ApplyError, match="reduce"):
        apply_report(report, env_file)

    assert env_file.read_text() == "# unchanged on failure\n"
