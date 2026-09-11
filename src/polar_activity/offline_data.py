"""Strict unencrypted .REC decoding and export to the existing motion dataset format."""

import csv
import hashlib
import json
import os
import struct
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .diagnostics import diagnose
from .heart_rate import HR_FIELDS, OFFLINE_HR_TIMING
from .magnetometer import calibration_fields, decode_mag
from .protocol import (
    AcquisitionError,
    StreamConfig,
    TimestampReconstructor,
    conversion_factor,
    decode_imu,
    parse_settings,
)
from .storage import write_json


def recover_duplicate_blocks(data: bytes, packets: list[bytes], expected: int, stream: str):
    """A candidate only: caller must verify it with a second independent file read.

    Never drop repeated samples. Only adjacent byte-identical RFC76 payload blocks
    are candidates, and the resulting full file must match the listed size and parse.
    """
    if not packets or b"".join(p[1:] for p in packets) != data:
        raise AcquisitionError("Cannot verify mismatched download without its transport packets")
    kept = []
    removed = []
    for index, packet in enumerate(packets):
        payload = packet[1:]
        if kept and payload == kept[-1]:
            removed.append(index)
        else:
            kept.append(payload)
    candidate = b"".join(kept)
    if not removed or len(candidate) != expected:
        raise AcquisitionError(
            f"Downloaded {len(data)} bytes; expected {expected}. "
            "Duplicate-block recovery could not establish the complete file; raw bytes preserved"
        )
    decoded = parse_recording(candidate, stream)
    stamps = [frame["last"] for frame in decoded["frames"]]
    if stream != "hr" and any(b <= a for a, b in zip(stamps, stamps[1:], strict=False)):
        raise AcquisitionError("Recovered file has duplicate/backward frame timestamps")
    return candidate, removed


def decode_offline_hr(payload):
    # Polar SDK OfflineHrData: raw type 0 = BPM; raw type 1 = BPM/quality/corrected BPM.
    if len(payload) < 11 or payload[0] & 0x3F != 14 or payload[9] not in (0, 1):
        raise AcquisitionError("Unsupported or empty offline HR frame")
    width = 1 if payload[9] == 0 else 3
    if (len(payload) - 10) % width:
        raise AcquisitionError("Truncated offline HR sample")
    return [
        {
            "hr_bpm": payload[i],
            "ppg_quality": payload[i + 1] if width == 3 else "",
            "corrected_hr_bpm": payload[i + 2] if width == 3 else "",
        }
        for i in range(10, len(payload), width)
    ]


def parse_recording(data: bytes, stream: str) -> dict:
    offset = 0

    def take(size):
        nonlocal offset
        if size < 0 or offset + size > len(data):
            raise AcquisitionError("Truncated offline recording; retain the original .REC file")
        value = data[offset : offset + size]
        offset += size
        return value

    if take(1) != b"\x00":
        raise AcquisitionError("Encrypted .REC files are not supported by this recorder")
    magic, version, reserved, firmware_hash = struct.unpack("<IIII", take(16))
    if magic != 0x3D7C4C2B:
        raise AcquisitionError("Invalid offline recording signature")
    if version != 1:
        raise AcquisitionError(f"Unsupported offline recording format version {version}")
    date_bytes = take(20)
    try:
        if date_bytes[-1] != 0:
            raise ValueError()
        started = datetime.strptime(date_bytes[:19].decode().replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except (UnicodeError, ValueError):
        raise AcquisitionError("Invalid offline recording date") from None
    settings_bytes = take(take(1)[0])
    settings = parse_settings(settings_bytes)
    config = None
    try:
        if stream == "hr":
            # HR uses fixed settings; empty settings are present in the SDK fixture.
            if settings.get("sample_rate", [1]) != [1]:
                raise AcquisitionError("Unsupported offline HR sample rate")
        else:
            config = _imu_config(settings, stream)
    except (KeyError, IndexError, ValueError):
        raise AcquisitionError("Offline recording lacks a single valid IMU configuration") from None
    security_length = take(1)[0]
    security = take(security_length)
    if security not in (b"", b"\x00"):
        raise AcquisitionError("Encrypted offline payload is not supported")
    factor = conversion_factor(settings) if stream != "hr" else None
    frames = []
    while offset < len(data):
        size = int.from_bytes(take(2), "little")
        frame_offset = offset
        payload = take(size)
        if size < 10 or payload[0] & 0x3F != {"acc": 2, "gyro": 5, "hr": 14, "mag": 6}[stream]:
            raise AcquisitionError("Invalid offline frame length or measurement type")
        if stream == "hr":
            last, samples = int.from_bytes(payload[1:9], "little"), decode_offline_hr(payload)
        elif stream == "mag":
            last, samples, calibration = decode_mag(payload, config, factor)
        else:
            last, samples = decode_imu(payload, config, factor)
        if not samples:
            raise AcquisitionError("Offline frame contains no samples")
        frames.append(
            {"offset": frame_offset, "payload": payload, "last": last, "samples": samples}
        )
        if stream == "mag":
            frames[-1]["calibration"] = calibration
    if not frames:
        raise AcquisitionError(
            "Offline recording contains no samples; record longer before stopping"
        )
    return {
        "header": {
            "version": version,
            "reserved": reserved,
            "firmware_hash": firmware_hash,
            "started_utc": started.replace(tzinfo=UTC).isoformat(),
        },
        "config": config,
        "factor": factor,
        "factor_source": (
            None
            if stream == "hr"
            else "device_file"
            if "factor" in settings
            else "polar_sdk_default"
        ),
        "settings_hex": settings_bytes.hex(),
        "frames": frames,
    }


def _imu_config(settings, stream="acc"):
    try:
        values = {k: settings[k][0] for k in ("sample_rate", "resolution", "range", "channels")}
        if any(len(settings[k]) != 1 for k in values) or values["sample_rate"] <= 0:
            raise ValueError()
        config = StreamConfig(**values)
    except (KeyError, IndexError, ValueError):
        raise AcquisitionError("Offline recording lacks a single valid IMU configuration") from None
    if config.channels not in ((3, 4) if stream == "mag" else (3,)) or config.resolution != 16:
        raise AcquisitionError("Unsupported offline IMU channels/resolution")
    return config


def _csv(path, fields, rows):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def _hr_rows(session, downloads, anchor, packets, headers):
    rows = []
    origin = None
    previous_header = None
    sample_index = 0
    for source in downloads:
        if source["stream"] != "hr":
            continue
        data = (session / source["local_path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != source["sha256"]:
            raise AcquisitionError("Downloaded HR file hash mismatch")
        record = parse_recording(data, "hr")
        headers.append(
            {"path": source["path"], **record["header"], "settings_hex": record["settings_hex"]}
        )
        start = datetime.fromisoformat(record["header"]["started_utc"])
        delta = start - datetime(2000, 1, 1, tzinfo=UTC)
        header_ns = (delta.days * 86400 + delta.seconds) * 10**9
        # SDK HR samples carry no individual timestamps (even the frame timestamp
        # can be zero). Keep the original packet; label this nominal timeline.
        # A repeated split-file header continues the same sample index. A new
        # header starts a new interval, and must not overlap the preceding one.
        if previous_header != header_ns:
            if rows and header_ns + 10**9 <= origin + sample_index * 10**9:
                raise AcquisitionError("Offline HR split headers overlap or go backwards")
            origin, sample_index = header_ns, 0
        previous_header = header_ns
        for frame in record["frames"]:
            packet_id = len(packets) + 1
            packets.append(
                {
                    "packet_id": packet_id,
                    "stream": "hr",
                    "payload_hex": frame["payload"].hex(),
                    "source": "sensor_memory",
                    "source_path": source["path"],
                    "source_offset": frame["offset"],
                    "host_monotonic_ns": None,
                    "host_time_utc": None,
                }
            )
            for sample in frame["samples"]:
                sample_index += 1
                rows.append(
                    {
                        **dict.fromkeys(HR_FIELDS, ""),
                        **sample,
                        "time_s": (origin + sample_index * 10**9 - anchor) / 1e9,
                        "packet_id": packet_id,
                        "sample_index": sample_index - 1,
                        "packet_timestamp_ns": frame["last"],
                        "timestamp_method": "offline_header_1hz_estimate",
                    }
                )
    return rows


def export_recordings(session: Path, manifest: dict, downloads: list[dict]) -> dict:
    """Preserve raw files, reject malformed frames, and use one device clock for both IMUs."""
    rows = {"acc": [], "gyro": []}
    if "mag" in manifest.get("requested_streams", []):
        rows["mag"] = []
    packets = []
    configurations = {}
    factors = {}
    factor_sources = {}
    headers = []
    for stream in rows:
        sources = [f for f in downloads if f["stream"] == stream]
        if not sources:
            raise AcquisitionError(f"No downloaded {stream.upper()} recording")
        reconstructor = None
        for source in sources:
            data = (session / source["local_path"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != source["sha256"]:
                raise AcquisitionError("Downloaded file hash mismatch")
            record = parse_recording(data, stream)
            config = asdict(record["config"])
            if stream in configurations and config != configurations[stream]:
                raise AcquisitionError("Split offline recording changes configuration")
            configurations[stream] = config
            factors[stream] = record["factor"]
            factor_sources[stream] = record["factor_source"]
            headers.append(
                {
                    "path": source["path"],
                    **record["header"],
                    "settings_hex": record["settings_hex"],
                    "factor": record["factor"],
                    "factor_source": record["factor_source"],
                }
            )
            reconstructor = reconstructor or TimestampReconstructor(config["sample_rate"])
            for frame in record["frames"]:
                stamps, method = reconstructor.reconstruct(frame["last"], len(frame["samples"]))
                if method == "non_monotonic_frame":
                    raise AcquisitionError("Offline timestamps go backwards across frames/files")
                packet_id = len(packets) + 1
                packets.append(
                    {
                        "packet_id": packet_id,
                        "stream": stream,
                        "payload_hex": frame["payload"].hex(),
                        "source": "sensor_memory",
                        "source_path": source["path"],
                        "source_offset": frame["offset"],
                        "host_monotonic_ns": None,
                        "host_time_utc": None,
                    }
                )
                unit = {"acc": "mg", "gyro": "dps", "mag": "ut"}[stream]
                for index, (stamp, xyz) in enumerate(zip(stamps, frame["samples"], strict=True)):
                    rows[stream].append(
                        {
                            "device_timestamp_ns": stamp,
                            "packet_timestamp_ns": frame["last"],
                            "packet_id": packet_id,
                            "sample_index": index,
                            "host_monotonic_ns": "",
                            "host_time_utc": "",
                            "timestamp_method": method,
                            **{
                                f"{stream}_{axis}_{unit}": v
                                for axis, v in zip("xyz", xyz, strict=True)
                            },
                        }
                    )
                    if stream == "mag":
                        rows[stream][-1].update(calibration_fields(frame["calibration"][index]))
    # Auxiliary streams must not shift the motion origin or change existing predictions.
    anchor = min(rows[s][0]["device_timestamp_ns"] for s in ("acc", "gyro"))
    duration = (max(rows[s][-1]["device_timestamp_ns"] for s in ("acc", "gyro")) - anchor) / 1e9
    hr_rows = _hr_rows(session, downloads, anchor, packets, headers)
    if "hr" in manifest.get("requested_streams", []) and not hr_rows:
        raise AcquisitionError("Requested HR recording is missing; retain files and retry sync")
    for stream, items in rows.items():
        for row in items:
            row["time_s"] = (row["device_timestamp_ns"] - anchor) / 1e9
        _csv(session / f"{stream}.csv", ["time_s", *[k for k in items[0] if k != "time_s"]], items)
    _csv(
        session / "hr.csv",
        HR_FIELDS
        + (
            [
                "ppg_quality",
                "corrected_hr_bpm",
                "sample_index",
                "packet_timestamp_ns",
                "timestamp_method",
            ]
            if hr_rows
            else []
        ),
        hr_rows,
    )
    # Offline labels are supplied later by the user; do not infer boundaries from filenames.
    if not (session / "labels.csv").exists():
        _csv(
            session / "labels.csv",
            [
                "set_id",
                "activity",
                "start_time_s",
                "end_time_s",
                "expected_rep_count",
                "note",
                "completion",
            ],
            [],
        )
    temporary = session / "packets.jsonl.tmp"
    temporary.write_text("".join(json.dumps(packet) + "\n" for packet in packets), encoding="utf-8")
    os.replace(temporary, session / "packets.jsonl")
    metadata = {
        "schema_version": 1,
        "capture_mode": "sensor_memory",
        "session_id": manifest["session_id"],
        "started_utc": manifest["started_utc"],
        "subject": manifest["subject"],
        "sensor_position": manifest["sensor_position"],
        "arm": manifest["arm"],
        "notes": manifest["notes"],
        "device": manifest["device"],
        "status": "complete",
        "hr_enabled": bool(hr_rows),
        "mag_enabled": "mag" in rows,
        "hr_timing": OFFLINE_HR_TIMING if hr_rows else None,
        "configurations": configurations,
        "scale_factors": factors,
        "scale_factor_sources": factor_sources,
        "recording_duration_s": duration,
        "warnings": [],
        "parse_errors": [],
        "application_dropped_packets": 0,
        "clock_mapping": {
            "method": "earliest_offline_device_sample",
            "device_anchor_ns": anchor,
            "alignment_uncertainty_ms": None,
            "caveat": (
                "Both streams share original device time; "
                "no live host arrivals or acquisition-UTC calibration."
            ),
        },
        "offline_headers": headers,
        "offline_source_files": downloads,
        "downloaded_utc": datetime.now(UTC).isoformat(),
        "completion_scope": (
            "Downloaded files are complete; "
            "requested wall-time coverage is not independently proven."
        ),
    }
    quality = diagnose(session, metadata)
    metadata["quality"] = quality
    if quality["warnings"]:
        metadata["warnings"] = list(quality["warnings"])
        metadata["status"] = "partial"
    write_json(session / "metadata.json", metadata)
    return metadata
