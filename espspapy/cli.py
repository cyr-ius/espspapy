"""Command line interface: ``espspapy``."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import getpass
import json
import logging
import sys
import time

from .discovery import async_discover
from .esptouch import async_pair
from .exceptions import SpaException
from .helpers import get_current_bssid, get_local_ip
from .models import Device, SpaInfo, SpaStatus
from .spa import Spa

STATES = {"on": True, "off": False}


def _format_status(status: SpaStatus) -> str:
    unit = "°F" if status.fahrenheit else "°C"

    def state(value: bool, hours: float | None = None) -> str:
        timer = f" (timer {hours:g} h)" if hours is not None else ""
        return ("on" if value else "off") + timer

    def heater_state() -> str:
        text = state(status.heater, status.heater_hours)
        if status.heater:
            text += {True: " (heating)", False: " (idle)", None: " (heating?)"}[
                status.heating
            ]
        return text

    def temperature(value: int | None) -> str:
        return f"{value} {unit}" if value is not None else "?"

    lines = [
        f"Power        : {state(status.power)}",
        f"Filter       : {state(status.filter, status.filter_hours)}",
        f"Heater       : {heater_state()}",
        f"Jets         : {state(status.jets)}",
        f"Bubbles      : {state(status.bubbles)}",
        f"Sanitizer    : {state(status.sanitizer, status.sanitizer_hours)}",
        f"Temperature  : {temperature(status.current_temperature)}",
        f"Target       : {temperature(status.target_temperature)}",
    ]
    if status.error_code:
        lines.append(f"Error        : {status.error_code}")
    return "\n".join(lines)


def _print(value: SpaStatus | SpaInfo, as_json: bool) -> None:
    if as_json:
        print(json.dumps(asdict(value), ensure_ascii=False))
    elif isinstance(value, SpaStatus):
        print(_format_status(value))
    else:
        print(f"uid={value.uid} ip={value.ip} dtype={value.dtype}")


async def _run(args: argparse.Namespace) -> int:
    if args.command == "discover":
        spas = await async_discover(args.timeout)
        for info in spas:
            _print(info, args.json)
        return 0 if spas else 1

    if args.command == "pair":
        password = args.password
        if password is None:
            password = getpass.getpass(f"Wi-Fi password for {args.ssid}: ")
        local_ip = args.local_ip or get_local_ip()
        bssid = args.bssid or get_current_bssid()
        if bssid is None:
            print("No 2.4 GHz Wi-Fi connection here, use --bssid", file=sys.stderr)
            return 2
        print(f"Pairing {args.ssid} from {local_ip}…", file=sys.stderr)
        result = await async_pair(args.ssid, password, local_ip, bssid=bssid)
        print(f"Spa module {result.mac} joined with IP {result.ip}", file=sys.stderr)
        args.host = result.ip

    async with Spa(args.host) as spa:
        if args.command in ("pair", "identify"):
            _print(await spa.async_identify(), args.json)
        elif args.command == "status":
            _print(await spa.async_get_status(), args.json)
        elif args.command == "monitor":
            previous = None
            while True:
                status = await spa.async_get_status()
                if not args.changes or status.raw != previous:
                    previous = status.raw
                    if not args.json:
                        print(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')}")
                    _print(status, args.json)
                    sys.stdout.flush()
                await asyncio.sleep(args.interval)
        elif args.command == "temp":
            _print(await spa.async_set_temperature(args.value), args.json)
        elif args.command == "raw":
            _print(await spa.async_send_frame(args.frame), args.json)
        else:
            device = Device[args.command.upper()]
            if args.state == "toggle":
                status = await spa.async_toggle(device)
            else:
                status = await spa.async_set_device(device, STATES[args.state])
            _print(status, args.json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser."""
    parser = argparse.ArgumentParser(prog="espspapy", description="Control spas.")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logs")
    sub = parser.add_subparsers(dest="command", required=True)

    discover = sub.add_parser("discover", help="find the spas of the LAN")
    discover.add_argument("--timeout", type=float, default=3.0)

    pair = sub.add_parser("pair", help="connect a spa to the Wi-Fi (ESP-Touch)")
    pair.add_argument("--ssid", required=True, help="2.4 GHz network")
    pair.add_argument(
        "--bssid", help="2.4 GHz access point MAC address (detected if on Wi-Fi)"
    )
    pair.add_argument("--password", help="Wi-Fi password (prompted if omitted)")
    pair.add_argument("--local-ip", help="IP address of this host")

    for name, help_text in (
        ("identify", "show the spa identity"),
        ("status", "show the spa status"),
    ):
        sub.add_parser(name, help=help_text).add_argument("host")

    monitor = sub.add_parser("monitor", help="follow the status (Ctrl+C to stop)")
    monitor.add_argument("host")
    monitor.add_argument("--interval", type=float, default=30.0)
    monitor.add_argument(
        "--changes", action="store_true", help="print only when the status changes"
    )

    for device in Device:
        name = device.name.lower()
        command = sub.add_parser(name, help=f"switch {name}")
        command.add_argument("host")
        command.add_argument("state", choices=("on", "off", "toggle"))

    temp = sub.add_parser("temp", help="set the target temperature")
    temp.add_argument("host")
    temp.add_argument("value", type=int)

    raw = sub.add_parser("raw", help="send a hex frame (checksum added)")
    raw.add_argument("host")
    raw.add_argument("frame")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command line interface."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )
    try:
        return asyncio.run(_run(args))
    except (SpaException, ValueError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0 if args.command == "monitor" else 130


if __name__ == "__main__":
    sys.exit(main())
