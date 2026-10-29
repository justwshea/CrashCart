"""Create a deterministic, unbranded GPU telemetry dashboard."""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


SAMPLE_SECONDS = 120


def telemetry(fault_id: str, node: int, gpu: int):
    """Return time series and the heatmap snapshot at T_f + 120 seconds."""
    rng = np.random.default_rng(731 + node * 8 + gpu)
    times = np.arange(0, 601, 30)  # seconds relative to T_f
    util = np.clip(91 + rng.normal(0, 2, (len(times), 64)), 0, 100)
    clock = np.clip(1500 + rng.normal(0, 25, (len(times), 64)), 800, 1600)
    power = np.clip(285 + rng.normal(0, 8, (len(times), 64)), 100, 350)
    temp = np.clip(68 + rng.normal(0, 2, 64), 40, 99).reshape(8, 8)
    index = node * 8 + gpu

    if fault_id in {"xid79_demo", "ecc_dbe", "ib_flap", "oom_one_rank",
                    "dataloader_oom_kill", "illegal_access", "preemption"}:
        util[times >= 0, index] = 0
        clock[times >= 0, index] = 0
        power[times >= 0, index] = 0
    elif fault_id == "nan_divergence":
        util[times >= 0, index] = 0
        clock[times >= 0, index] = 0
    elif fault_id == "thermal_throttle":
        after = times >= 0
        temp[node, gpu] = 94
        clock[after, index] = np.interp(times[after], [0, SAMPLE_SECONDS, 600], [1490, 510, 510])
    elif fault_id == "power_cap":
        after = times >= 0
        clock[after, index] = 620
        power[after, index] = 160
    elif fault_id in {"nccl_version_mismatch", "fs_stall"}:
        util[times >= 0, index] = 0
        clock[times >= 0, index] = 0

    sample_index = int(np.where(times == SAMPLE_SECONDS)[0][0])
    sampled_util = util[sample_index].reshape(8, 8).copy()
    sampled_clock = clock[sample_index].reshape(8, 8).copy()
    sampled_power = power[sample_index].reshape(8, 8).copy()
    return times, util, clock, power, temp, sampled_util, sampled_clock, sampled_power


def render_dashboard(path, fault_id: str, node: int, gpu: int) -> None:
    times, util, clock, power, temp, sampled_util, sampled_clock, _ = telemetry(fault_id, node, gpu)
    fig = plt.figure(figsize=(18, 10), dpi=100)
    fig.suptitle("GPU telemetry", fontsize=18)
    grid = fig.add_gridspec(2, 3, height_ratios=[1, 1.25], hspace=.38, wspace=.25)
    for idx, (values, title, ylabel, color) in enumerate((
        (util, "GPU utilization", "%", "#4c9f70"),
        (clock, "SM clock", "MHz", "#5287b5"),
        (power, "Power", "W", "#d18c45"),
    )):
        ax = fig.add_subplot(grid[0, idx])
        mean = values.mean(axis=1)
        ax.fill_between(times, values.min(axis=1), values.max(axis=1), color=color, alpha=.18)
        ax.plot(times, mean, color=color, linewidth=2)
        ax.set_title(title)
        ax.set_xlabel("Seconds from T_f")
        ax.set_ylabel(ylabel)
        ax.grid(alpha=.2)
    heatmaps = ((sampled_util, "Utilization (%)", "viridis", 0, 100),
                (temp, "Temperature (°C)", "inferno", 40, 100),
                (sampled_clock, "SM clock (MHz)", "cividis", 0, 1600))
    for idx, (values, title, cmap, lo, hi) in enumerate(heatmaps):
        ax = fig.add_subplot(grid[1, idx])
        image = ax.imshow(values, cmap=cmap, vmin=lo, vmax=hi, aspect="auto")
        ax.set_title(title + " at T_f + 120 s")
        ax.set_xticks(range(8), [str(x) for x in range(8)])
        ax.set_yticks(range(8), [f"node-{x:02d}" for x in range(8)])
        ax.set_xlabel("GPU")
        for row in range(8):
            for col in range(8):
                ax.text(col, row, f"{values[row, col]:.0f}", ha="center", va="center", fontsize=7, color="white")
        fig.colorbar(image, ax=ax, fraction=.046, pad=.04)
    fig.savefig(path, dpi=100, metadata={"Software": "CrashCart synthetic telemetry"})
    plt.close(fig)
