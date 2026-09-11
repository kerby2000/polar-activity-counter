"""Summarize saved notification arrival timing; no hardware connection is made."""

import argparse
import json
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def analyze(session: Path, output: Path) -> dict:
    metadata = json.loads((session / "metadata.json").read_text())
    packets = [json.loads(line) for line in (session / "packets.jsonl").read_text().splitlines()]
    clock = metadata["clock_mapping"]
    result = {
        "session_id": metadata["session_id"],
        "status": metadata["status"],
        "finalization_time_s": metadata["recording_duration_s"],
        "streams": {},
    }
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    for index, stream in enumerate(("acc", "gyro", "hr")):
        selected = [packet for packet in packets if packet["stream"] == stream]
        if not selected:
            continue
        arrivals = np.array(
            [(p["host_monotonic_ns"] - metadata["host_start_monotonic_ns"]) / 1e9 for p in selected]
        )
        gaps = np.diff(arrivals)
        stats = {
            "packets": len(selected),
            "first_arrival_s": float(arrivals[0]),
            "last_arrival_s": float(arrivals[-1]),
            "max_arrival_gap_s": float(max(gaps)) if len(gaps) else None,
            "median_arrival_gap_s": float(np.median(gaps)) if len(gaps) else None,
            "seconds_final_arrival_to_finalization": float(
                metadata["recording_duration_s"] - arrivals[-1]
            ),
        }
        color = ("#235789", "#2a9d8f", "#ae517d")[index]
        axes[0].scatter(
            arrivals,
            np.full(len(arrivals), index),
            marker="|",
            s=150,
            color=color,
            label=stream.upper(),
        )
        axes[1].plot(arrivals[1:], gaps, ".-", lw=0.8, color=color, label=stream.upper())
        if stream != "hr" and clock:
            endpoints = np.array(
                [
                    (
                        int.from_bytes(bytes.fromhex(p["payload_hex"])[1:9], "little")
                        - clock["device_anchor_ns"]
                        + clock["host_anchor_monotonic_ns"]
                        - metadata["host_start_monotonic_ns"]
                    )
                    / 1e9
                    for p in selected
                ]
            )
            stats["last_mapped_sample_time_s"] = float(endpoints[-1])
            stats["last_arrival_minus_mapped_endpoint_s"] = float(arrivals[-1] - endpoints[-1])
        result["streams"][stream] = stats
    axes[0].set(yticks=[0, 1, 2], yticklabels=["ACC", "GYRO", "HR"], ylim=(-0.5, 2.5))
    axes[1].set(ylabel="Host arrival gap (s)", xlabel="Session time (s)")
    for axis in axes:
        axis.axvline(
            metadata["recording_duration_s"], color="#b43c27", ls="--", label="Finalization begins"
        )
        axis.grid(alpha=0.2)
    axes[1].legend(loc="upper left", ncol=4)
    fig.suptitle(
        f"{session.name}: notification arrivals and host delivery gaps\n"
        "Host delivery gaps are not missing-sample measurements"
    )
    fig.tight_layout()
    output.mkdir(parents=True, exist_ok=True)
    fig.savefig(output / "connection_timing.png", dpi=140)
    plt.close(fig)
    (output / "connection_timing.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.session, args.output), indent=2))
