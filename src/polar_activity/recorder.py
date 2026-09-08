"""Recording orchestration; BLE, storage and labels remain independently testable."""

import asyncio
import logging
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .device import Packet, SenseDevice, find_device
from .keyboard import HELP, Keyboard, LabelController
from .labels import Labels
from .protocol import AcquisitionError, TimestampReconstructor, choose_config, decode_hr, decode_imu
from .storage import SessionStore

LOG = logging.getLogger(__name__)


class PacketBuffer:
    def __init__(self, maxsize: int = 4096):
        self.queue: asyncio.Queue[Packet] = asyncio.Queue(maxsize)
        self.dropped = 0

    def put(self, packet: Packet) -> None:
        try:
            self.queue.put_nowait(packet)
        except asyncio.QueueFull:
            self.dropped += 1


async def record_session(
    output: Path,
    subject: str,
    sensor_position: str,
    arm: str,
    duration: float,
    selector: str | None = None,
    scan_timeout: float = 8,
    hr: bool = True,
    interactive: bool = True,
    notes: str = "",
    device_factory=SenseDevice,
    selected_device=None,
) -> tuple[Path, dict]:
    from .diagnostics import diagnose

    selected = selected_device or await find_device(selector, scan_timeout)
    buffer = PacketBuffer()
    device = device_factory(selected, buffer.put)
    store = SessionStore(output, subject, sensor_position, arm, notes)
    labels = Labels(output, store.elapsed)
    recon: dict[str, TimestampReconstructor] = {}
    configs = {}
    last_arrival: dict[str, int] = {}
    error: Exception | None = None
    parse_errors: list[str] = []
    cancelled = False

    def process(packet: Packet) -> None:
        packet_id = store.packet(packet)  # Preserve bytes before attempting any interpretation.
        if packet.stream in configs:
            last, samples = decode_imu(
                packet.payload, configs[packet.stream], device.factors[packet.stream]
            )
            stamps, method = recon[packet.stream].reconstruct(last, len(samples))
            store.imu(packet, packet_id, last, stamps, samples, method)
            last_arrival[packet.stream] = packet.host_monotonic_ns
            if method == "non_monotonic_frame":
                raise AcquisitionError(
                    f"{packet.stream} clock moved backwards/repeated. "
                    "Raw packet retained; start a new session after checking sensor."
                )
        elif packet.stream == "hr":
            store.hr(packet, packet_id, decode_hr(packet.payload))
        else:
            raise AcquisitionError("Unexpected PMD stream/packet; raw bytes retained")

    try:
        await device.connect()
        report = await device.inspect()
        store.metadata["device"] = report
        store.metadata["warnings"].extend(report["warnings"])
        for stream in ("acc", "gyro"):
            if not report["available_streams"].get(stream):
                raise AcquisitionError(f"{stream.upper()} unavailable on this device")
            configs[stream] = choose_config(report["settings"].get(stream, {}), stream)
            recon[stream] = TimestampReconstructor(configs[stream].sample_rate)
            await device.start(stream, configs[stream])
            store.metadata["configurations"][stream] = asdict(configs[stream])
        if hr and report["available_streams"].get("hr"):
            try:
                await device.start_hr()
                store.metadata["hr_enabled"] = True
            except Exception as exc:
                warning = f"HR unavailable; IMU acquisition continues: {exc}"
                store.metadata["warnings"].append(warning)
                LOG.warning("%s", warning)
        elif hr:
            store.metadata["warnings"].append("HR characteristic not available")
        store.metadata["scale_factors"] = device.factors.copy()
        store.metadata["control_exchanges"] = device.exchanges
        stream_start = time.monotonic_ns()
        store.metadata["streaming_started_time_s"] = store.elapsed(stream_start)
        store.metadata["requested_duration_s"] = duration
        store.save_metadata()
        last_flush = time.monotonic()
        with Keyboard(interactive) as keyboard:
            controller = LabelController(labels)
            print(
                f"Recording to {output}\n"
                + (HELP if keyboard.enabled else "Keyboard labels disabled.")
            )
            while (time.monotonic_ns() - stream_start) / 1e9 < duration:
                if buffer.dropped:
                    raise AcquisitionError(
                        "Recorder buffer overflow; session stopped. Check disk/CPU load."
                    )
                if device.disconnected.is_set():
                    raise AcquisitionError(
                        "Sensor disconnected. Check charge, distance and other apps; "
                        "partial session saved. Start a new session to continue."
                    )
                for _ in range(min(buffer.queue.qsize(), 100)):
                    process(buffer.queue.get_nowait())
                char = keyboard.read()
                if char is not None and controller.key(char):
                    break
                now = time.monotonic_ns()
                for stream in configs:
                    if now - last_arrival.get(stream, stream_start) > 10 * 10**9:
                        raise AcquisitionError(
                            f"No {stream.upper()} data for 10s. Check device/connection; "
                            "partial data saved."
                        )
                if time.monotonic() - last_flush >= 1:
                    store.flush()
                    last_flush = time.monotonic()
                await asyncio.sleep(0.01)
        store.metadata["status"] = "complete"
    except asyncio.CancelledError:
        cancelled = True
        store.metadata["status"] = "interrupted"
    except Exception as exc:
        error = exc
        store.metadata["status"] = "failed"
        store.metadata["error"] = str(exc)
    finally:
        labels.close()
        store.metadata["recording_duration_s"] = store.elapsed()
        stop_errors = await device.close()
        store.metadata["warnings"].extend(stop_errors)
        while not buffer.queue.empty():
            try:
                process(buffer.queue.get_nowait())
            except Exception as exc:
                parse_errors.append(str(exc))
        store.metadata.update(
            ended_utc=datetime.now(UTC).isoformat(),
            application_dropped_packets=buffer.dropped,
            parse_errors=parse_errors,
            control_exchanges=device.exchanges,
            scale_factors=device.factors.copy(),
        )
        if parse_errors or buffer.dropped:
            store.metadata["status"] = "failed"
            error = error or AcquisitionError(
                "Packet errors occurred; inspect metadata and packets.jsonl"
            )
        store.flush()
        store.close()
        quality = diagnose(output, metadata=store.metadata)
        store.metadata["quality"] = quality
        if any(quality[s]["samples"] == 0 for s in ("acc", "gyro")):
            store.metadata["status"] = "failed"
            error = error or AcquisitionError("One or both IMU streams contain no samples")
        store.save_metadata()
        from .diagnostics import format_report

        print(format_report(quality))
    if error:
        raise AcquisitionError(f"{error}\nSaved session: {output}") from error
    if cancelled:
        LOG.warning("Recording interrupted; partial session finalized: %s", output)
        raise asyncio.CancelledError
    return output, quality
