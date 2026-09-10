"""Append-only raw streams with atomic metadata snapshots."""

import csv
import json
import os
import platform
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import TextIO

from . import __version__
from .device import Packet


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class SessionStore:
    def __init__(self, path: Path, subject: str, position: str, arm: str, notes: str):
        path.mkdir(parents=True, exist_ok=False)
        self.path = path
        self.started_ns = time.monotonic_ns()
        self.anchor: tuple[int, int] | None = None
        self.files: list[TextIO] = []
        self.writers: dict[str, csv.DictWriter] = {}
        self.packet_id = 0
        self.metadata = {
            "schema_version": 1,
            "session_id": str(uuid.uuid4()),
            "started_utc": datetime.now(UTC).isoformat(),
            "host_start_monotonic_ns": self.started_ns,
            "software_version": __version__,
            "python": platform.python_version(),
            "os": platform.platform(),
            "dependencies": {
                p: version(p) for p in ("polar-python", "bleak", "numpy", "matplotlib")
            },
            "subject": subject,
            "sensor_position": position,
            "arm": arm,
            "notes": notes,
            "status": "recording",
            "sdk_mode_requested": False,
            "hr_enabled": False,
            "configurations": {},
            "scale_factors": {},
            "warnings": [],
            "clock_mapping": None,
        }
        common = [
            "time_s",
            "device_timestamp_ns",
            "packet_timestamp_ns",
            "packet_id",
            "sample_index",
            "host_monotonic_ns",
            "host_time_utc",
            "timestamp_method",
        ]
        for stream, unit in [("acc", "mg"), ("gyro", "dps")]:
            self._csv(stream, common + [f"{stream}_{axis}_{unit}" for axis in "xyz"])
        self._csv(
            "hr",
            [
                "time_s",
                "host_monotonic_ns",
                "host_time_utc",
                "packet_id",
                "hr_bpm",
                "contact_supported",
                "contact_detected",
                "energy_expended",
                "rr_intervals_ms",
            ],
        )
        self.raw = self._open("packets.jsonl")
        self.save_metadata()

    def _open(self, name: str) -> TextIO:
        handle = (self.path / name).open("w", newline="", encoding="utf-8")
        self.files.append(handle)
        return handle

    def _csv(self, stream: str, fields: list[str]) -> None:
        writer = csv.DictWriter(self._open(f"{stream}.csv"), fieldnames=fields)
        writer.writeheader()
        self.writers[stream] = writer

    def elapsed(self, mono: int | None = None) -> float:
        return ((mono if mono is not None else time.monotonic_ns()) - self.started_ns) / 1e9

    def packet(self, packet: Packet) -> int:
        self.packet_id += 1
        record = asdict(packet)
        record["payload_hex"] = record.pop("payload").hex()
        record["packet_id"] = self.packet_id
        self.raw.write(json.dumps(record) + "\n")
        return self.packet_id

    def imu(
        self,
        packet: Packet,
        packet_id: int,
        last: int,
        stamps: list[int],
        samples: list[list[float]],
        method: str,
    ) -> None:
        if self.anchor is None:
            self.anchor = (last, packet.host_monotonic_ns)
            self.metadata["clock_mapping"] = {
                "device_anchor_ns": last,
                "host_anchor_monotonic_ns": packet.host_monotonic_ns,
                "method": "first_IMU_final_sample_to_host_arrival",
                "alignment_uncertainty_ms": None,
                "caveat": "Arrival anchor includes unknown BLE delay; not acquisition UTC.",
            }
            self.save_metadata()
        anchor_device, anchor_host = self.anchor
        unit = "mg" if packet.stream == "acc" else "dps"
        for index, (stamp, sample) in enumerate(zip(stamps, samples, strict=True)):
            row = dict(
                time_s=(stamp - anchor_device + anchor_host - self.started_ns) / 1e9,
                device_timestamp_ns=stamp,
                packet_timestamp_ns=last,
                packet_id=packet_id,
                sample_index=index,
                host_monotonic_ns=packet.host_monotonic_ns,
                host_time_utc=packet.host_time_utc,
                timestamp_method=method,
            )
            row.update(
                {
                    f"{packet.stream}_{axis}_{unit}": value
                    for axis, value in zip("xyz", sample, strict=True)
                }
            )
            self.writers[packet.stream].writerow(row)

    def hr(self, packet: Packet, packet_id: int, values: dict) -> None:
        self.writers["hr"].writerow(
            dict(
                time_s=self.elapsed(packet.host_monotonic_ns),
                host_monotonic_ns=packet.host_monotonic_ns,
                host_time_utc=packet.host_time_utc,
                packet_id=packet_id,
                **{**values, "rr_intervals_ms": json.dumps(values["rr_intervals_ms"])},
            )
        )

    def save_metadata(self) -> None:
        write_json(self.path / "metadata.json", self.metadata)

    def flush(self) -> None:
        for handle in self.files:
            handle.flush()

    def close(self) -> None:
        for handle in self.files:
            handle.close()
