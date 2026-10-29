import json
from tools.make_dashboard import SAMPLE_SECONDS, telemetry

from tools.make_incident import FAULTS, make_incident

ROOT_MARKERS = {
    "xid79_demo": "Xid (PCI:0000:8a:00): 79",
    "ecc_dbe": "Xid 48: double-bit ECC",
    "ib_flap": "Link down",
    "oom_one_rank": "torch.OutOfMemoryError",
    "nan_divergence": "gnorm inf",
    "fs_stall": "not responding",
    "nccl_version_mismatch": "NCCL version 2.18.3",
    "dataloader_oom_kill": "Killed process 9931",
    "preemption": "preempted by higher priority job",
    "illegal_access": "illegal memory access",
}


def test_generator_is_deterministic_and_truth_line_exists(tmp_path):
    first = make_incident("xid79_demo", tmp_path / "one", 7)
    second = make_incident("xid79_demo", tmp_path / "two", 7)
    truth = json.loads((first / "truth.json").read_text())
    source = first / truth["evidence_file"]
    lines = source.read_text().splitlines()
    assert truth["category"] == "gpu_fell_off_bus"
    assert "Xid" in lines[truth["evidence_line"] - 1]
    for generated in first.rglob("*"):
        if generated.is_file():
            assert generated.read_bytes() == (second / generated.relative_to(first)).read_bytes()


def test_every_fault_has_real_evidence_and_expected_bundle_size(tmp_path):
    for fault_id in FAULTS:
        bundle = make_incident(fault_id, tmp_path, 7)
        truth = json.loads((bundle / "truth.json").read_text())
        source = bundle / truth["evidence_file"]
        assert source.is_file(), fault_id
        if truth["needs_dashboard"]:
            assert truth["evidence_file"] == "dashboard.png"
            assert truth["evidence_line"] is None
        else:
            lines = source.read_text().splitlines()
            assert 1 <= truth["evidence_line"] <= len(lines), fault_id
            assert ROOT_MARKERS[fault_id] in lines[truth["evidence_line"] - 1], fault_id
        chars = sum(path.stat().st_size for path in bundle.rglob("*")
                    if path.is_file() and path.suffix.lower() != ".png")
        estimated_tokens = chars // 4
        assert 700_000 <= estimated_tokens <= 900_000, (fault_id, estimated_tokens)


def test_dashboard_applies_faults_at_t_f_plus_120_seconds():
    times, util, clock, power, temp, sampled_util, sampled_clock, sampled_power = telemetry(
        "xid79_demo", 1, 5
    )
    sample_index = list(times).index(SAMPLE_SECONDS)
    assert times[sample_index] == 120
    assert util[sample_index, 13] == sampled_util[1, 5] == 0
    assert clock[sample_index, 13] == sampled_clock[1, 5] == 0

    _, thermal_util, thermal_clock, _, thermal_temp, _, thermal_sample_clock, _ = telemetry(
        "thermal_throttle", 2, 3
    )
    assert thermal_temp[2, 3] > 90
    assert thermal_clock[sample_index, 19] == thermal_sample_clock[2, 3] < 700
    assert thermal_util[sample_index, 19] < 100

    _, _, _, cap_power, cap_temp, _, _, cap_sample_power = telemetry("power_cap", 0, 1)
    assert cap_temp[0, 1] < 90
    assert cap_power[sample_index, 1] == cap_sample_power[0, 1] == 160
