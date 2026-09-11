"""Generate an explicitly artificial 12s fixture, never a physical experiment result."""

import argparse
import math
import struct
from dataclasses import asdict
from pathlib import Path

from polar_activity.device import Packet
from polar_activity.diagnostics import diagnose, format_report
from polar_activity.labels import Labels
from polar_activity.protocol import StreamConfig, TimestampReconstructor, decode_hr, decode_imu
from polar_activity.storage import SessionStore


def generate(path: Path) -> None:
    store = SessionStore(path, "SYNTHETIC", "synthetic_upper_arm", "unknown", "Artificial QA only")
    store.metadata["synthetic"] = True
    now = [0.0]
    labels = Labels(path, lambda: now[0])
    for activity, begin in [("pull-up", 1), ("push-up", 4), ("squat", 7), ("jump", 10)]:
        now[0] = begin
        labels.select(activity)
        item = labels.toggle()
        now[0] = begin + 1.5
        labels.toggle()
        labels.reps(item, "1")
    labels.close()
    epoch = 800_000_000_000_000_123
    for stream, kind, sensor_range, factor in [("acc", 2, 8, 1), ("gyro", 5, 2000, 0.125)]:
        config = StreamConfig(52, 16, sensor_range, 3)
        store.metadata["configurations"][stream] = asdict(config)
        store.metadata["scale_factors"][stream] = factor
        recon = TimestampReconstructor(52)
        for batch in range(24):
            content = bytearray()
            for index in range(batch * 26, (batch + 1) * 26):
                t = index / 52
                amplitude = math.sin(2 * math.pi * t)
                content.extend(
                    struct.pack(
                        "<hhh",
                        int(250 * amplitude),
                        int(130 * math.cos(t)),
                        1000 + int(90 * amplitude),
                    )
                )
            last = epoch + round(((batch + 1) * 26 - 1) * 1e9 / 52)
            # ACC permits raw type 1; gyro type 0 requires delta compression.
            frame = 1
            if kind == 5:
                samples = list(struct.iter_unpack("<hhh", content))
                content = bytearray(struct.pack("<hhh", *samples[0]))
                content.extend(bytes([16, len(samples) - 1]))
                for previous, current in zip(samples, samples[1:], strict=False):
                    content.extend(
                        struct.pack(
                            "<hhh", *[b - a for a, b in zip(previous, current, strict=True)]
                        )
                    )
                frame = 0x80
            payload = bytes([kind]) + last.to_bytes(8, "little") + bytes([frame]) + content
            arrival = store.started_ns + last - epoch + 50_000_000
            packet = Packet(stream, payload, arrival, store.metadata["started_utc"])
            ident = store.packet(packet)
            stamp, samples = decode_imu(payload, config, factor)
            times, method = recon.reconstruct(stamp, len(samples))
            store.imu(packet, ident, stamp, times, samples, method)
    for second in range(12):
        packet = Packet(
            "hr",
            bytes([0, 70 + second]),
            store.started_ns + second * 10**9,
            store.metadata["started_utc"],
        )
        store.hr(packet, store.packet(packet), decode_hr(packet.payload))
    store.metadata.update(
        status="complete", recording_duration_s=12, hr_enabled=True, application_dropped_packets=0
    )
    store.close()
    store.metadata["quality"] = diagnose(path, store.metadata)
    store.save_metadata()
    print("SYNTHETIC DATA ONLY\n" + format_report(store.metadata["quality"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    generate(parser.parse_args().output)
