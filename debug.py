"""Debug script for the timer frames.

Without ``--send``, nothing leaves this host: the frames of the examples are
only printed. With ``--send``, they are sent to the module, and the status is
read before and after each frame (timer bytes 12-14)::

    python debug.py                                # print the frames
    python debug.py --host 192.168.1.10 --send     # send all the examples
    python debug.py --host 192.168.1.10 --send filter clear-filter
    python debug.py --host 192.168.1.10 --send -v filter-now clear-filter

Start with ``filter-now``: like the control panel, the filter should start at
once and the status should count down 2 hours in byte 12.

The timer frames come from the decompiled mobile client and were never tested
on a real device: watch the device, and clear the timers when done
(``clear-filter``, ``clear-heater``, ``clear-sanitizer``).
"""

import argparse
import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import logging

from espspapy import Device, Spa, SpaException, SpaStatus, TimerSlot, build_timer_frame
from espspapy.helpers import with_checksum

type Example = tuple[Device, list[TimerSlot], str]


def _in(hours: float) -> datetime:
    """Return a start time ``hours`` from now, rounded up to the minute.

    Delays are truncated to the minute when encoded: rounding up keeps the
    encoded delay equal to ``hours``.
    """
    start = datetime.now() + timedelta(hours=hours, seconds=59)
    return start.replace(second=0, microsecond=0)


def _tomorrow_at(hour: int) -> datetime:
    start = datetime.now() + timedelta(days=1)
    return start.replace(hour=hour, minute=0, second=0, microsecond=0)


EXAMPLES: dict[str, Callable[[], Example]] = {
    # Immediate start, like the control panel: byte 12 should become 0x84.
    "filter-now": lambda: (
        Device.FILTER,
        [TimerSlot(_in(0), 2)],
        "filter now for 2 hours (expected byte 12: 84)",
    ),
    # Byte 13 should become 0x86.
    "sanitizer-now": lambda: (
        Device.SANITIZER,
        [TimerSlot(_in(0), 3)],
        "sanitizer now for 3 hours (expected byte 13: 86)",
    ),
    "filter": lambda: (
        Device.FILTER,
        [TimerSlot(_in(0.25), 2)],
        "filter in 15 minutes for 2 hours",
    ),
    "filter-two": lambda: (
        Device.FILTER,
        [TimerSlot(_in(1), 2), TimerSlot(_in(13), 2)],
        "filter in 1 hour and in 13 hours, 2 hours each",
    ),
    "heater": lambda: (
        Device.HEATER,
        [TimerSlot(_tomorrow_at(7), 3, 38)],
        "heater tomorrow at 7:00 for 3 hours at 38 °C",
    ),
    "heater-long": lambda: (
        Device.HEATER,
        [TimerSlot(_in(12), 30, 37)],
        "heater in 12 hours for 30 hours at 37 °C",
    ),
    "sanitizer": lambda: (
        Device.SANITIZER,
        [TimerSlot(_in(0.5), 3)],
        "sanitizer in 30 minutes for 3 hours",
    ),
    "clear-filter": lambda: (Device.FILTER, [], "clear the filter timers"),
    "clear-heater": lambda: (Device.HEATER, [], "clear the heater timers"),
    "clear-sanitizer": lambda: (Device.SANITIZER, [], "clear the sanitizer timers"),
}


def timers(status: SpaStatus) -> str:
    """Return the timer bytes of a status, decoded and raw."""
    raw = status.raw[24:30]
    return (
        f"filter={status.filter_hours} h sanitizer={status.sanitizer_hours} h "
        f"heater={status.heater_hours} h (bytes 12-14: {raw})"
    )


async def send(host: str, names: list[str]) -> None:
    """Send the examples to the module and print the status around them."""
    async with Spa(host) as spa:
        print(f"Before        : {timers(await spa.async_get_status())}")
        for name in names:
            device, slots, label = EXAMPLES[name]()
            print(f"\n{name}: {label}")
            status = await spa.async_set_timers(device, slots)
            print(f"Answer        : {timers(status) if status else 'not a status'}")
            await asyncio.sleep(2)
            print(f"Status        : {timers(await spa.async_get_status())}")


def main() -> None:
    """Parse the arguments and print or send the examples."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("examples", nargs="*", choices=list(EXAMPLES))
    parser.add_argument("--host", help="IP address of the module")
    parser.add_argument("--send", action="store_true", help="send to the module")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logs")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    if args.send and not args.host:
        parser.error("--send needs --host")

    names = args.examples or [name for name in EXAMPLES if "clear" not in name]
    for name in names:
        device, slots, label = EXAMPLES[name]()
        frame = with_checksum(build_timer_frame(device, slots))
        print(f"{name:16}{label}\n{'':16}{frame}")

    if not args.send:
        print("\nNothing sent: add --host and --send to send these frames.")
        return
    try:
        asyncio.run(send(args.host, names))
    except SpaException as err:
        raise SystemExit(f"Error: {err}") from err


if __name__ == "__main__":
    main()
