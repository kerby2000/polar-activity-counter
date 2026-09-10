"""Narrow Polar file-transfer reader: RFC60/76 framing and protobuf wire fields.

Protocol reference: Polar BLE SDK 3d15da61 (see docs/offline_recording.md).
No device file deletion, firmware update, reset or arbitrary file writing.
"""

import asyncio
import re

from .protocol import AcquisitionError

MTU = "fb005c51-02e7-f387-1cad-8acd2d8df0c8"
D2H = "fb005c52-02e7-f387-1cad-8acd2d8df0c8"
MAX_FILE = 32 * 1024 * 1024


class TransferError(AcquisitionError):
    def __init__(self, code: int):
        self.code = code
        meaning = {103: "file not found", 202: "system busy; use sensor/heart mode"}.get(
            code, "see saved transfer log"
        )
        super().__init__(f"Polar file-transfer error {code}: {meaning}")


def varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("Unsigned protobuf value required")
    out = bytearray()
    while value >= 128:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def field(number: int, value: int | bytes) -> bytes:
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    return varint(number << 3 | 2) + varint(len(value)) + value


def protobuf(data: bytes) -> dict[int, list]:
    """Decode bounded fields, preserving repetitions and ignoring unknown field numbers."""
    offset = 0

    def read_int():
        nonlocal offset
        result = 0
        for shift in range(0, 70, 7):
            if offset >= len(data):
                raise AcquisitionError("Truncated protobuf integer")
            byte = data[offset]
            offset += 1
            if shift == 63 and byte > 1:
                raise AcquisitionError("Oversized protobuf integer")
            result |= (byte & 127) << shift
            if not byte & 128:
                return result
        raise AcquisitionError("Oversized protobuf integer")

    result: dict[int, list] = {}
    while offset < len(data):
        tag = read_int()
        number, wire = tag >> 3, tag & 7
        if not number:
            raise AcquisitionError("Invalid protobuf field")
        if wire == 0:
            value = read_int()
        else:
            size = read_int() if wire == 2 else {1: 8, 5: 4}.get(wire)
            if size is None or offset + size > len(data):
                raise AcquisitionError("Truncated/unsupported protobuf field")
            value = data[offset : offset + size]
            offset += size
        result.setdefault(number, []).append(value)
    return result


def frames(data: bytes, size: int = 20):
    for index, offset in enumerate(range(0, max(1, len(data)), size - 1)):
        chunk = data[offset : offset + size - 1]
        more = offset + size - 1 < len(data)
        yield bytes([(index % 16) << 4 | (index > 0) | (6 if more else 2)]) + chunk


class FileTransfer:
    def __init__(self, device, timeout: float = 30):
        self.device = device
        self.client = device.client
        self.timeout = timeout
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.lock = device.lock  # serialize file and PMD operations on this connection
        self.poisoned = False
        self.exchanges: list[dict] = []
        self.last_packets: list[bytes] = []

    async def connect(self):
        for uuid in (MTU, D2H):
            if self.client.services.get_characteristic(uuid) is None:
                raise AcquisitionError("Sensor does not expose Polar file transfer")
        async with asyncio.timeout(self.timeout):
            await self.client.start_notify(MTU, lambda _, data: self.queue.put_nowait(bytes(data)))
            await self.client.start_notify(D2H, lambda *_: None)

    async def _write(self, uuid, data):
        # ATT Write Request for every chunk: conservative pacing supported on Windows.
        for packet in frames(data):
            await self.client.write_gatt_char(uuid, packet, response=True)

    async def transfer(self, request: bytes) -> bytes:
        async with self.lock:
            self.last_packets = []
            if self.poisoned or not self.queue.empty():
                self.poisoned = True
                raise AcquisitionError("File-transfer channel desynchronized; reconnect")
            exchange = {"request_hex": request.hex(), "received_bytes": 0, "frames": 0}
            self.exchanges.append(exchange)
            try:
                async with asyncio.timeout(self.timeout):
                    await self._write(MTU, request)
                result = bytearray()
                index = 0
                while True:
                    async with asyncio.timeout(self.timeout):
                        packet = await self.queue.get()
                    self.last_packets.append(packet)
                    if not packet:
                        raise AcquisitionError("Empty RFC76 packet")
                    header = packet[0]
                    status = (header >> 1) & 3
                    if header >> 4 != index % 16 or header & 1 != (index > 0) or header & 8:
                        raise AcquisitionError("File-transfer sequence mismatch; retry download")
                    index += 1
                    exchange["frames"] = index
                    if status == 0:
                        if len(packet) != 3:
                            raise AcquisitionError("Malformed file-transfer response")
                        code = int.from_bytes(packet[1:], "little")
                        if code:
                            raise TransferError(code)
                        return bytes(result)
                    if status not in (1, 3):
                        raise AcquisitionError("Invalid RFC76 status")
                    result.extend(packet[1:])  # header-only MORE is legal
                    exchange["received_bytes"] = len(result)
                    if len(result) > MAX_FILE:
                        raise AcquisitionError("Sensor file exceeds the 32 MiB transfer limit")
                    if status == 1:
                        # Some firmware/Windows transfers repeat even the LAST
                        # payload with a new sequence number. Consume only exact
                        # terminal repeats, so they cannot contaminate the next GET.
                        while True:
                            try:
                                async with asyncio.timeout(min(0.2, self.timeout)):
                                    tail = await self.queue.get()
                            except TimeoutError:
                                break
                            self.last_packets.append(tail)
                            if (
                                not tail
                                or tail[0] != ((index % 16) << 4 | 3)
                                or tail[1:] != packet[1:]
                            ):
                                raise AcquisitionError("Unexpected data after file-transfer end")
                            index += 1
                            result.extend(tail[1:])
                            exchange["frames"] = index
                            exchange["received_bytes"] = len(result)
                            if len(result) > MAX_FILE:
                                raise AcquisitionError("Sensor file exceeds the transfer limit")
                        return bytes(result)
            except TransferError as exc:
                exchange["error"] = str(exc)
                raise
            except BaseException as exc:
                self.poisoned = True
                exchange["error"] = str(exc) or type(exc).__name__
                # Abort just this transfer. Never remove a file or stop a measurement.
                try:
                    async with asyncio.timeout(2):
                        await self.client.write_gatt_char(MTU, b"\x00\x00\x00", response=True)
                except Exception:
                    pass
                raise

    async def query(self, query_id: int) -> bytes:
        return await self.transfer((query_id | 0x8000).to_bytes(2, "little"))

    async def get(self, path: str) -> bytes:
        if not path.startswith("/") or ".." in path or "\\" in path or "\x00" in path:
            raise ValueError("Invalid sensor path")
        header = field(1, 0) + field(2, path.encode("ascii"))
        if len(header) >= 32768:
            raise ValueError("Sensor path too long")
        return await self.transfer(len(header).to_bytes(2, "little") + header)

    async def disk_space(self) -> dict:
        data = protobuf(await self.query(5))
        try:
            size, total, free = (data[n][0] for n in (1, 2, 3))
            if (
                not all(isinstance(v, int) for v in (size, total, free))
                or size <= 0
                or total <= 0
                or free > total
            ):
                raise ValueError()
            return {"total_bytes": size * total, "free_bytes": size * free}
        except (KeyError, ValueError, TypeError):
            raise AcquisitionError("Malformed sensor disk-space reply") from None

    async def list_recordings(self) -> list[dict]:
        pending = ["/U/0/"]
        files = []
        visited = set()
        while pending:
            path = pending.pop()
            if path in visited or len(visited) >= 2000:
                raise AcquisitionError("Invalid or excessively large sensor directory tree")
            visited.add(path)
            try:
                directory = protobuf(await self.get(path))
            except TransferError as exc:
                if exc.code == 103 and path == "/U/0/":
                    return []
                raise
            for entry in directory.get(1, []):
                fields = protobuf(entry)
                try:
                    name, size = fields[1][0].decode("ascii"), fields[2][0]
                except (KeyError, AttributeError, UnicodeError):
                    raise AcquisitionError("Malformed sensor directory entry") from None
                if not isinstance(size, int) or ".." in name or "\\" in name or "\x00" in name:
                    raise AcquisitionError("Unsafe sensor directory entry")
                if re.fullmatch(r"(?:\d{8}|R|\d{6})/", name):
                    pending.append(path + name)
                elif re.fullmatch(r"(?:ACC|GYRO)\d*\.REC", name):
                    if not re.fullmatch(r"/U/0/\d{8}/R/\d{6}/", path):
                        raise AcquisitionError("Unexpected offline recording directory")
                    files.append(
                        {
                            "path": path + name,
                            "size": size,
                            "stream": "acc" if name.startswith("ACC") else "gyro",
                        }
                    )
        return sorted(files, key=lambda f: f["path"])
