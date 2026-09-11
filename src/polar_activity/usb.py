"""Read-only Polar USB HID transport. No FlowSync or device-file mutations.

The HID envelope is distinct from BLE RFC76. See docs/usb_recording.md for
protocol references and hardware validation status. Device GET requests and
transport acknowledgements are the only writes sent to USB.
"""

import asyncio
import threading
import time

from .pftp import MAX_FILE, FileTransfer, TransferError, protobuf
from .protocol import AcquisitionError

POLAR_VENDOR = 0x0DA4
REPORT_SIZE = 64
PAYLOAD_SIZE = 61


def hid_module():
    try:
        import hid
    except ImportError as exc:
        raise AcquisitionError(
            'USB support needs hidapi: run .\\.venv\\Scripts\\python.exe -m pip install -e ".[usb]"'
        ) from exc
    return hid


def enumerate_usb() -> list[dict]:
    return hid_module().enumerate(POLAR_VENDOR, 0)


def public_device(device: dict) -> dict:
    return {
        k: (v.decode("utf-8", "replace") if isinstance(v, bytes) else v) for k, v in device.items()
    }


def select_usb(devices: list[dict], selector: str | None = None) -> dict:
    candidates = [d for d in devices if d["vendor_id"] == POLAR_VENDOR]
    if selector:
        candidates = [
            d
            for d in candidates
            if selector.casefold()
            in {
                str(d.get("serial_number", "")).casefold(),
                str(d.get("product_string", "")).casefold(),
                public_device(d).get("path", "").casefold(),
            }
        ]
    if not candidates:
        raise AcquisitionError(
            "No matching Polar USB HID device. Enable USB in Polar Flow, "
            "then connect the sensor in its USB adapter; close FlowSync."
        )
    if len(candidates) != 1:
        raise AcquisitionError(
            "Multiple Polar USB interfaces found; use usb scan and --device SERIAL or PATH"
        )
    return candidates[0]


def usb_report(payload: bytes, sequence: int, more: bool = False) -> bytes:
    if len(payload) > PAYLOAD_SIZE:
        raise ValueError("USB report payload exceeds 61 bytes")
    header = bytes([1, ((len(payload) + 1) << 2) | int(more), sequence & 255])
    return (header + payload).ljust(REPORT_SIZE, b"\x00")


def parse_usb_report(report: bytes) -> tuple[int, bool, bytes]:
    # INW4J firmware 3.0.16 uses report ID 0x11 for device-to-host replies;
    # legacy Polar transports also return 0x01. Host requests/ACKs remain 0x01.
    if not 3 <= len(report) <= REPORT_SIZE or report[0] not in (0x01, 0x11):
        raise AcquisitionError("Invalid Polar USB report ID/length")
    size = (report[1] >> 2) - 1
    flag = report[1] & 3
    if flag not in (0, 1) or not 0 <= size <= PAYLOAD_SIZE or size + 3 > len(report):
        raise AcquisitionError("Malformed Polar USB report header")
    return report[2], bool(flag), report[3 : 3 + size]


class UsbFileTransfer(FileTransfer):
    """Reuse read-only filesystem operations with independent HID framing."""

    transport_name = "usb_hid"
    supports_duplicate_recovery = False

    def __init__(self, selected: dict, timeout: float = 10, *, handle=None):
        self.selected = selected
        self.timeout = timeout
        self.handle = handle
        self.lock = asyncio.Lock()
        self.cancel = threading.Event()
        self.poisoned = False
        self.exchanges = []
        self.last_packets = []
        self._assembled = bytearray()
        self._terminated = False
        self.progress = None

    @property
    def last_data(self):
        # Materialize only at completion/error, not once per 61-byte report.
        end = -1 if self._terminated else None
        return bytes(self._assembled[2:end])

    async def connect(self):
        if self.handle is None:
            handle = hid_module().device()
            try:
                handle.open_path(self.selected["path"])
            except OSError as exc:
                handle.close()
                raise AcquisitionError(
                    "Cannot open Polar USB device; close FlowSync and reconnect the adapter"
                ) from exc
            self.handle = handle

    async def close(self):
        async with self.lock:
            if self.handle is not None:
                self.handle.close()
                self.handle = None

    def _write_report(self, report: bytes):
        if self.cancel.is_set():
            raise AcquisitionError("USB transfer cancelled")
        written = self.handle.write(report)
        if written != len(report):
            raise AcquisitionError(f"Short USB write: {written}/{len(report)} bytes")

    def _read_report(self) -> bytes:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.cancel.is_set():
                raise AcquisitionError("USB transfer cancelled")
            value = bytes(self.handle.read(REPORT_SIZE, 100))
            if value:
                self.last_packets.append(value)
                return value
        raise AcquisitionError("USB reply timed out; close FlowSync and reconnect the adapter")

    def _transfer_sync(self, request: bytes, exchange: dict) -> bytes:
        # USB message termination is not the BLE RFC76 LAST flag.
        message = request + b"\x00"
        for index, offset in enumerate(range(0, len(message), PAYLOAD_SIZE)):
            chunk = message[offset : offset + PAYLOAD_SIZE]
            more = offset + PAYLOAD_SIZE < len(message)
            self._write_report(usb_report(chunk, index, more))
            if more:
                seq, continued, data = parse_usb_report(self._read_report())
                if seq != index % 256 or not continued or data:
                    raise AcquisitionError("USB request acknowledgement mismatch")
        assembled = self._assembled
        index = 0
        deadline = time.monotonic() + 3600
        next_progress = time.monotonic() + 5
        while time.monotonic() < deadline:
            seq, more, chunk = parse_usb_report(self._read_report())
            if seq != index % 256:
                raise AcquisitionError("USB response sequence mismatch; partial transfer retained")
            assembled.extend(chunk)
            if len(assembled) > MAX_FILE + 3 or index >= 1_000_000:
                raise AcquisitionError("USB transfer exceeds size/report limit")
            exchange["received_bytes"] = max(0, len(assembled) - (2 if more else 3))
            exchange["frames"] = index + 1
            if self.progress and time.monotonic() >= next_progress:
                self.progress(exchange["received_bytes"])
                next_progress = time.monotonic() + 5
            if not more:
                if len(assembled) < 3:
                    raise AcquisitionError("Truncated USB response envelope")
                status = int.from_bytes(assembled[:2], "little")
                if status:
                    raise TransferError(status)
                if assembled[-1] != 0:
                    raise AcquisitionError("Unsupported USB response terminator")
                self._terminated = True
                return self.last_data
            self._write_report(usb_report(b"", seq, more=True))
            index += 1
        raise AcquisitionError("USB transfer exceeded one-hour deadline")

    async def transfer(self, request: bytes) -> bytes:
        # Restrict even internal callers to GET and the read-only disk-space query.
        if request != b"\x05\x80":
            if len(request) < 2 or int.from_bytes(request[:2], "little") != len(request) - 2:
                raise ValueError("USB accepts only GET or disk-space requests")
            command = protobuf(request[2:])
            if (
                set(command) != {1, 2}
                or command.get(1) != [0]
                or len(command.get(2, [])) != 1
                or not isinstance(command[2][0], bytes)
            ):
                raise ValueError("USB device mutation commands are not supported")
        async with self.lock:
            if self.handle is None or self.poisoned:
                raise AcquisitionError("USB transport closed/desynchronized; reconnect")
            self.last_packets = []
            self._assembled = bytearray()
            self._terminated = False
            self.cancel.clear()
            exchange = {
                "transport": self.transport_name,
                "request_hex": request.hex(),
                "received_bytes": 0,
                "frames": 0,
            }
            self.exchanges.append(exchange)
            started = time.perf_counter()
            worker = asyncio.create_task(asyncio.to_thread(self._transfer_sync, request, exchange))
            try:
                return await asyncio.shield(worker)
            except BaseException as exc:
                self.poisoned = True
                self.cancel.set()
                try:
                    await asyncio.shield(worker)
                except BaseException:
                    pass
                exchange["error"] = str(exc) or type(exc).__name__
                raise
            finally:
                exchange["elapsed_s"] = time.perf_counter() - started
                exchange["bytes_per_s"] = exchange["received_bytes"] / exchange["elapsed_s"]

    async def inspect(self) -> dict:
        raw = await self.get("/DEVICE.BPB")
        fields = protobuf(raw)
        info = {"usb": public_device(self.selected), "device_info_hex": raw.hex()}
        for number, key in ((7, "model_name"), (8, "hardware_code")):
            if fields.get(number):
                info[key] = fields[number][0].decode("utf-8", "replace")
        return info
