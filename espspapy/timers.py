"""Timer frames (filter, heater and sanitizer schedules).

The controller has no clock: each slot is sent as a delay from the time of
sending, so a list must be sent again to stay accurate. Only one-shot slots are
supported; the layout of weekly slots is not confirmed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from .const import (
    TEMP_RANGE_CELSIUS,
    TIMER_FRAME_PREFIX,
    TIMER_LIST_CLEAR,
    TIMER_LIST_LEN,
    TIMER_LIST_MARKER,
    TIMER_MAX_DURATION,
    TIMER_MAX_SLOTS,
    TIMER_WINDOW_HOURS,
)
from .models import Device

TIMER_DEVICES = {Device.FILTER: 0x09, Device.HEATER: 0x0A, Device.SANITIZER: 0x0B}


@dataclass(frozen=True, slots=True)
class TimerSlot:
    """One-shot slot: start time, duration in hours, heater target in °C."""

    start: datetime
    duration: int
    temperature: int | None = None


def encode_slot(device: Device, slot: TimerSlot, now: datetime | None = None) -> bytes:
    """Encode a slot into 5 bytes, relative to ``now``.

    Bytes: hours before the start, minutes before the start (bit 7 for long
    slots), duration in hours, day bit, target temperature (heater only).
    A slot is long when it lasts 24 hours or more: its hours and day bit are
    then counted in weeks instead of days.
    """
    now = now or datetime.now(slot.start.tzinfo)
    minutes = int((slot.start - now).total_seconds() // 60)
    if minutes < 0:
        raise ValueError(f"Slot start {slot.start} is in the past")
    if not 1 <= slot.duration <= TIMER_MAX_DURATION:
        raise ValueError(
            f"Duration {slot.duration} out of range 1-{TIMER_MAX_DURATION} hours"
        )
    hours, minutes = divmod(minutes, 60)
    if hours + slot.duration >= TIMER_WINDOW_HOURS:
        raise ValueError(f"Slot must end within {TIMER_WINDOW_HOURS} hours")

    if device is Device.HEATER:
        low, high = TEMP_RANGE_CELSIUS
        if slot.temperature is None or not low <= slot.temperature <= high:
            raise ValueError(f"Heater slot needs a temperature in {low}-{high} °C")
        temperature = slot.temperature
    else:
        temperature = 0

    period = TIMER_WINDOW_HOURS if slot.duration >= 24 else 24
    long_flag = 0x80 if period == TIMER_WINDOW_HOURS else 0
    return bytes(
        (
            hours % period,
            minutes | long_flag,
            slot.duration,
            1 << (hours + slot.duration) // period,
            temperature,
        )
    )


def build_timer_frame(
    device: Device, slots: Sequence[TimerSlot], now: datetime | None = None
) -> str:
    """Return the frame (without checksum) replacing the timers of a device.

    An empty ``slots`` clears the timers of the device.
    """
    if device not in TIMER_DEVICES:
        raise ValueError(f"No timer for {device.name}")
    if len(slots) > TIMER_MAX_SLOTS:
        raise ValueError(f"At most {TIMER_MAX_SLOTS} slots per device")
    if slots:
        payload = bytes((TIMER_LIST_MARKER,)) + b"".join(
            encode_slot(device, slot, now) for slot in slots
        )
    else:
        payload = bytes((TIMER_LIST_CLEAR,))
    payload = payload.ljust(TIMER_LIST_LEN, b"\x00")
    return f"{TIMER_FRAME_PREFIX}{TIMER_DEVICES[device]:02X}{payload.hex().upper()}"
