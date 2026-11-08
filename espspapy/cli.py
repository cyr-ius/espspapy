"""Command line interface: ``espspapy``."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import timedelta
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
from .shell import async_shell
from .spa import Spa

STATES = {"on": True, "off": False}


def _format_status(status: SpaStatus) -> str:
    unit = "°F" if status.fahrenheit else "°C"

    def state(value: bool, hours: float | None = None) -> str:
        timer = f" (timer {hours:g} h)" if hours is not None else ""
        return ("on" if value else "off") + timer

    def heater_state() -> str:
        text = state(status.heater)
        if status.heater:
            text += {True: " (heating)", False: " (idle)", None: " (heating?)"}[
                status.heating
            ]
        return text

    raw = [status.raw[i : i + 2] for i in range(0, len(status.raw), 2)]

    def delay() -> str:
        hours = status.heater_delay_hours
        if hours is not None:
            return f"in {hours:g} h"
        # The last hour of a delay reads like no delay, unless already started.
        return "none" if status.heater else "none (or 1 h left)"

    def duration() -> str:
        hours = status.heater_hours
        if hours is not None:
            return f"{hours:g} h"
        started = status.heater or status.heater_delay_hours is not None
        # The last hour of a running duration reads like no duration.
        return "none (no end, or 1 h left)" if started else "none"

    def temperature(value: int | None) -> str:
        return f"{value} {unit}" if value is not None else "?"

    lines = [
        f"Power        : {state(status.power)}",
        f"Filter       : {state(status.filter, status.filter_hours)}",
        f"Heater       : {heater_state()}",
        f"Heater start : {delay()}  [bytes 9-10: {raw[9]} {raw[10]}]",
        f"Heater time  : {duration()}  [bytes 11, 14: {raw[11]} {raw[14]}]",
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


async def _set_timer(spa: Spa, args: argparse.Namespace) -> SpaStatus:
    device = Device[args.device.upper()]
    if args.clear:
        return await spa.async_clear_timer(device)
    if args.hours is None:
        raise ValueError("--hours or --clear is needed")
    delay = timedelta(minutes=args.delay)
    if device is Device.HEATER:
        return await spa.async_set_heater_timer(args.hours, delay, args.temp)
    if args.temp is not None:
        raise ValueError("--temp is only for the heater")
    if device is Device.FILTER:
        return await spa.async_set_filter_timer(args.hours, delay)
    return await spa.async_set_sanitizer_timer(args.hours, delay)


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

    if args.command == "shell":
        loop = asyncio.get_running_loop()

        async def read_line(prompt: str) -> str | None:
            if prompt:
                print(prompt, end="", flush=True)
            line = await loop.run_in_executor(None, sys.stdin.readline)
            return line or None

        def write(text: str) -> None:
            sys.stdout.write(text)
            sys.stdout.flush()

        print("Debug shell of the Wi-Fi module ('exit' or Ctrl+D to quit)")
        try:
            await async_shell(args.host, read_line, write)
        except OSError as err:
            print(f"Error: cannot connect to {args.host}: {err}", file=sys.stderr)
            return 1
        print()
        return 0

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
        elif args.command == "timer":
            _print(await _set_timer(spa, args), args.json)
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

    shell = sub.add_parser(
        "shell", help="interactive debug shell of the Wi-Fi module (port 23)"
    )
    shell.add_argument("host")

    timer = sub.add_parser(
        "timer",
        help="run the filter, heater or sanitizer for some hours, now or later",
    )
    timer.add_argument("host")
    timer.add_argument("device", choices=("filter", "heater", "sanitizer"))
    timer.add_argument("--hours", type=int, help="duration, 1-48 hours")
    timer.add_argument(
        "--in",
        dest="delay",
        type=int,
        default=0,
        metavar="MINUTES",
        help="start after this delay (default: now)",
    )
    timer.add_argument("--temp", type=int, help="heater target (default: current)")
    timer.add_argument("--clear", action="store_true", help="clear the timers")

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
        return 0 if args.command in ("monitor", "shell") else 130


if __name__ == "__main__":
    sys.exit(main())
