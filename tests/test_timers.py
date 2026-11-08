"""Tests of the timer frames."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from espspapy import Device, Spa, TimerSlot, build_timer_frame
from espspapy.helpers import checksum
from espspapy.timers import encode_slot

from .conftest import FakeSpa

NOW = datetime(2026, 11, 1, 20, 0, tzinfo=UTC)


def test_short_slot() -> None:
    """Filter in 1 h 15 min for 4 hours: same day, no temperature."""
    slot = TimerSlot(NOW + timedelta(hours=1, minutes=15), 4)
    assert encode_slot(Device.FILTER, slot, NOW).hex() == "010f040100"


def test_short_slot_next_day() -> None:
    """Heater tomorrow 7:00 for 20 hours at 38 °C: day bit 1."""
    slot = TimerSlot(datetime(2026, 11, 2, 7, 0, tzinfo=UTC), 20, 38)
    assert encode_slot(Device.HEATER, slot, NOW).hex() == "0b00140226"


def test_long_slot() -> None:
    """Heater in 35 h 30 for 30 hours: counted in weeks, bit 7 on minutes."""
    slot = TimerSlot(NOW + timedelta(hours=35, minutes=30), 30, 37)
    assert encode_slot(Device.HEATER, slot, NOW).hex() == "239e1e0125"


@pytest.mark.parametrize(
    ("device", "slot"),
    [
        (Device.FILTER, TimerSlot(NOW - timedelta(minutes=1), 2)),
        (Device.FILTER, TimerSlot(NOW, 0)),
        (Device.FILTER, TimerSlot(NOW, 49)),
        (Device.FILTER, TimerSlot(NOW + timedelta(hours=160), 8)),
        (Device.HEATER, TimerSlot(NOW, 2)),
        (Device.HEATER, TimerSlot(NOW, 2, 41)),
    ],
)
def test_invalid_slot(device: Device, slot: TimerSlot) -> None:
    """Past start, duration, 7-day window and heater temperature are checked."""
    with pytest.raises(ValueError):
        encode_slot(device, slot, NOW)


def test_frame() -> None:
    """Prefix, device, list marker and zero padding to 36 bytes."""
    slots = [
        TimerSlot(NOW + timedelta(hours=1), 3),
        TimerSlot(NOW + timedelta(hours=13), 3),
    ]
    frame = build_timer_frame(Device.SANITIZER, slots, NOW)
    assert frame == "8888280F0B" + "55" + "0100030100" + "0D00030100" + "00" * 25
    assert len(bytes.fromhex(frame + checksum(frame))) == 0x28 + 2


def test_clear_frame() -> None:
    """An empty list clears the timers."""
    assert build_timer_frame(Device.FILTER, []) == "8888280F09" + "00" * 36


def test_invalid_frame() -> None:
    """Only filter, heater and sanitizer have timers, 7 slots at most."""
    with pytest.raises(ValueError):
        build_timer_frame(Device.BUBBLES, [])
    with pytest.raises(ValueError):
        build_timer_frame(Device.FILTER, [TimerSlot(NOW, 1)] * 8, NOW)


async def test_set_timers(fake_spa: FakeSpa) -> None:
    """The frame is sent with its checksum; a status answer is decoded."""
    slot = TimerSlot(datetime.now(UTC) + timedelta(hours=2), 4)
    async with Spa("127.0.0.1", fake_spa.port, heartbeat_interval=None) as spa:
        status = await spa.async_set_timers(Device.FILTER, [slot])
    assert status is not None
    sent = fake_spa.received[-1]["data"]
    assert sent.startswith("8888280F0955")
    assert checksum(sent[:-2]) == sent[-2:]


async def test_set_timers_without_answer(fake_spa: FakeSpa) -> None:
    """Like the real board: no answer to the timer frame, sent once, status read."""
    slot = TimerSlot(datetime.now(UTC) + timedelta(hours=2), 4)
    fake_spa.not_responding = 1
    async with Spa("127.0.0.1", fake_spa.port, heartbeat_interval=None) as spa:
        status = await spa.async_set_timers(Device.FILTER, [slot])
    assert status is not None
    sent = [msg["data"] for msg in fake_spa.received if msg["type"] == 1]
    assert [frame[:10] for frame in sent] == ["8888280F09", "8888060FEE"]
