"""Generate deterministic synthetic multi-node GPU training incidents."""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.make_dashboard import render_dashboard


FAULTS = {
    "xid79_demo": ("gpu_fell_off_bus", False),
    "ecc_dbe": ("ecc_error", False),
    "ib_flap": ("ib_link_failure", False),
    "oom_one_rank": ("cuda_oom", False),
    "nan_divergence": ("nan_divergence", False),
    "thermal_throttle": ("thermal_throttle", True),
    "power_cap": ("power_cap", True),
    "fs_stall": ("filesystem_stall", False),
    "nccl_version_mismatch": ("nccl_version_mismatch", False),
    "dataloader_oom_kill": ("dataloader_oom_kill", False),
    "preemption": ("preemption", False),
    "illegal_access": ("illegal_memory_access", False),
}
BASE_TIME = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
FAULT_SECOND = 3190  # step 290 at 11 seconds per step


def timestamp(seconds: int) -> str:
    return (BASE_TIME + timedelta(seconds=seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")


def _rank_log(path: Path, rank: int, fault: str, faulty_rank: int, faulty_node: int,
              fault_second: int, rng: random.Random) -> None:
    lines = []
    node, gpu = divmod(rank, 8)
    for i in range(300):
        seconds = i * 11
        if seconds > fault_second:
            break
        if i % 11 == 0:
            lines.append(f"{timestamp(seconds)} INFO rank={rank} NCCL communicator initialized on node-{node:02d} gpu={gpu}")
        if fault == "nccl_version_mismatch" and node == faulty_node and i == 2:
            lines.append(f"{timestamp(seconds)} ERROR rank={rank} NCCL version 2.18.3 loaded; expected 2.20.5 after node image reconfiguration")
        if fault == "ib_flap" and node == faulty_node and i in (288, 289):
            lines.append(f"{timestamp(seconds)} WARN rank={rank} NCCL WARN NET/IB : ibv_poll_cq error 12, vendor err 129")
        if fault == "fs_stall" and node == faulty_node and i == 288:
            lines.append(f"{timestamp(seconds)} INFO rank={rank} saving checkpoint to /mnt/checkpoint/step-0288; waiting for filesystem completion")
        if fault == "preemption" and node == faulty_node and seconds == fault_second:
            lines.append(f"{timestamp(seconds)} ERROR rank={rank} Received signal 15 from scheduler; job preempted")
        if fault == "nan_divergence" and i == 288 and rank == faulty_rank:
            lines.append(f"{timestamp(seconds)} ERROR rank={rank} step {i} loss 2.6 lr 0.0002 tok/s 17320 gnorm inf mem 71.2 GB")
        if fault == "nan_divergence" and i == 289:
            lines.append(f"{timestamp(seconds)} ERROR rank={rank} FloatingPointError: loss is nan after gnorm inf")
        if seconds == fault_second and rank == faulty_rank:
            if fault == "oom_one_rank":
                lines.append(f"{timestamp(seconds)} ERROR rank={rank} torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB on device")
            elif fault == "dataloader_oom_kill":
                lines.append(f"{timestamp(seconds)} ERROR rank={rank} DataLoader worker (pid 9931) is killed by signal: Killed")
            elif fault == "illegal_access":
                lines.append(f"{timestamp(seconds)} ERROR rank={rank} CUDA error: an illegal memory access was encountered")
        if seconds == fault_second:
            break

        loss = 2.6 - i * .004 + rng.uniform(-.008, .008)
        gnorm = 1.1 + rng.uniform(-.15, .15)
        lines.append(
            f"{timestamp(seconds)} INFO rank={rank} step {i} loss {loss:.4f} lr 0.0002 tok/s 17320 "
            f"gnorm {gnorm:.3f} mem {58 + rng.random()*5:.1f} GB data_wait_ms {rng.uniform(2,18):.1f} "
            f"input_queue 3 host_mem 112.4 GB"
        )
        if i in (40, 120, 220):
            lines.append(f"{timestamp(seconds)} INFO rank={rank} checkpoint saved path=/mnt/checkpoint/step-{i:04d} shards=64 bytes=17179869184 duration_s=8.2")
        if i in (60, 180):
            lines.append(f"{timestamp(seconds)} INFO rank={rank} evaluation completed samples=4096 accuracy=0.78 duration_s=16.4 dataloader_workers=8")

    if fault == "xid79_demo" and rank == faulty_rank:
        lines.append(f"{timestamp(fault_second)} INFO rank={rank} waiting at collective after device loss")
    elif rank != faulty_rank:
        for j in range(25):
            if j == 0:
                lines.append(f"{timestamp(fault_second + 600)} ERROR ProcessGroupNCCL.cpp:800 Watchdog caught collective operation timeout: 600000 milliseconds before timing out")
            lines.append(f"{timestamp(fault_second + 600 + j)} ERROR rank={rank} Traceback frame {j}: ProcessGroupNCCL collective wait at reducer.cpp:{1200+j}")
        lines.append(f"{timestamp(fault_second + 625)} ERROR ProcessGroupNCCL communicator aborted after timeout")
    path.write_text("\n".join(lines) + "\n")


def _find_line(path: Path, needle: str) -> int:
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if needle in line:
            return number
    raise ValueError(f"evidence {needle!r} not found in {path}")


def make_incident(fault_id: str, out: Path, seed: int) -> Path:
    category, needs_dashboard = FAULTS[fault_id]
    rng = random.Random(seed + sum(map(ord, fault_id)))
    node, gpu = (1, 5) if fault_id == "xid79_demo" else (rng.randrange(8), rng.randrange(8))
    rank = node * 8 + gpu
    root = out / fault_id
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    for r in range(64):
        _rank_log(logs / f"rank_{r:03d}.log", r, fault_id, rank, node, FAULT_SECOND, rng)

    kernel_evidence = {
        "xid79_demo": "NVRM: Xid (PCI:0000:8a:00): 79, pid=4128, name=python, GPU has fallen off the bus.",
        "ecc_dbe": "NVRM: Xid 48: double-bit ECC error on framebuffer memory",
        "ib_flap": "mlx5_core 0000:af:00.0: Link down",
        "dataloader_oom_kill": "Out of memory: Killed process 9931 (pt_data_worker)",
        "illegal_access": "NVRM: Xid 31: MMU fault",
    }
    for n in range(8):
        entries = [f"{timestamp(0)} kernel: Linux version 6.8.0-synthetic",
                   f"{timestamp(120)} kernel: GPU driver initialized"]
        if n == node and fault_id in kernel_evidence:
            evidence = kernel_evidence[fault_id]
            entries.append(f"{timestamp(FAULT_SECOND)} kernel: {evidence}")
            if fault_id == "ib_flap":
                entries.append(f"{timestamp(FAULT_SECOND + 1)} kernel: mlx5_core 0000:af:00.0: Link down")
        elif n == node and fault_id == "fs_stall":
            entries.append(f"{timestamp(FAULT_SECOND - 120)} kernel: nfs: server fs01 not responding, still trying")
        else:
            entries.append(f"{timestamp(400)} kernel: node healthy")
        (logs / f"dmesg_node-{n:02d}.log").write_text("\n".join(entries) + "\n")

    scheduler = [f"{timestamp(0)} job_id: synthetic-{fault_id}",
                 f"{timestamp(1)} allocated nodes: node-00..node-07",
                 f"{timestamp(2)} state: RUNNING"]
    if fault_id == "preemption":
        scheduler.append(f"{timestamp(FAULT_SECOND)} event: Job preempted by higher priority job")
    elif fault_id == "nccl_version_mismatch":
        scheduler.append(f"{timestamp(FAULT_SECOND - 22)} node-{node:02d} reimaged with older image cuda12.2-nccl2.18")
        scheduler.append(f"{timestamp(FAULT_SECOND)} state: FAILED at first collective after restart")
    elif needs_dashboard:
        scheduler.append(f"{timestamp(FAULT_SECOND + 600)} state: FAILED DUE TO TIME LIMIT")
    else:
        scheduler.append(f"{timestamp(FAULT_SECOND + 625)} state: FAILED")
    (logs / "scheduler.log").write_text("\n".join(scheduler) + "\n")

    config = "# Synthetic job configuration\njob_name: training-demo\nnodes: 8\ngpus_per_node: 8\nprecision: bf16\ncheckpoint_interval: 40\n"
    (root / "job_config.yaml").write_text(config)
    render_dashboard(root / "dashboard.png", fault_id, node, gpu)

    evidence_sources = {
        "xid79_demo": (f"logs/dmesg_node-{node:02d}.log", "Xid (PCI:0000:8a:00): 79"),
        "ecc_dbe": (f"logs/dmesg_node-{node:02d}.log", "Xid 48: double-bit ECC"),
        "ib_flap": (f"logs/dmesg_node-{node:02d}.log", "Link down"),
        "oom_one_rank": (f"logs/rank_{rank:03d}.log", "torch.OutOfMemoryError"),
        "nan_divergence": (f"logs/rank_{rank:03d}.log", "gnorm inf"),
        "thermal_throttle": ("dashboard.png", None),
        "power_cap": ("dashboard.png", None),
        "fs_stall": (f"logs/dmesg_node-{node:02d}.log", "not responding"),
        "nccl_version_mismatch": (f"logs/rank_{rank:03d}.log", "NCCL version 2.18.3"),
        "dataloader_oom_kill": (f"logs/dmesg_node-{node:02d}.log", "Killed process 9931"),
        "preemption": ("logs/scheduler.log", "preempted by higher priority job"),
        "illegal_access": (f"logs/rank_{rank:03d}.log", "illegal memory access"),
    }
    evidence_file, needle = evidence_sources[fault_id]
    evidence_line = _find_line(root / evidence_file, needle) if needle else None
    truth = {"fault_id": fault_id, "category": category, "node": f"node-{node:02d}", "gpu": gpu, "rank": rank,
             "evidence_file": evidence_file, "evidence_line": evidence_line,
             "needs_dashboard": needs_dashboard, "notes": "Synthetic incident; planted ground truth for evaluation only."}
    (root / "truth.json").write_text(json.dumps(truth, indent=2) + "\n")
    return root


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", choices=sorted(FAULTS))
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=Path("incidents"))
    args = parser.parse_args()
    if bool(args.id) == args.all:
        parser.error("choose exactly one of --id or --all")
    ids = [args.id] if args.id else list(FAULTS)
    for fault_id in ids:
        path = make_incident(fault_id, args.out, args.seed)
        print(f"Generated synthetic incident {fault_id}: {path}")


if __name__ == "__main__":
    main()
