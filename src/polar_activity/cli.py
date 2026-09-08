"""PowerShell-friendly commands with actionable errors and machine-readable reports."""

import argparse
import asyncio
import json
import logging
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

from bleak.exc import BleakError

from . import __version__
from .device import SenseDevice, discover, find_device
from .diagnostics import diagnose, format_report
from .protocol import AcquisitionError
from .recorder import record_session
from .storage import write_json


def positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Must be a finite positive number")
    return number


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Verity Sense VS-0 acquisition (no classifier)")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--verbose", action="store_true")
    commands = root.add_subparsers(dest="command", required=True)
    for command in ("scan", "verify", "record"):
        sub = commands.add_parser(command)
        sub.add_argument("--device", help="Exact Polar ID, advertised name or BLE identifier")
        sub.add_argument("--scan-timeout", type=positive, default=8)
        if command != "record":
            sub.add_argument("--json", action="store_true")
        if command == "verify":
            sub.add_argument("--output", type=Path, help="Save capability report as JSON")
        if command == "record":
            sub.add_argument("--duration", type=positive, default=300)
            sub.add_argument(
                "--output", type=Path, help="New session directory; must not already exist"
            )
            sub.add_argument("--subject", required=True)
            sub.add_argument("--sensor-position", required=True)
            sub.add_argument("--arm", choices=["left", "right", "unknown"], default=None)
            sub.add_argument("--notes", default="")
            sub.add_argument("--no-hr", action="store_true")
            sub.add_argument("--no-interactive", action="store_true")
    sub = commands.add_parser("diagnose")
    sub.add_argument("session", type=Path)
    sub.add_argument("--json", action="store_true")
    sub = commands.add_parser("plot")
    sub.add_argument("session", type=Path)
    selection = sub.add_mutually_exclusive_group()
    selection.add_argument("--set", dest="set_id")
    selection.add_argument("--activity")
    sub.add_argument("--start", type=float)
    sub.add_argument("--end", type=float)
    return root


async def ble_command(args: argparse.Namespace) -> None:
    if args.command == "scan":
        devices = await discover(args.device, args.scan_timeout)
        values = [dict(name=d.name, address=d.address) for d in devices]
        print(
            json.dumps(values, indent=2)
            if args.json
            else (
                "\n".join(f"{d.name} | {d.address}" for d in devices)
                or "No Verity Sense found. Turn on sensor mode; check Bluetooth and other apps."
            )
        )
        return
    if args.command == "record":
        output = args.output or Path("data/raw") / datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        inferred = next(
            (arm for arm in ("left", "right") if args.sensor_position.endswith("_" + arm)),
            "unknown",
        )
        arm = args.arm or inferred
        if inferred != "unknown" and arm != inferred:
            raise ValueError("--arm conflicts with --sensor-position")
        if output.exists():
            raise ValueError(f"Output already exists: {output}. Choose a new session directory.")
        if not args.subject.strip() or not args.sensor_position.strip():
            raise ValueError("Subject and sensor position must be non-empty")
        await record_session(
            output,
            args.subject,
            args.sensor_position,
            arm,
            args.duration,
            args.device,
            args.scan_timeout,
            not args.no_hr,
            not args.no_interactive,
            args.notes,
        )
        return
    selected = await find_device(args.device, args.scan_timeout)
    device = SenseDevice(selected, lambda _: None)
    try:
        await device.connect()
        report = await device.inspect()
        report["control_exchanges"] = device.exchanges
    finally:
        await device.close()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, report)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"{report['name']} | ID {report['polar_device_id']} | {report['address']}\n"
            f"Battery: {report['battery_percent']}% | Firmware: {report['firmware']}"
        )
        for stream, available in report["available_streams"].items():
            print(f"{stream.upper():5} {'yes' if available else 'no'}")
        for stream, settings in report["settings"].items():
            print(f"{stream.upper()}: {settings}")
        for warning in report["warnings"]:
            print(f"WARNING: {warning}")
        print("Capabilities queried; simultaneous streaming still requires a recording test.")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.command in ("scan", "verify", "record"):
            asyncio.run(ble_command(args))
        elif args.command == "diagnose":
            result = diagnose(args.session)
            print(json.dumps(result, indent=2) if args.json else format_report(result))
        else:
            from .plotting import plot_session

            for boundary in (args.start, args.end):
                if boundary is not None and not math.isfinite(boundary):
                    raise ValueError("Time boundaries must be finite")
            for path in plot_session(
                args.session, args.set_id, args.activity, args.start, args.end
            ):
                print(path)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except (AcquisitionError, BleakError, OSError, ValueError, TimeoutError) as exc:
        logging.debug("Command failed", exc_info=True)
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0
