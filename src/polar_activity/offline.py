"""Persistent sensor-memory sessions: start, disconnect, stop and verified download."""

import asyncio
import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from polar_python.constants import PmdMeasurementType

from .connection_log import ConnectionLog
from .device import SenseDevice, find_device
from .offline_data import export_recordings, parse_recording, recover_duplicate_blocks
from .pftp import FileTransfer
from .protocol import AcquisitionError, choose_config
from .storage import write_json

KINDS = {"acc": 2, "gyro": 5}
MANIFEST = "offline-session.json"


def now():
    return datetime.now(UTC).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def measurement_status(device) -> dict[str, dict]:
    payload = await device.command_raw(b"\x05")
    values = {}
    for value in payload:
        kind = value & 0x3F
        if kind in values:
            raise AcquisitionError("Duplicate type in PMD measurement status")
        values[kind] = {"online": bool(value & 0x40), "offline": bool(value & 0x80)}
    if not all(kind in values for kind in KINDS.values()):
        raise AcquisitionError("Measurement status does not include both ACC and gyro")
    return {stream: values[kind] for stream, kind in KINDS.items()}


async def stop_owned(device, manifest, save):
    status = await measurement_status(device)
    if any(v["online"] for v in status.values()):
        raise AcquisitionError("A live IMU recording is running; stop it in its owning app first")
    if any(status[s]["offline"] for s in manifest.get("stop_acknowledged", [])):
        raise AcquisitionError("A stopped stream was restarted; refusing to stop another session")
    owned = manifest.get("start_requests", [])
    for stream in reversed(owned):
        if stream not in KINDS:
            raise AcquisitionError("Invalid stream ownership in offline manifest")
        if status[stream]["offline"]:
            await device.command_raw(bytes([3, KINDS[stream]]))
            manifest.setdefault("stop_acknowledged", []).append(stream)
            save()
    deadline = time.monotonic() + 15
    while True:
        status = await measurement_status(device)
        if not any(v["offline"] or v["online"] for v in status.values()):
            break
        if time.monotonic() > deadline:
            raise AcquisitionError("Sensor has not confirmed IMU recording stopped; retry sync")
        await asyncio.sleep(0.25)
    manifest["state"] = "stopped"
    manifest.setdefault("stopped_utc", now())
    save()


def select_new_files(manifest, entries):
    old = {f["path"]: f["size"] for f in manifest["baseline_files"]}
    selected = []
    for entry in entries:
        if entry["path"] in old:
            if entry["size"] != old[entry["path"]]:
                raise AcquisitionError(
                    "A pre-existing sensor recording changed; refusing to mix sessions"
                )
        else:
            selected.append(entry)
    ordered = []
    for stream in KINDS:
        group = [e for e in selected if e["stream"] == stream]
        if not group:
            raise AcquisitionError(
                f"No new {stream.upper()} file found; keep sensor files and retry sync"
            )
        parents = {e["path"].rsplit("/", 1)[0] for e in group}
        if len(parents) != 1:
            raise AcquisitionError(
                f"Multiple new {stream.upper()} recordings found; cannot choose automatically"
            )

        def part(entry):
            match = re.fullmatch(r"(?:ACC|GYRO)(\d*)\.REC", entry["path"].rsplit("/", 1)[-1])
            if not match:
                raise AcquisitionError("Unexpected recording filename")
            return int(match.group(1) or 0)

        group.sort(key=part)
        if [part(e) for e in group] != list(range(len(group))):
            raise AcquisitionError("Missing or duplicate split recording file")
        if any(e["size"] <= 0 for e in group):
            raise AcquisitionError("An offline file is empty; wait briefly and retry sync")
        ordered.extend(group)
    return ordered


async def prepare_session(device, ftp, path: Path, manifest: dict):
    status = await measurement_status(device)
    if any(v["online"] or v["offline"] for v in status.values()):
        raise AcquisitionError(
            "ACC or gyro is already recording; refusing to take over that session"
        )
    manifest["device"] = await device.inspect()
    manifest["disk_before"] = await ftp.disk_space()
    if manifest["disk_before"]["free_bytes"] < 2 * 1024 * 1024:
        raise AcquisitionError(
            "Sensor has less than 2 MiB free; preserve/download existing recordings first"
        )
    manifest["configurations"] = {}
    for stream, kind in KINDS.items():
        settings = await device.command(bytes([1, kind | 0x80]))
        manifest["configurations"][stream] = asdict(choose_config(settings, stream))
    # Offline-record APIs in Polar's SDK use direct file GETs. Full Flow-style
    # sync notifications terminate/reconfigure the connection on this firmware.
    manifest["baseline_files"] = await ftp.list_recordings()
    manifest["state"] = "prepared"
    write_json(path / MANIFEST, manifest)


async def start_session(device, path: Path, manifest: dict):
    def save():
        return write_json(path / MANIFEST, manifest)

    # Check again after preflight, before claiming ownership of either stream.
    status = await measurement_status(device)
    if any(v["online"] or v["offline"] for v in status.values()):
        raise AcquisitionError("ACC or gyro is already recording; cannot start this session")
    manifest["state"] = "starting"
    manifest["start_requests"] = []
    save()
    acknowledged = []
    try:
        for stream, kind in KINDS.items():
            config = choose_config(
                {k: [v] for k, v in manifest["configurations"][stream].items()}, stream
            )
            request = bytearray(config.settings(PmdMeasurementType(kind)).to_bytes())
            request[1] |= 0x80
            manifest["start_requests"].append(stream)
            save()  # retain ownership intent even if the ACK/connection is lost
            await device.command_raw(bytes(request))
            acknowledged.append(stream)
        confirmed = await measurement_status(device)
        if not all(v["offline"] and not v["online"] for v in confirmed.values()):
            raise AcquisitionError("Both IMUs did not confirm internal recording")
        manifest["state"] = "recording"
        manifest["recording_confirmed_utc"] = now()
        manifest["confirmed_status"] = confirmed
        save()
    except BaseException:
        manifest["state"] = "start_uncertain" if device.poisoned else "start_failed"
        if not device.poisoned and device.client.is_connected:
            for stream in reversed(acknowledged):
                try:
                    await device.command_raw(bytes([3, KINDS[stream]]))
                    manifest.setdefault("stop_acknowledged", []).append(stream)
                except Exception as exc:
                    manifest.setdefault("cleanup_errors", []).append(str(exc))
        save()
        raise


async def sync_session(device, ftp, path, manifest):
    def save():
        return write_json(path / MANIFEST, manifest)

    await stop_owned(device, manifest, save)
    downloads = []
    entries = await ftp.list_recordings()
    selected = select_new_files(manifest, entries)
    manifest["selected_files"] = selected
    manifest["state"] = "downloading"
    save()
    for index, entry in enumerate(selected):
        print(f"Downloading {entry['path']} ({entry['size']} bytes)...", flush=True)
        data, verification = await verified_download(ftp, path, entry, manifest, save)
        local = f"sensor-files/{index:04d}-{entry['path'].rsplit('/', 1)[-1]}"
        target = path / local
        target.parent.mkdir(exist_ok=True)
        temporary = target.with_suffix(".REC.tmp")
        temporary.write_bytes(data)
        temporary.replace(target)
        downloads.append({**entry, **verification, "local_path": local, "sha256": sha(target)})
        manifest["downloads"] = downloads
        save()
    manifest["state"] = "decoding"
    save()
    metadata = export_recordings(path, manifest, downloads)
    manifest["state"] = "downloaded"
    manifest["downloaded_utc"] = now()
    manifest["export_status"] = metadata["status"]
    manifest["output_sha256"] = {
        name: sha(path / name)
        for name in ("acc.csv", "gyro.csv", "hr.csv", "packets.jsonl", "metadata.json")
    }
    save()
    return metadata


async def verified_download(ftp, path, entry, manifest, save):
    attempts = []

    async def read():
        error = None
        started = time.perf_counter()
        try:
            raw = await ftp.get(entry["path"])
        except (Exception, asyncio.CancelledError) as exc:
            error = exc
            if hasattr(ftp, "last_data"):
                raw = ftp.last_data
            else:
                raw = b"".join(p[1:] for p in getattr(ftp, "last_packets", []))
        elapsed = time.perf_counter() - started
        packets = list(getattr(ftp, "last_packets", []))
        folder = path / "transfer-attempts"
        folder.mkdir(exist_ok=True)
        token = f"{entry['stream']}-{uuid.uuid4().hex}"
        target = folder / f"{token}.received"
        target.write_bytes(raw)  # retain even a size mismatch or malformed file
        packet_path = folder / f"{token}.json"
        transport = getattr(ftp, "transport_name", "ble_rfc76")
        write_json(packet_path, {"transport": transport, "packets_hex": [p.hex() for p in packets]})
        info = {
            "path": entry["path"],
            "expected_bytes": entry["size"],
            "received_bytes": len(raw),
            "sha256": sha(target),
            "raw_path": target.relative_to(path).as_posix(),
            "transport_path": packet_path.relative_to(path).as_posix(),
            "transport": transport,
            "elapsed_s": elapsed,
            "bytes_per_s": len(raw) / elapsed if elapsed else None,
        }
        if error is not None:
            info["error"] = str(error) or type(error).__name__
        manifest.setdefault("download_attempts", []).append(info)
        attempts.append(info)
        save()
        if error is not None:
            raise error
        return raw, packets, info

    raw, packets, info = await read()
    if len(raw) == entry["size"]:
        parse_recording(raw, entry["stream"])
        return raw, {"verification": "reported_size_and_strict_decode", "attempts": attempts}
    if not getattr(ftp, "supports_duplicate_recovery", True):
        raise AcquisitionError(
            f"USB size mismatch: expected {entry['size']}, received {len(raw)}; "
            "raw transfer retained. Reconnect the adapter and retry."
        )
    data, removed = recover_duplicate_blocks(raw, packets, entry["size"], entry["stream"])
    info["duplicate_payload_indices"] = removed
    save()
    print("Repeated transfer blocks detected; verifying against a second download...", flush=True)
    repeated, repeated_packets, repeated_info = await read()
    if len(repeated) != entry["size"]:
        repeated, removed_again = recover_duplicate_blocks(
            repeated, repeated_packets, entry["size"], entry["stream"]
        )
        repeated_info["duplicate_payload_indices"] = removed_again
        save()
    if data != repeated:
        raise AcquisitionError("Independent downloads disagree; raw attempts preserved")
    return data, {
        "verification": "duplicate_blocks_removed_two_matching_reads",
        "attempts": attempts,
    }


async def run_offline(
    args, *, selected_device=None, device_factory=SenseDevice, ftp_factory=FileTransfer
):
    action = args.offline_command
    path = args.output if action == "start" else getattr(args, "session", None)
    manifest = None
    if action in ("stop", "sync"):
        manifest = json.loads((path / MANIFEST).read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or not isinstance(
            manifest.get("baseline_files"), list
        ):
            raise ValueError("Invalid offline session manifest")
        if "device" not in manifest or "start_requests" not in manifest:
            raise AcquisitionError("Session never reached the recording-start stage")
        if manifest["state"] == "downloaded":
            for source in manifest["downloads"]:
                if sha(path / source["local_path"]) != source["sha256"]:
                    raise AcquisitionError(
                        "A saved sensor file changed; existing session preserved"
                    )
            for name, expected in manifest["output_sha256"].items():
                if sha(path / name) != expected:
                    raise AcquisitionError(
                        "A downloaded output changed; existing session preserved"
                    )
            print(f"Session already downloaded and verified: {path}")
            return
    if action == "start":
        if not args.subject.strip() or not args.sensor_position.strip():
            raise ValueError("Subject and sensor position must be non-empty")
        path.mkdir(parents=True, exist_ok=False)
        arm = next(
            (a for a in ("left", "right") if args.sensor_position.endswith("_" + a)), "unknown"
        )
        manifest = {
            "schema_version": 1,
            "session_id": str(uuid.uuid4()),
            "started_utc": now(),
            "subject": args.subject,
            "sensor_position": args.sensor_position,
            "arm": arm,
            "notes": args.notes,
            "state": "preparing",
            "errors": [],
        }
        write_json(path / MANIFEST, manifest)
    selector = manifest["device"]["address"] if action in ("stop", "sync") else args.device
    device = None
    ftp = None
    connection_log = ConnectionLog(path / "connection.log") if path else None

    async def close_connection():
        nonlocal device, ftp
        if device:
            cleanup = await device.close()
            if manifest is not None:
                manifest.setdefault("connection_history", []).extend(device.connection_events)
                manifest.setdefault("pmd_exchanges", []).extend(device.exchanges)
                manifest.setdefault("cleanup_errors", []).extend(cleanup)
                if ftp:
                    manifest.setdefault("file_transfers", []).extend(ftp.exchanges)
            device, ftp = None, None

    try:
        selected = selected_device or await find_device(selector, args.scan_timeout)
        if (
            action in ("stop", "sync")
            and selected.address.lower() != manifest["device"]["address"].lower()
        ):
            raise AcquisitionError("Offline session belongs to a different sensor")
        device = device_factory(selected, lambda _: None, connect_timeout=args.connect_timeout)
        await device.connect()
        ftp = ftp_factory(device)
        if action != "stop":
            await ftp.connect()
        if action == "start":
            await prepare_session(device, ftp, path, manifest)
            await start_session(device, path, manifest)
            print(
                "Internal ACC + GYRO recording confirmed at 52 Hz.\n"
                f"You can leave Bluetooth range. Keep the sensor on.\nSession: {path}"
            )
        elif action == "stop":
            await stop_owned(device, manifest, lambda: write_json(path / MANIFEST, manifest))
            print(f"Internal recording stopped. Download with: offline sync {path}")
        elif action == "sync":
            metadata = await sync_session(device, ftp, path, manifest)
            print(
                f"Saved {metadata['quality']['acc']['samples']} ACC and "
                f"{metadata['quality']['gyro']['samples']} GYRO samples: {path}"
            )
            for warning in metadata["warnings"]:
                print(f"WARNING: {warning}")
        elif action == "status":
            result = {
                "measurements": await measurement_status(device),
                "disk": await ftp.disk_space(),
            }
            print(json.dumps(result, indent=2))
        else:
            status = await measurement_status(device)
            if any(v["online"] or v["offline"] for v in status.values()):
                raise AcquisitionError("Stop the active recording before listing its files")
            entries = await ftp.list_recordings()
            print(json.dumps(entries, indent=2))
    except BaseException as exc:
        if manifest is not None:
            manifest.setdefault("errors", []).append(
                {"time_utc": now(), "action": action, "error": str(exc) or type(exc).__name__}
            )
        raise
    finally:
        # Offline recordings are deliberately not registered in device.active:
        # disconnecting after start must leave them running in sensor memory.
        await close_connection()
        if manifest is not None:
            write_json(path / MANIFEST, manifest)
        if connection_log:
            connection_log.close()
