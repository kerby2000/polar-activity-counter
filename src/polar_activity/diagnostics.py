"""Quality evidence from acquisition timestamps, not BLE delivery cadence."""

import csv
import json
from collections import Counter
from pathlib import Path


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def stream_quality(rows: list[dict], rate: float) -> dict:
    stamps = [int(row["device_timestamp_ns"]) for row in rows]
    deltas = [b - a for a, b in zip(stamps, stamps[1:], strict=False)]
    span = (stamps[-1] - stamps[0]) / 1e9 if len(stamps) > 1 else 0
    period = 1e9 / rate
    packets: list[list[int]] = []
    for row in rows:
        packet_id = int(row["packet_id"])
        if not packets or packets[-1][0] != packet_id:
            packets.append([packet_id, int(row["packet_timestamp_ns"]), 0])
        packets[-1][2] += 1
    frame_missing = 0
    frame_discontinuities = 0
    for previous, current in zip(packets, packets[1:], strict=False):
        delta = current[1] - previous[1]
        if abs(delta - current[2] * period) > period * 0.5:
            frame_discontinuities += 1
        frame_missing += max(0, round(delta / period) - current[2])
    frame_span = (packets[-1][1] - packets[0][1]) / 1e9 if len(packets) > 1 else 0
    missing = sum(max(0, round(d / period) - 1) for d in deltas if d > period * 1.5)
    return dict(
        samples=len(stamps),
        packets=len(packets),
        expected_rate_hz=rate,
        measured_rate_hz=(len(stamps) - 1) / span if span > 0 else None,
        packet_endpoint_rate_hz=(len(stamps) - packets[0][2]) / frame_span
        if frame_span > 0
        else None,
        duration_s=span,
        largest_gap_ms=max(deltas, default=0) / 1e6,
        suspicious_gaps=sum(d > period * 1.5 for d in deltas),
        duplicate_timestamps=len(stamps) - len(set(stamps)),
        non_monotonic_timestamps=sum(d < 0 for d in deltas),
        estimated_missing_samples=missing,
        frame_estimated_missing_samples=frame_missing,
        frame_discontinuities=frame_discontinuities,
        first_device_timestamp_ns=stamps[0] if stamps else None,
        last_device_timestamp_ns=stamps[-1] if stamps else None,
        loss_caveat="No sequence counter: inferred sample loss, not exact packet loss",
    )


def diagnose(path: Path, metadata: dict | None = None) -> dict:
    metadata = metadata or json.loads((path / "metadata.json").read_text(encoding="utf-8"))
    result: dict = {
        "duration_s": metadata.get("recording_duration_s"),
        "warnings": list(metadata.get("warnings", [])),
    }
    for stream in ("acc", "gyro"):
        rate = metadata.get("configurations", {}).get(stream, {}).get("sample_rate", 52)
        result[stream] = stream_quality(read_rows(path / f"{stream}.csv"), rate)
        quality = result[stream]
        if not quality["samples"]:
            result["warnings"].append(f"{stream.upper()} has no samples")
        if (
            quality["frame_discontinuities"]
            or quality["duplicate_timestamps"]
            or quality["non_monotonic_timestamps"]
        ):
            result["warnings"].append(
                f"{stream.upper()} timestamp discontinuities: inspect raw packets"
            )
        measured = quality["packet_endpoint_rate_hz"]
        if measured is not None and abs(measured / rate - 1) > 0.02:
            result["warnings"].append(
                f"{stream.upper()} measured rate differs from configured rate by >2%"
            )
    acc, gyro = result["acc"], result["gyro"]
    mismatch = abs(acc["duration_s"] - gyro["duration_s"])
    result["acc_gyro_duration_mismatch_s"] = mismatch
    result["acc_gyro_overlap_s"] = 0
    if acc["samples"] and gyro["samples"]:
        result["acc_gyro_overlap_s"] = max(
            0,
            (
                min(acc["last_device_timestamp_ns"], gyro["last_device_timestamp_ns"])
                - max(acc["first_device_timestamp_ns"], gyro["first_device_timestamp_ns"])
            )
            / 1e9,
        )
        if mismatch > max(0.5, 2 / acc["expected_rate_hz"]):
            result["warnings"].append(
                "ACC/gyro duration mismatch exceeds 0.5s (startup/shutdown may differ)"
            )
        if not result["acc_gyro_overlap_s"]:
            result["warnings"].append("ACC and gyro have no overlapping device-time interval")
    hr_values = [int(row["hr_bpm"]) for row in read_rows(path / "hr.csv")]
    result["hr"] = dict(
        samples=len(hr_values),
        mean_bpm=sum(hr_values) / len(hr_values) if hr_values else None,
        min_bpm=min(hr_values, default=None),
        max_bpm=max(hr_values, default=None),
    )
    if metadata.get("hr_enabled") and not hr_values:
        result["warnings"].append("HR enabled but no HR samples arrived")
    sets = read_rows(path / "labels.csv")
    result["labels"] = dict(Counter(s["activity"] for s in sets))
    result["unknown_rep_counts"] = sum(not s["expected_rep_count"] for s in sets)
    result["incomplete_sets"] = sum(s["completion"] != "complete" for s in sets)
    result["application_dropped_packets"] = metadata.get("application_dropped_packets", 0)
    if result["application_dropped_packets"]:
        result["warnings"].append("Application buffer dropped packets; session is incomplete")
    if metadata.get("error"):
        result["warnings"].append(metadata["error"])
    result["warnings"].extend(metadata.get("parse_errors", []))
    return result


def format_report(quality: dict) -> str:
    lines = ["Session quality report", f"Recording duration: {quality['duration_s']} s"]
    for stream in ("acc", "gyro"):
        value = quality[stream]
        rate = value["measured_rate_hz"]
        rate_text = f"{rate:.3f}" if rate is not None else "unavailable"
        lines.append(
            f"{stream.upper()}: {value['samples']} samples, "
            f"expected {value['expected_rate_hz']} Hz, "
            f"measured {rate_text} Hz, span {value['duration_s']:.3f}s, "
            f"largest gap {value['largest_gap_ms']:.3f}ms; gaps={value['suspicious_gaps']}, "
            f"duplicates={value['duplicate_timestamps']}, "
            f"backwards={value['non_monotonic_timestamps']}, "
            f"estimated missing={value['frame_estimated_missing_samples']}"
        )
        endpoint = value["packet_endpoint_rate_hz"]
        lines.append(
            f"  Rate from packet endpoints: {endpoint:.3f} Hz"
            if endpoint is not None
            else "  Rate from packet endpoints: unavailable (need at least two batches)"
        )
    lines.append(
        f"ACC/gyro duration mismatch: {quality['acc_gyro_duration_mismatch_s']:.3f}s; "
        f"overlap {quality['acc_gyro_overlap_s']:.3f}s"
    )
    lines.append(f"HR: {quality['hr']}")
    lines.append(
        f"Labelled sets: {quality['labels']}; incomplete={quality['incomplete_sets']}, "
        f"unknown rep counts={quality['unknown_rep_counts']}"
    )
    lines.extend(f"WARNING: {warning}" for warning in quality["warnings"])
    return "\n".join(lines)
