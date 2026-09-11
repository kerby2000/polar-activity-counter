"""USB inventory and verified copies of stopped sensor-memory sessions."""

import hashlib
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from .offline import MANIFEST, now, select_new_files, sha, verified_download
from .offline_data import export_recordings
from .protocol import AcquisitionError
from .storage import write_json
from .usb import UsbFileTransfer, enumerate_usb, public_device, select_usb

USB_MANIFEST = "usb-session.json"
EXPORTS = ("acc.csv", "gyro.csv", "hr.csv", "packets.jsonl", "metadata.json")


def load_source(session: Path) -> tuple[dict, str]:
    raw = (session / MANIFEST).read_bytes()
    source = json.loads(raw)
    required = ("session_id", "started_utc", "subject", "sensor_position", "arm", "notes", "device")
    if source.get("schema_version") != 1 or any(k not in source for k in required):
        raise ValueError("Invalid offline session manifest")
    if source.get("state") not in ("stopped", "downloading", "decoding", "downloaded"):
        raise AcquisitionError(
            "Stop the internal recording before docking: offline stop SESSION. "
            "USB download does not stop or start measurements."
        )
    return source, hashlib.sha256(raw).hexdigest()


def select_files(source: dict, entries: list[dict], selected: dict) -> list[dict]:
    """Previously downloaded paths are authoritative even when newer sessions exist."""
    known = {f["path"]: f for f in source.get("downloads", [])}
    expected = source.get("selected_files") or list(known.values())
    if expected:
        wanted = {e["path"] for e in expected}
        parents = {p.rsplit("/", 1)[0] for p in wanted}
        group = [e for e in entries if e["path"].rsplit("/", 1)[0] in parents]
        files = select_new_files({"baseline_files": []}, group)
        if {e["path"] for e in files} != wanted:
            raise AcquisitionError("Sensor files changed or are missing; source session preserved")
        sizes = {e["path"]: e["size"] for e in expected}
        if any(e["size"] != sizes[e["path"]] for e in files):
            raise AcquisitionError(
                "Sensor file sizes changed; finish recording before USB download"
            )
    else:
        files = select_new_files(source, entries)
    parents = {e["path"].rsplit("/", 1)[0] for e in files}
    if len(parents) > 1:
        # Sequential PMD starts can cross a second boundary (pullups-02).
        # Keep one new directory per stream and refuse unrelated start times.
        try:
            starts = [
                datetime.strptime(p.split("/")[-3] + p.split("/")[-1], "%Y%m%d%H%M%S")
                for p in parents
            ]
        except (ValueError, IndexError) as exc:
            raise AcquisitionError("Invalid sensor recording directory date") from exc
        if len(starts) != 2 or (max(starts) - min(starts)).total_seconds() > 1:
            raise AcquisitionError("ACC and GYRO do not belong to one sensor recording time")
    # With an existing reference, every file must match its SHA-256 after transfer.
    # For first downloads the USB serial must identify the BLE session's sensor.
    referenced = all(known.get(e["path"], {}).get("sha256") for e in files)
    serial = str(selected.get("serial_number") or "").casefold()
    polar_id = str(source["device"].get("polar_device_id") or "").casefold()
    if not referenced and (not polar_id or serial != polar_id):
        raise AcquisitionError(
            "Cannot match USB serial to the source sensor. Keep the files and save usb scan "
            "output for inspection, or download this session through BLE first."
        )
    return [{**e, "reference_sha256": known.get(e["path"], {}).get("sha256")} for e in files]


def checked_local(output: Path, relative: str) -> Path:
    target = (output / relative).resolve()
    if not target.is_relative_to(output.resolve()):
        raise ValueError("USB manifest contains a path outside its output directory")
    return target


def validate_saved(output: Path, manifest: dict):
    for item in manifest.get("downloads", []):
        if sha(checked_local(output, item["local_path"])) != item["sha256"]:
            raise AcquisitionError("A saved USB recording changed; choose a new output folder")
    for name, expected in manifest.get("output_sha256", {}).items():
        if sha(checked_local(output, name)) != expected:
            raise AcquisitionError("A saved USB export changed; choose a new output folder")


def prepare_output(output: Path, source: dict, source_hash: str, session: Path) -> dict:
    if output.exists():
        manifest_path = output / USB_MANIFEST
        if not manifest_path.is_file():
            raise ValueError("Output already exists and is not a USB download; choose a new folder")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("schema_version") != 1
            or manifest.get("source_manifest_sha256") != source_hash
        ):
            raise ValueError(
                "Output belongs to another or changed source session; choose a new folder"
            )
        validate_saved(output, manifest)
        return manifest
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1,
        "session_id": str(uuid.uuid4()),
        "reference_session_id": source["session_id"],
        "source_session": str(session.resolve()),
        "source_manifest_sha256": source_hash,
        **{
            k: source[k]
            for k in ("started_utc", "subject", "sensor_position", "arm", "notes", "device")
        },
        "download_transport": "usb_hid",
        "state": "preparing",
        "downloads": [],
        "errors": [],
    }
    write_json(output / USB_MANIFEST, manifest)
    return manifest


async def sync_usb(ftp, output: Path, manifest: dict, source: dict):
    def save():
        write_json(output / USB_MANIFEST, manifest)

    manifest["usb_device"] = public_device(ftp.selected)
    save()
    entries = await ftp.list_recordings()
    files = select_files(source, entries, ftp.selected)
    if manifest.get("selected_files") and manifest["selected_files"] != files:
        raise AcquisitionError("Sensor file selection changed since the previous USB attempt")
    manifest["selected_files"] = files
    manifest["state"] = "downloading"
    save()
    completed = {d["path"]: d for d in manifest["downloads"]}
    for index, entry in enumerate(files):
        if entry["path"] in completed:
            item = completed[entry["path"]]
            if item["size"] != entry["size"] or (
                entry["reference_sha256"] and item["sha256"] != entry["reference_sha256"]
            ):
                raise AcquisitionError("Previously saved USB file does not match this reference")
            print(f"Already verified: {entry['path']}", flush=True)
            continue
        print(f"USB downloading {entry['path']} ({entry['size']:,} bytes)...", flush=True)
        started = time.perf_counter()

        def progress(received, expected=entry["size"], began=started):
            elapsed = time.perf_counter() - began
            speed = received / elapsed if elapsed else 0
            remaining = max(0, expected - received) / speed if speed else None
            eta = f"; about {remaining:.0f}s remaining" if remaining is not None else ""
            print(
                f"USB {received:,}/{expected:,} bytes; {speed / 1024:.1f} KiB/s{eta}",
                file=sys.stderr,
                flush=True,
            )

        ftp.progress = progress
        try:
            data, verification = await verified_download(ftp, output, entry, manifest, save)
        finally:
            ftp.progress = None
        digest = hashlib.sha256(data).hexdigest()
        if entry["reference_sha256"] and digest != entry["reference_sha256"]:
            raise AcquisitionError(
                "USB bytes differ from the saved BLE reference; raw attempt retained"
            )
        local = f"sensor-files/{index:04d}-{entry['path'].rsplit('/', 1)[-1]}"
        target = output / local
        target.parent.mkdir(exist_ok=True)
        temporary = target.with_suffix(".REC.tmp")
        temporary.write_bytes(data)
        temporary.replace(target)
        manifest["downloads"].append(
            {
                **entry,
                **verification,
                "local_path": local,
                "sha256": digest,
                "matches_ble_reference": bool(entry["reference_sha256"]),
            }
        )
        save()
    # Never publish a complete export if the file listing changed during the copy.
    after = await ftp.list_recordings()
    sizes = {e["path"]: e["size"] for e in after}
    if any(sizes.get(e["path"]) != e["size"] for e in files):
        raise AcquisitionError(
            "Sensor files changed during USB download; retained copies are provisional"
        )
    if select_files(source, after, ftp.selected) != files:
        raise AcquisitionError(
            "Recording parts changed during USB download; retained copies are provisional"
        )
    manifest["state"] = "decoding"
    save()
    metadata = export_recordings(output, manifest, manifest["downloads"])
    metadata.update(download_transport="usb_hid", reference_session_id=source["session_id"])
    write_json(output / "metadata.json", metadata)
    attempts = [a for d in manifest["downloads"] for a in d["attempts"]]
    elapsed = sum(a["elapsed_s"] for a in attempts)
    useful = sum(d["size"] for d in manifest["downloads"])
    manifest.update(
        state="downloaded",
        downloaded_utc=now(),
        export_status=metadata["status"],
        transfer_summary={
            "useful_bytes": useful,
            "file_transfer_s": elapsed,
            "useful_bytes_per_s": useful / elapsed if elapsed else None,
            "all_match_ble_reference": all(
                d["matches_ble_reference"] for d in manifest["downloads"]
            ),
        },
        output_sha256={name: sha(output / name) for name in EXPORTS},
    )
    save()
    print(
        f"Saved {metadata['quality']['acc']['samples']} ACC and "
        f"{metadata['quality']['gyro']['samples']} GYRO samples: {output}"
    )
    speed = manifest["transfer_summary"]["useful_bytes_per_s"]
    if speed:
        print(f"File transfer: {useful:,} bytes in {elapsed:.2f}s ({speed / 1024:.1f} KiB/s)")
    if manifest["transfer_summary"]["all_match_ble_reference"]:
        print("USB files match the saved BLE reference byte for byte.")
    for warning in metadata["warnings"]:
        print(f"WARNING: {warning}")


async def run_usb(args, *, enumerator=None, ftp_factory=None):
    enumerator = enumerator or enumerate_usb
    ftp_factory = ftp_factory or UsbFileTransfer
    action = args.usb_command
    if action == "scan":
        devices = [public_device(d) for d in enumerator()]
        print(json.dumps(devices, indent=2))
        if not devices:
            print(
                "No Polar USB HID device. Enable USB in Polar Flow and connect the adapter.",
                file=sys.stderr,
            )
        return
    manifest = None
    output = getattr(args, "output", None)
    if action == "sync":
        source, source_hash = load_source(args.session)
        # Refuse conflicting outputs before opening hardware.
        if output.resolve() == args.session.resolve():
            raise ValueError("Use a separate USB output folder to preserve the source recording")
        manifest = prepare_output(output, source, source_hash, args.session)
        if manifest["state"] == "downloaded":
            print(f"USB session already downloaded and verified: {output}")
            return
    elif output and output.exists():
        raise ValueError("Report output already exists; choose a new filename")
    ftp = None
    report = {"transport": "usb_hid", "started_utc": now()}
    started = time.perf_counter()
    try:
        selected = select_usb(enumerator(), args.device)
        report["usb_device"] = public_device(selected)
        ftp = ftp_factory(selected, timeout=args.timeout)
        await ftp.connect()
        if action == "sync":
            await sync_usb(ftp, output, manifest, source)
        else:
            # Probe identity and directories using GET only. Disk query is optional
            # because its support on this USB firmware is not established yet.
            report["device_info"] = await ftp.inspect()
            report["recordings"] = await ftp.list_recordings()
            if args.disk:
                report["disk"] = await ftp.disk_space()
            print(json.dumps(report, indent=2))
    except BaseException as exc:
        report["error"] = str(exc) or type(exc).__name__
        if manifest is not None:
            manifest["errors"].append({"time_utc": now(), "error": report["error"]})
            manifest["state"] = "failed"
        raise
    finally:
        if ftp:
            try:
                await ftp.close()
            except Exception as exc:
                report["close_error"] = str(exc) or type(exc).__name__
            finally:
                report["file_transfers"] = ftp.exchanges
                report["last_packets_hex"] = [p.hex() for p in ftp.last_packets]
        report["elapsed_s"] = time.perf_counter() - started
        report["finished_utc"] = now()
        if manifest is not None:
            manifest.setdefault("connections", []).append(report)
            write_json(output / USB_MANIFEST, manifest)
        elif output:
            output.parent.mkdir(parents=True, exist_ok=True)
            write_json(output, report)
