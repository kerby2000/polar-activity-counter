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
    root = argparse.ArgumentParser(
        description="Verity Sense recording and personal activity analysis"
    )
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--verbose", action="store_true")
    commands = root.add_subparsers(dest="command", required=True)
    for command in ("scan", "verify", "record"):
        sub = commands.add_parser(command)
        sub.add_argument("--device", help="Exact Polar ID, advertised name or BLE identifier")
        sub.add_argument("--scan-timeout", type=positive, default=8)
        if command != "scan":
            sub.add_argument(
                "--connect-timeout",
                type=positive,
                default=30,
                help="Seconds allowed for connection and service discovery (default: 30)",
            )
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
    sub = commands.add_parser(
        "count", help="Automatically find repeated-motion sets and full cycles"
    )
    sub.add_argument("session", type=Path)
    sub.add_argument(
        "--output", type=Path, help="Derived output folder; default SESSION/automatic-count"
    )
    sub.add_argument("--json", action="store_true")
    sub.add_argument("--no-plot", action="store_true")
    sub = commands.add_parser(
        "train", help="Build a personal model from explicit reference intervals"
    )
    sub.add_argument("references", type=Path, help="JSON manifest of labelled reference intervals")
    sub.add_argument("--output", type=Path, default=Path("data/models/personal.json"))
    sub = commands.add_parser(
        "analyze", help="Recognize activities and estimate repetitions with a personal model"
    )
    sub.add_argument("session", type=Path)
    sub.add_argument("--model", type=Path, default=Path("data/models/personal.json"))
    sub.add_argument("--output", type=Path)
    sub.add_argument("--json", action="store_true")
    sub.add_argument("--no-plot", action="store_true")
    offline = commands.add_parser("offline", help="Record into sensor memory and download later")
    actions = offline.add_subparsers(dest="offline_command", required=True)
    for action in ("start", "status", "stop", "list", "sync"):
        sub = actions.add_parser(action)
        sub.add_argument("--scan-timeout", type=positive, default=8)
        sub.add_argument("--connect-timeout", type=positive, default=30)
        if action in ("stop", "sync"):
            sub.add_argument("session", type=Path, help="Session folder created by offline start")
        else:
            sub.add_argument("--device")
        if action == "start":
            sub.add_argument("--subject", required=True)
            sub.add_argument("--sensor-position", required=True)
            sub.add_argument("--notes", default="")
            sub.add_argument("--output", required=True, type=Path, help="New session directory")
    usb = commands.add_parser(
        "usb", help="Download sensor-memory recordings through the USB adapter"
    )
    actions = usb.add_subparsers(dest="usb_command", required=True)
    actions.add_parser("scan", help="List Polar USB HID interfaces")
    for action in ("list", "sync"):
        sub = actions.add_parser(action)
        sub.add_argument("--device", help="Exact USB serial, product name or path from usb scan")
        sub.add_argument(
            "--timeout", type=positive, default=10, help="USB idle reply timeout in seconds"
        )
        if action == "sync":
            sub.add_argument(
                "session", type=Path, help="Stopped or downloaded offline session folder"
            )
            sub.add_argument(
                "--output",
                required=True,
                type=Path,
                help="Separate USB copy folder; retry resumes verified files",
            )
        else:
            sub.add_argument("--output", type=Path, help="Save USB inventory/diagnostics as JSON")
            sub.add_argument("--disk", action="store_true", help="Also query sensor disk space")
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
            connect_timeout=args.connect_timeout,
        )
        return
    selected = await find_device(args.device, args.scan_timeout)
    device = SenseDevice(selected, lambda _: None, connect_timeout=args.connect_timeout)
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
        if args.command == "usb":
            from .usb_sync import run_usb

            asyncio.run(run_usb(args))
        elif args.command == "offline":
            from .offline import run_offline

            asyncio.run(run_offline(args))
        elif args.command in ("scan", "verify", "record"):
            asyncio.run(ble_command(args))
        elif args.command == "diagnose":
            result = diagnose(args.session)
            print(json.dumps(result, indent=2) if args.json else format_report(result))
        elif args.command == "train":
            from .recognition import fit_model

            result = fit_model(args.references, args.output)
            print(f"Saved personal model: {args.output}")
            print(
                f"Classes: {', '.join(result['classes'])}; "
                f"{len(result['examples'])} reference windows"
            )
        elif args.command == "analyze":
            from .analyser import analyse_session, format_analysis

            result = analyse_session(args.session, args.model, args.output, not args.no_plot)
            print(json.dumps(result, indent=2) if args.json else format_analysis(result))
        elif args.command == "count":
            from .counting import count_session, format_counts

            result = count_session(args.session, args.output, not args.no_plot)
            print(json.dumps(result, indent=2) if args.json else format_counts(result))
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
        detail = str(exc) or (
            "Operation timed out. Run with --verbose for the failing step."
            if isinstance(exc, TimeoutError)
            else type(exc).__name__
        )
        print(f"Error: {detail}", file=sys.stderr)
        return 1
    return 0
