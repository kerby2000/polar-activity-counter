"""Recording orchestration; BLE, storage and labels remain independently testable."""

import asyncio
import logging
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .connection_log import ConnectionLog
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
    connect_timeout: float = 30,
) -> tuple[Path, dict]:
    from .diagnostics import diagnose

    selected = selected_device or await find_device(selector, scan_timeout)
    buffer = PacketBuffer()
    device = device_factory(selected, buffer.put, connect_timeout=connect_timeout)
    store = SessionStore(output, subject, sensor_position, arm, notes)
    labels = Labels(output, store.elapsed)
    recon: dict[str, TimestampReconstructor] = {}
    configs = {}
    last_arrival: dict[str, int] = {}
    error: Exception | None = None
    parse_errors: list[str] = []
    cancelled = False
    sample_counts = {"acc": 0, "gyro": 0}
    packet_counts = {"acc": 0, "gyro": 0}
    stop_reason = "setup_error"
    last_loop_ns = None
    max_loop_interval_ms = 0.0
    connection_log = ConnectionLog(output / "connection.log")

    def process(packet: Packet) -> None:
        packet_id = store.packet(packet)  # Preserve bytes before attempting any interpretation.
        if packet.stream in configs:
            last, samples = decode_imu(
                packet.payload, configs[packet.stream], device.factors[packet.stream]
            )
            stamps, method = recon[packet.stream].reconstruct(last, len(samples))
            store.imu(packet, packet_id, last, stamps, samples, method)
            sample_counts[packet.stream] += len(samples)
            packet_counts[packet.stream] += 1
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
        print("Connecting and checking the sensor; wait for READY before exercising.", flush=True)
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
        store.metadata["scale_factor_sources"] = device.factor_sources.copy()
        store.metadata["control_exchanges"] = device.exchanges
        setup_finished = time.monotonic_ns()
        store.metadata["streaming_started_time_s"] = store.elapsed(setup_finished)
        store.metadata["requested_duration_s"] = duration
        store.save_metadata()
        print("Checking two valid packets from each of ACC and GYRO...", flush=True)
        while not all(count >= 2 for count in packet_counts.values()):
            if buffer.dropped or device.disconnected.is_set():
                raise AcquisitionError(
                    "Sensor disconnected or buffer overflow before recording ready"
                )
            for _ in range(min(buffer.queue.qsize(), 100)):
                process(buffer.queue.get_nowait())
            if all(count >= 2 for count in packet_counts.values()):
                break
            if time.monotonic_ns() - setup_finished > 10 * 10**9:
                missing = ", ".join(s.upper() for s, count in packet_counts.items() if count < 2)
                raise AcquisitionError(
                    f"Not enough valid {missing} packets during startup; do not exercise"
                )
            await asyncio.sleep(0.01)
        # Flush real samples before announcing readiness or accepting exercise labels.
        store.flush()
        stream_start = time.monotonic_ns()
        store.metadata["recording_ready_time_s"] = store.elapsed(stream_start)
        store.save_metadata()
        print(
            f"READY: receiving ACC and GYRO at configured 52 Hz "
            f"({sample_counts['acc']} / {sample_counts['gyro']} samples saved).",
            flush=True,
        )
        last_flush = time.monotonic()
        last_progress = last_flush
        with Keyboard(interactive) as keyboard:
            controller = LabelController(labels)
            print(
                f"Recording to {output}\n"
                + (HELP if keyboard.enabled else "Keyboard labels disabled.")
            )
            while (time.monotonic_ns() - stream_start) / 1e9 < duration:
                loop_ns = time.monotonic_ns()
                if last_loop_ns is not None:
                    max_loop_interval_ms = max(max_loop_interval_ms, (loop_ns - last_loop_ns) / 1e6)
                last_loop_ns = loop_ns
                stop_reason = "acquisition_error"
                if buffer.dropped:
                    stop_reason = "buffer_overflow"
                    raise AcquisitionError(
                        "Recorder buffer overflow; session stopped. Check disk/CPU load."
                    )
                if device.disconnected.is_set():
                    stop_reason = "disconnected"
                    raise AcquisitionError(
                        "Sensor disconnected. Check charge, distance and other apps; "
                        "partial session saved. Start a new session to continue."
                    )
                for _ in range(min(buffer.queue.qsize(), 100)):
                    process(buffer.queue.get_nowait())
                char = keyboard.read()
                if char is not None and controller.key(char):
                    stop_reason = "keyboard_finish"
                    break
                now = time.monotonic_ns()
                for stream in configs:
                    if now - last_arrival.get(stream, stream_start) > 10 * 10**9:
                        stop_reason = "notification_timeout"
                        raise AcquisitionError(
                            f"No {stream.upper()} data for 10s. Check device/connection; "
                            "partial data saved."
                        )
                if time.monotonic() - last_flush >= 1:
                    store.flush()
                    last_flush = time.monotonic()
                if time.monotonic() - last_progress >= 5 and not controller.prompt:
                    print(
                        f"Saved ACC {sample_counts['acc']} | GYRO {sample_counts['gyro']} samples",
                        flush=True,
                    )
                    last_progress = time.monotonic()
                await asyncio.sleep(0.01)
            else:
                stop_reason = "duration_reached"
        store.metadata["status"] = "complete"
    except asyncio.CancelledError:
        cancelled = True
        stop_reason = "cancelled"
        store.metadata["status"] = "interrupted"
    except Exception as exc:
        error = exc
        store.metadata["status"] = "failed"
        store.metadata["error"] = str(exc)
    finally:
        labels.close()
        termination_ns = time.monotonic_ns()
        store.metadata["recording_duration_s"] = store.elapsed(termination_ns)
        store.metadata["termination"] = {
            "reason": stop_reason,
            "detected_time_s": store.elapsed(termination_ns),
            "disconnected_before_cleanup": device.disconnected.is_set(),
            "buffered_packets": buffer.queue.qsize(),
            "max_recording_loop_interval_ms": max_loop_interval_ms,
            "last_notification_time_s": {
                stream: store.elapsed(stamp) for stream, stamp in device.last_notifications.items()
            },
            "last_notification_age_s": {
                stream: (termination_ns - stamp) / 1e9
                for stream, stamp in device.last_notifications.items()
            },
            "max_notification_gap_s": device.max_notification_gap_s.copy(),
        }
        try:
            stop_errors = await device.close()
        finally:
            connection_log.close()
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
            scale_factor_sources=device.factor_sources.copy(),
            connection_events=device.connection_events,
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
