"""One serialized BLE control channel and raw notification delivery."""

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from polar_python.constants import PmdMeasurementType, PolarCharacteristic

from .protocol import AcquisitionError, StreamConfig, conversion_factor, parse_settings

LOG = logging.getLogger(__name__)
CP = PolarCharacteristic.PMD_CONTROL_POINT.value
DATA = PolarCharacteristic.PMD_DATA.value
HR = PolarCharacteristic.HEART_RATE.value


@dataclass(frozen=True)
class Packet:
    stream: str
    payload: bytes
    host_monotonic_ns: int
    host_time_utc: str


async def discover(selector: str | None = None, timeout: float = 8) -> list[BLEDevice]:
    try:
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    except Exception as exc:
        raise AcquisitionError(
            f"BLE scan failed: {exc}. Enable Bluetooth in Windows Settings, check the "
            "adapter/driver and run from a plain PowerShell terminal."
        ) from exc
    matches = []
    for device, advert in found.values():
        name = advert.local_name or device.name or ""
        if "polar sense" not in name.lower() and "verity sense" not in name.lower():
            continue
        if selector and selector.lower() not in (
            name.lower(),
            device.address.lower(),
            name.split()[-1].lower(),
        ):
            continue
        device.name = name
        matches.append(device)
    return matches


async def find_device(selector: str | None, timeout: float) -> BLEDevice:
    devices = await discover(selector, timeout)
    if not devices:
        raise AcquisitionError(
            "Verity Sense not found. Turn it on in sensor/heart mode, unplug "
            "the charger, keep it nearby, and close phone/watch BLE connections. "
            "Check --device if supplied, then retry verify."
        )
    if len(devices) > 1:
        names = ", ".join(f"{d.name} ({d.address})" for d in devices)
        raise AcquisitionError(f"Multiple sensors found: {names}. Select one with --device ID.")
    return devices[0]


class SenseDevice:
    def __init__(
        self,
        device: BLEDevice,
        sink: Callable[[Packet], None],
        timeout: float = 10,
        client_factory: Callable = BleakClient,
        connect_timeout: float = 30,
    ):
        self.device = device
        self.sink = sink
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.disconnected = asyncio.Event()
        self.connection_events: list[dict] = []
        self.last_notifications: dict[str, int] = {}
        self.max_notification_gap_s: dict[str, float] = {}
        self.closing = False
        self.client = client_factory(
            device,
            disconnected_callback=self._disconnected,
            pair=False,
            timeout=connect_timeout,
            # Windows can retain stale characteristic/CCCD handles after pairing.
            # Ask the sensor for its current service table on every connection.
            winrt={"use_cached_services": False},
        )
        self.responses: asyncio.Queue[bytes] = asyncio.Queue()
        self.lock = asyncio.Lock()
        self.active: list[str] = []
        self.factors: dict[str, float] = {}
        self.factor_sources: dict[str, str] = {}
        self.exchanges: list[dict] = []
        self.poisoned = False

    def _event(self, event: str, **details) -> None:
        self.connection_events.append(
            {
                "event": event,
                "host_monotonic_ns": time.monotonic_ns(),
                "host_time_utc": datetime.now(UTC).isoformat(),
                **details,
            }
        )
        LOG.info("Connection event %s: %s", event, details)

    def _disconnected(self, _client: object) -> None:
        if not self.disconnected.is_set():
            self._event(
                "disconnected",
                during_cleanup=self.closing,
                reason="not supplied by Bleak callback; inspect connection.log",
            )
        self.disconnected.set()

    async def connect(self) -> None:
        self.closing = False
        self.disconnected.clear()
        stage = "Bluetooth connection and service discovery"
        limit = self.connect_timeout
        try:
            LOG.info("Connecting to %s (%s)", self.device.name, self.device.address)
            async with asyncio.timeout(limit):
                await self.client.connect()
            self._event("connected")
            stage = "PMD control indications"
            limit = self.timeout
            LOG.info("Enabling %s", stage)
            async with asyncio.timeout(limit):
                await self.client.start_notify(CP, self._control)
            stage = "PMD data notifications"
            LOG.info("Enabling %s", stage)
            async with asyncio.timeout(limit):
                await self.client.start_notify(DATA, self._data)
        except Exception as exc:
            await self.close()
            detail = str(exc) or type(exc).__name__
            if isinstance(exc, TimeoutError):
                detail = f"timed out after {limit:g}s"
            advice = "Check sensor power, sensor mode and other BLE connections; retry."
            if isinstance(exc, TimeoutError) and stage.startswith("Bluetooth"):
                advice += " Allow more discovery time with --connect-timeout 60."
            if getattr(exc, "winerror", None) in (-2147023673, 1223):
                advice = (
                    "Windows canceled the GATT request; this does not necessarily mean "
                    "you clicked Cancel. The application does not request re-pairing. "
                    "Close the Windows pairing dialog and other sensor apps, then "
                    "power-cycle the sensor and retry."
                )
            raise AcquisitionError(f"{stage} failed: {detail}. {advice}") from exc

    def _control(self, _sender: object, data: bytearray) -> None:
        if data and data[0] == 0xF0:
            self.responses.put_nowait(bytes(data))

    def _packet(self, stream: str, data: bytearray) -> None:
        mono = time.monotonic_ns()
        utc = datetime.now(UTC).isoformat()
        previous = self.last_notifications.get(stream)
        if previous is not None:
            self.max_notification_gap_s[stream] = max(
                self.max_notification_gap_s.get(stream, 0), (mono - previous) / 1e9
            )
        self.last_notifications[stream] = mono
        self.sink(Packet(stream, bytes(data), mono, utc))

    def _data(self, _sender: object, data: bytearray) -> None:
        stream = {2: "acc", 5: "gyro"}.get(data[0] & 0x3F, "unknown") if data else "unknown"
        self._packet(stream, data)

    async def command(self, request: bytes) -> dict[str, list[int]]:
        return parse_settings(await self.command_raw(request))

    async def command_raw(self, request: bytes) -> bytes:
        async with self.lock:
            if self.poisoned:
                raise AcquisitionError("PMD command channel timed out; reconnect before reuse")
            exchange: dict = {"request_hex": request.hex(), "response_hex": []}
            self.exchanges.append(exchange)
            try:
                async with asyncio.timeout(self.timeout):
                    await self.client.write_gatt_char(CP, request, response=True)
                    payload = bytearray()
                    while True:
                        answer = await self.responses.get()
                        exchange["response_hex"].append(answer.hex())
                        expected_type = request[1] & 0x3F if len(request) > 1 else None
                        if (
                            len(answer) < 4
                            or answer[:2] != bytes([0xF0, request[0]])
                            or (expected_type is not None and answer[2] & 0x3F != expected_type)
                        ):
                            self.poisoned = True
                            raise AcquisitionError(
                                "Unexpected PMD response; reconnect before reuse"
                            )
                        if answer[3]:
                            raise AcquisitionError(
                                f"PMD operation {request[0]} type {expected_type} "
                                f"rejected with code {answer[3]}. Stop other "
                                "online/offline recordings and use sensor mode."
                            )
                        payload.extend(answer[5:])
                        if len(payload) > 8192:
                            self.poisoned = True
                            raise AcquisitionError("PMD response exceeds size limit; reconnect")
                        if len(answer) == 4 or not answer[4]:
                            return bytes(payload)
            except asyncio.CancelledError:
                self.poisoned = True
                raise
            except TimeoutError as exc:
                self.poisoned = True
                raise AcquisitionError(
                    "PMD command timed out. Check sensor power, Bluetooth and "
                    "other connected apps; reconnect and retry."
                ) from exc

    async def inspect(self) -> dict:
        try:
            async with asyncio.timeout(self.timeout):
                features = bytes(await self.client.read_gatt_char(CP))
        except TimeoutError as exc:
            raise AcquisitionError(
                f"Reading PMD features timed out after {self.timeout:g}s. "
                "Check sensor power and other BLE connections, then retry."
            ) from exc
        if len(features) < 2 or features[0] != 0x0F:
            raise AcquisitionError("Unexpected PMD feature report")
        available = {
            k.name.lower(): bool(features[1] & (1 << k.value))
            for k in PmdMeasurementType
            if k.name != "RFU"
        }
        available["hr"] = self.client.services.get_characteristic(HR) is not None
        report = {
            "name": self.device.name,
            "address": self.device.address,
            "polar_device_id": None,
            "battery_percent": None,
            "firmware": None,
            "model": None,
            "available_streams": available,
            "settings": {},
            "feature_bytes_hex": features.hex(),
            "warnings": [],
        }
        match = re.search(r"\b([0-9A-Fa-f]{6,8})$", self.device.name or "")
        if match:
            report["polar_device_id"] = match.group(1)
        for field, uuid in [
            ("battery_percent", "2a19"),
            ("firmware_revision", "2a26"),
            ("software_revision", "2a28"),
            ("model", "2a24"),
        ]:
            try:
                async with asyncio.timeout(self.timeout):
                    value = bytes(
                        await self.client.read_gatt_char(f"0000{uuid}-0000-1000-8000-00805f9b34fb")
                    )
                report[field] = (
                    value[0] if field == "battery_percent" else value.decode().strip("\x00")
                )
            except Exception as exc:
                report["warnings"].append(f"{field} unavailable: {exc}")
        # Polar exposes its application version (as shown in Flow) via Software Revision.
        report["firmware"] = report.get("software_revision") or report.get("firmware_revision")
        report["firmware_source"] = "2a28" if report.get("software_revision") else "2a26"
        for kind in PmdMeasurementType:
            if kind.name in ("RFU", "PPI") or not available.get(kind.name.lower()):
                continue
            try:
                report["settings"][kind.name.lower()] = await self.command(bytes([1, kind.value]))
            except AcquisitionError as exc:
                report["warnings"].append(str(exc))
                if self.poisoned:
                    raise
        return report

    async def start(self, stream: str, config: StreamConfig) -> None:
        kind = PmdMeasurementType.ACC if stream == "acc" else PmdMeasurementType.GYRO
        response = await self.command(bytes(config.settings(kind).to_bytes()))
        self.active.append(stream)  # Stop even if a supplied factor is malformed.
        self.factors[stream] = conversion_factor(response)
        self.factor_sources[stream] = "device" if "factor" in response else "polar_sdk_default"

    async def start_hr(self) -> None:
        async with asyncio.timeout(self.timeout):
            await self.client.start_notify(HR, lambda _, data: self._packet("hr", data))
        self.active.append("hr")

    async def close(self) -> list[str]:
        self.closing = True
        self._event("cleanup_started", connected=self.client.is_connected)
        errors = []
        if self.poisoned:
            errors.append(
                "PMD channel desynchronized; stop acknowledgements unavailable. "
                "Power-cycle the sensor before the next session."
            )
        for stream in reversed(self.active):
            try:
                if not self.client.is_connected:
                    # The disconnect is already recorded; a stop-notify error here
                    # would be a cleanup consequence, not a second acquisition failure.
                    continue
                if stream == "hr":
                    async with asyncio.timeout(self.timeout):
                        await self.client.stop_notify(HR)
                elif not self.poisoned and self.client.is_connected:
                    await self.command(bytes([3, 2 if stream == "acc" else 5]))
            except Exception as exc:
                errors.append(f"Stopping {stream}: {exc}")
                LOG.warning("%s", errors[-1])
        self.active.clear()
        try:
            async with asyncio.timeout(self.timeout):
                await self.client.disconnect()
        except Exception as exc:
            errors.append(f"Disconnect: {exc}")
        return errors
