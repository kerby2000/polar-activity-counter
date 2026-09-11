"""USB wire fixtures and complete downloads through an injected HID device."""

import asyncio
import hashlib
import json
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_offline import rec_file

from polar_activity.cli import main, parser
from polar_activity.offline import MANIFEST, verified_download
from polar_activity.pftp import TransferError, field, protobuf
from polar_activity.protocol import AcquisitionError
from polar_activity.storage import write_json
from polar_activity.usb import (
    POLAR_VENDOR,
    UsbFileTransfer,
    parse_usb_report,
    select_usb,
    usb_report,
)
from polar_activity.usb_sync import USB_MANIFEST, run_usb, select_files

DEVICE = {
    "vendor_id": POLAR_VENDOR,
    "product_id": 1,
    "path": b"usb-test",
    "serial_number": "CF204722",
    "product_string": "Polar",
}
DIRECTORY = "/U/0/20260910/R/174112/"


def replies(data):
    # Independent fixture encoder: 2-byte status, data, terminator, then HID
    # framing described by PolarPacket/PolarResponse in polarusbdump.
    payload = b"\0\0" + data + b"\0"
    values = []
    for index, start in enumerate(range(0, len(payload), 61)):
        chunk = payload[start : start + 61]
        flag = int(start + 61 < len(payload))
        values.append(
            bytes([1, 4 * (len(chunk) + 1) + flag, index % 256]) + chunk + bytes(61 - len(chunk))
        )
    return values


class Hid:
    def __init__(self, files=None, packets=None):
        self.files = files or {}
        self.packets = deque(packets or [])
        self.writes = []
        self.request = bytearray()
        self.gets = []
        self.closed = False
        self.fail_path = None

    def write(self, report):
        self.writes.append(bytes(report))
        assert len(report) == 64 and report[0] == 1
        size = (report[1] >> 2) - 1
        if not size:
            assert report[1] == 5  # response ACK, no device file mutation
            return 64
        self.request.extend(report[3 : 3 + size])
        if report[1] & 1:
            self.packets.append(bytes([1, 5, report[2]]) + bytes(61))
        elif self.files:
            request = bytes(self.request)
            self.request.clear()
            assert request[-1] == 0
            command = protobuf(request[2:-1])
            assert command[1] == [0]  # only GET
            path = command[2][0].decode("ascii")
            self.gets.append(path)
            if path == self.fail_path:
                self.packets.extend(replies(self.files[path])[:1])
            else:
                self.packets.extend(replies(self.files[path]))
        return 64

    def read(self, length, timeout):
        assert length == 64 and timeout == 100
        return self.packets.popleft() if self.packets else []

    def close(self):
        self.closed = True


def filesystem():
    acc, gyro = rec_file(), rec_file(5)

    def directory(*entries):
        return b"".join(
            field(1, field(1, name.encode()) + field(2, size)) for name, size in entries
        )

    files = {
        "/DEVICE.BPB": field(7, b"Verity Sense") + field(8, b"INW4J"),
        "/U/0/": directory(("20260910/", 0)),
        "/U/0/20260910/": directory(("R/", 0)),
        "/U/0/20260910/R/": directory(("174112/", 0)),
        DIRECTORY: directory(("ACC.REC", len(acc)), ("GYRO.REC", len(gyro))),
        DIRECTORY + "ACC.REC": acc,
        DIRECTORY + "GYRO.REC": gyro,
    }
    return files


def source_session(tmp_path, *, referenced=True):
    session = tmp_path / "source"
    session.mkdir()
    files = filesystem()
    entries = [
        {"path": DIRECTORY + name, "size": len(files[DIRECTORY + name]), "stream": stream}
        for name, stream in (("ACC.REC", "acc"), ("GYRO.REC", "gyro"))
    ]
    source = {
        "schema_version": 1,
        "session_id": "reference-session",
        "started_utc": "2026-09-10T15:40:12+00:00",
        "subject": "sergey",
        "sensor_position": "upper_arm_left",
        "arm": "left",
        "notes": "test",
        "device": {"polar_device_id": "CF204722"},
        "state": "stopped",
        "baseline_files": [],
    }
    if referenced:
        source.update(
            state="downloaded",
            selected_files=entries,
            downloads=[
                {**e, "sha256": hashlib.sha256(files[e["path"]]).hexdigest()} for e in entries
            ],
        )
    write_json(session / MANIFEST, source)
    return session, source, files


def run_sync(session, output, hid):
    args = parser().parse_args(
        ["usb", "sync", str(session), "--output", str(output), "--timeout", "0.01"]
    )
    return asyncio.run(
        run_usb(
            args,
            enumerator=lambda: [DEVICE],
            ftp_factory=lambda selected, timeout: UsbFileTransfer(selected, timeout, handle=hid),
        )
    )


def test_known_wire_get_and_ack():
    async def check():
        hid = Hid(packets=replies(b"abc" * 30 + b"\0\0"))
        ftp = UsbFileTransfer(DEVICE, handle=hid)
        assert await ftp.get("/") == b"abc" * 30 + b"\0\0"
        # V800 generate_request layout for a one-byte path; not a round trip.
        assert hid.writes[0] == bytes.fromhex("01 24 00 05 00 08 00 12 01 2f 00") + bytes(53)
        assert hid.writes[1] == bytes.fromhex("01 05 00") + bytes(61)
        assert len(hid.writes) == 2  # no final ACK or extra report-ID byte
        assert ftp.last_data == b"abc" * 30 + b"\0\0"

    asyncio.run(check())


def test_verity_sense_captured_input_report_id():
    # First /DEVICE.BPB reply physically captured from INW4J/3.0.16 over USB.
    captured = bytes.fromhex(
        "11f90000000a0608001004180112060800100918051a0608031000181032084346323034373232"
        "3a0b506f6c617220494e57344a420b30303738343239322e30"
    )
    seq, more, payload = parse_usb_report(captured)
    assert seq == 0 and more and len(payload) == 61
    assert payload[:2] == b"\0\0"
    assert b"Polar INW4J" in payload

    async def check():
        packets = [b"\x11" + p[1:] for p in replies(b"a" * 70)]
        hid = Hid(packets=packets)
        ftp = UsbFileTransfer(DEVICE, handle=hid)
        assert await ftp.get("/") == b"a" * 70
        assert all(p[0] == 1 for p in hid.writes)

    asyncio.run(check())


def test_multifragment_request_exact_boundary_and_response_sequence_wrap():
    async def check():
        for length in (1, 53, 54, 180):
            path = "/" + "x" * length
            value = bytes(range(256)) * 100
            hid = Hid({path: value})
            ftp = UsbFileTransfer(DEVICE, handle=hid)
            assert await ftp.get(path) == value
            assert ftp.exchanges[-1]["frames"] > 256
            assert hid.gets == [path]

    asyncio.run(check())


@pytest.mark.parametrize("value", [b"", b"\0", b"a" * 58, b"a" * 59, b"a" * 61])
def test_payload_length_and_zero_termination(value):
    async def check():
        ftp = UsbFileTransfer(DEVICE, handle=Hid(packets=replies(value)))
        assert await ftp.get("/") == value

    asyncio.run(check())


@pytest.mark.parametrize(
    "packet",
    [b"", b"\x00\x04\x00", b"\x01\x00\x00", b"\x01\x06\x00", b"\x01\xfc\x00", b"\x01\x10\x00"],
)
def test_malformed_hid_header(packet):
    with pytest.raises(AcquisitionError):
        parse_usb_report(packet)
    with pytest.raises(ValueError):
        usb_report(bytes(62), 0)


@pytest.mark.parametrize("damage", ["sequence", "timeout", "terminator", "short", "status"])
def test_transfer_failure_poisoned_and_evidence_retained(damage):
    async def check():
        packets = replies(b"a" * 70)
        if damage == "sequence":
            packets[1] = packets[1][:2] + b"\x02" + packets[1][3:]
        elif damage == "timeout":
            packets.pop()
        elif damage == "terminator":
            packets = [usb_report(b"\0\0bad!", 0)]
        elif damage == "short":
            packets = [usb_report(b"\0\0", 0)]
        else:
            packets = [usb_report(b"\x2f\x01\0", 0)]
        hid = Hid(packets=packets)
        ftp = UsbFileTransfer(DEVICE, timeout=0.005, handle=hid)
        with pytest.raises(AcquisitionError):
            await ftp.get("/")
        assert ftp.poisoned and ftp.last_packets
        assert ftp.exchanges[-1]["error"]
        if damage in ("sequence", "timeout"):
            assert ftp.last_data == b"a" * 59
        with pytest.raises(AcquisitionError, match="desynchronized"):
            await ftp.get("/")
        await ftp.close()
        assert hid.closed

    asyncio.run(check())


def test_short_write_and_cancelled_download_close_worker():
    async def check():
        hid = Hid()
        hid.write = lambda _: 2
        ftp = UsbFileTransfer(DEVICE, handle=hid)
        with pytest.raises(AcquisitionError, match="Short USB write"):
            await ftp.get("/")
        hid = Hid()
        ftp = UsbFileTransfer(DEVICE, timeout=10, handle=hid)
        task = asyncio.create_task(ftp.get("/"))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await ftp.close()
        assert hid.closed and ftp.poisoned

    asyncio.run(check())


def test_only_polar_selected_and_ambiguous_interfaces_refused():
    keyboard = {**DEVICE, "vendor_id": 0x046D}
    assert select_usb([keyboard, DEVICE]) == DEVICE
    assert select_usb([DEVICE], "cf204722") == DEVICE
    with pytest.raises(AcquisitionError, match="No matching"):
        select_usb([keyboard])
    with pytest.raises(AcquisitionError, match="Multiple"):
        select_usb([DEVICE, {**DEVICE, "path": b"second"}])


def test_mutations_never_reach_hardware():
    async def check():
        hid = Hid()
        ftp = UsbFileTransfer(DEVICE, handle=hid)
        for request in (b"\x01\x80", b"bad", b"\x05\0\x08\x01\x12\x01/"):
            with pytest.raises(ValueError):
                await ftp.transfer(request)
        assert not hid.writes

    asyncio.run(check())


def test_complete_sync_uses_usb_and_preserves_source_and_exports(tmp_path):
    session, source, files = source_session(tmp_path)
    before = (session / MANIFEST).read_bytes()
    output = tmp_path / "usb"
    hid = Hid(files)
    run_sync(session, output, hid)
    manifest = json.loads((output / USB_MANIFEST).read_text())
    assert manifest["state"] == "downloaded"
    assert manifest["transfer_summary"]["all_match_ble_reference"]
    assert manifest["transfer_summary"]["useful_bytes_per_s"] > 0
    assert hid.closed and (session / MANIFEST).read_bytes() == before
    assert all(
        (output / d["local_path"]).read_bytes() == files[d["path"]] for d in manifest["downloads"]
    )
    metadata = json.loads((output / "metadata.json").read_text())
    assert metadata["download_transport"] == "usb_hid"
    assert metadata["quality"]["acc"]["samples"] == 156
    assert metadata["quality"]["gyro"]["samples"] == 156
    # A completed retry verifies disk hashes without touching hardware.
    idle = Hid(files)
    run_sync(session, output, idle)
    assert not idle.writes


def test_interrupted_sync_retains_partial_and_resumes_verified_files(tmp_path):
    session, _, files = source_session(tmp_path)
    output = tmp_path / "usb"
    hid = Hid(files)
    hid.fail_path = DIRECTORY + "GYRO.REC"
    with pytest.raises(AcquisitionError, match="timed out"):
        run_sync(session, output, hid)
    failed = json.loads((output / USB_MANIFEST).read_text())
    assert failed["state"] == "failed" and hid.closed
    assert len(failed["downloads"]) == 1
    assert failed["download_attempts"][-1]["received_bytes"] == 59
    assert not (output / "metadata.json").exists()
    hid = Hid(files)
    run_sync(session, output, hid)
    assert DIRECTORY + "ACC.REC" not in hid.gets
    assert DIRECTORY + "GYRO.REC" in hid.gets


@pytest.mark.parametrize("problem", ["hash", "size", "missing", "identity", "recording", "output"])
def test_conflicting_download_does_not_publish(tmp_path, problem):
    session, source, files = source_session(tmp_path, referenced=problem != "identity")
    output = tmp_path / "usb"
    if problem == "hash":
        source["downloads"][0]["sha256"] = "0" * 64
    elif problem == "size":
        source["selected_files"][0]["size"] += 1
    elif problem == "missing":
        files[DIRECTORY] = b""
    elif problem == "identity":
        source["device"]["polar_device_id"] = "OTHER"
    elif problem == "recording":
        source["state"] = "recording"
    elif problem == "output":
        output.mkdir()
        (output / "important.txt").write_text("keep")
    write_json(session / MANIFEST, source)
    with pytest.raises((AcquisitionError, ValueError)):
        run_sync(session, output, Hid(files))
    assert not (output / "metadata.json").exists()
    if problem == "hash":
        assert list((output / "transfer-attempts").glob("*.received"))
    if problem == "output":
        assert (output / "important.txt").read_text() == "keep"


def test_first_download_and_other_sessions_not_mixed(tmp_path):
    session, source, files = source_session(tmp_path, referenced=False)
    run_sync(session, tmp_path / "usb", Hid(files))
    entries = [
        {"path": DIRECTORY + "ACC.REC", "stream": "acc", "size": 100},
        {"path": DIRECTORY.replace("174112", "174212") + "GYRO.REC", "stream": "gyro", "size": 100},
    ]
    with pytest.raises(AcquisitionError, match="one sensor recording"):
        select_files(source, entries, DEVICE)


def test_usb_size_mismatch_preserved_without_ble_recovery(tmp_path):
    async def check():
        value = rec_file()
        ftp = UsbFileTransfer(DEVICE, handle=Hid(packets=replies(value)))
        manifest = {}
        with pytest.raises(AcquisitionError, match="USB size mismatch"):
            await verified_download(
                ftp,
                tmp_path,
                {"path": "/ACC.REC", "size": 1, "stream": "acc"},
                manifest,
                lambda: None,
            )
        assert len(manifest["download_attempts"]) == 1
        assert (tmp_path / manifest["download_attempts"][0]["raw_path"]).read_bytes() == value

    asyncio.run(check())


def test_usb_accepts_stream_starts_across_adjacent_seconds(tmp_path):
    _, source, _ = source_session(tmp_path, referenced=False)
    entries = [
        {"path": DIRECTORY + "ACC.REC", "stream": "acc", "size": 100},
        {"path": DIRECTORY.replace("174112", "174113") + "GYRO.REC", "stream": "gyro", "size": 100},
    ]
    selected = select_files(source, entries, DEVICE)
    assert len(selected) == 2
    assert selected[0]["stream"] == "acc" and selected[1]["stream"] == "gyro"


def test_cli_scan_and_list_without_bluetooth(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("polar_activity.usb_sync.enumerate_usb", lambda: [DEVICE])
    assert main(["usb", "scan"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["path"] == "usb-test"
    hid = Hid(filesystem())
    args = parser().parse_args(["usb", "list", "--output", str(tmp_path / "probe.json")])
    asyncio.run(
        run_usb(
            args,
            enumerator=lambda: [DEVICE],
            ftp_factory=lambda selected, timeout: UsbFileTransfer(selected, timeout, handle=hid),
        )
    )
    report = json.loads((tmp_path / "probe.json").read_text())
    assert report["device_info"]["model_name"] == "Verity Sense"
    assert len(report["recordings"]) == 2
    assert hid.closed


def test_probe_failure_saves_wire_diagnostics(tmp_path):
    output = tmp_path / "probe.json"
    hid = Hid(packets=[usb_report(b"\x2f\x01\0", 0)])
    args = SimpleNamespace(usb_command="list", output=output, device=None, timeout=0.01, disk=False)
    with pytest.raises(TransferError):
        asyncio.run(
            run_usb(
                args,
                enumerator=lambda: [DEVICE],
                ftp_factory=lambda selected, timeout: UsbFileTransfer(
                    selected, timeout, handle=hid
                ),
            )
        )
    report = json.loads(output.read_text())
    assert report["last_packets_hex"] and "303" in report["error"]
    assert hid.closed


def test_changed_file_during_download_is_not_published(tmp_path):
    session, _, files = source_session(tmp_path)
    hid = Hid(files)
    original_write = hid.write

    def write(report):
        result = original_write(report)
        if DIRECTORY + "GYRO.REC" in hid.gets:
            files[DIRECTORY] = b""
        return result

    hid.write = write
    output = tmp_path / "usb"
    with pytest.raises(AcquisitionError, match="changed during"):
        run_sync(session, output, hid)
    assert not (output / "metadata.json").exists()
    assert len(json.loads((output / USB_MANIFEST).read_text())["downloads"]) == 2


def test_modified_saved_recording_rejected_on_retry(tmp_path):
    session, _, files = source_session(tmp_path)
    output = tmp_path / "usb"
    run_sync(session, output, Hid(files))
    target = output / "sensor-files/0000-ACC.REC"
    target.write_bytes(b"changed")
    hid = Hid(files)
    with pytest.raises(AcquisitionError, match="saved USB recording changed"):
        run_sync(session, output, hid)
    assert not hid.writes and target.read_bytes() == b"changed"


def test_saved_real_pullup_bytes_replay_through_usb_transport(tmp_path):
    # Optional local acceptance fixture. Private recordings are never required by CI.
    session = Path(__file__).resolve().parents[1] / "data/raw/pullups-01"
    if not (session / MANIFEST).is_file():
        pytest.skip("Private pull-up reference is not present")
    source = json.loads((session / MANIFEST).read_text())
    before = hashlib.sha256((session / MANIFEST).read_bytes()).hexdigest()
    files = filesystem()
    entries = []
    for item in source["downloads"]:
        raw = (session / item["local_path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == item["sha256"]
        files[item["path"]] = raw
        entries.append(
            field(1, field(1, item["path"].rsplit("/", 1)[-1].encode()) + field(2, len(raw)))
        )
    files[DIRECTORY] = b"".join(entries)
    output = tmp_path / "replayed"
    run_sync(session, output, Hid(files))
    # Real bytes over a simulated HID endpoint, not a hardware speed measurement.
    for name in ("acc.csv", "gyro.csv", "packets.jsonl"):
        assert (output / name).read_bytes() == (session / name).read_bytes()
    metadata = json.loads((output / "metadata.json").read_text())
    assert metadata["quality"]["acc"]["samples"] == 3220
    assert metadata["quality"]["gyro"]["samples"] == 3220
    assert not metadata["warnings"]
    assert hashlib.sha256((session / MANIFEST).read_bytes()).hexdigest() == before
