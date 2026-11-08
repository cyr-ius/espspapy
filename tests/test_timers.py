"""Tests of the timer frames."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from espspapy import CommandError, Device, Spa, TimerSlot, build_timer_frame
from espspapy.cli import _set_timer, build_parser
from espspapy.helpers import checksum, with_checksum
from espspapy.spa import _slot
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


def sent_timer_frames(fake_spa: FakeSpa) -> list[str]:
    """Return the timer frames received by the fake spa."""
    frames = [str(msg["data"]) for msg in fake_spa.received if msg["type"] == 1]
    return [frame for frame in frames if frame.startswith("8888280F")]


async def test_heater_timer_like_the_panel(fake_spa: FakeSpa) -> None:
    """Heater in 2 hours for 3 hours at the current target (38 °C)."""
    async with Spa("127.0.0.1", fake_spa.port, heartbeat_interval=None) as spa:
        await spa.async_set_heater_timer(3, timedelta(hours=2))
        scheduled = spa.scheduled_timers
    (frame,) = sent_timer_frames(fake_spa)
    # Slot: 2 h 0 min before the start, 3 hours, day bit, 38 °C.
    assert frame.startswith("8888280F0A55" + "020003" + "0126")
    assert [slot.duration for slot in scheduled[Device.HEATER]] == [3]


async def test_heater_timer_fahrenheit(fake_spa: FakeSpa) -> None:
    """A °F target is sent in °C, like the mobile app."""
    fake_spa.target = 100
    async with Spa("127.0.0.1", fake_spa.port, heartbeat_interval=None) as spa:
        await spa.async_set_heater_timer(1, temperature=100)
    (frame,) = sent_timer_frames(fake_spa)
    assert frame[20:22] == "26"  # 100 °F = 38 °C


async def test_heater_timer_out_of_range(fake_spa: FakeSpa) -> None:
    """The target is checked against the spa unit before sending."""
    async with Spa("127.0.0.1", fake_spa.port, heartbeat_interval=None) as spa:
        with pytest.raises(CommandError):
            await spa.async_set_heater_timer(1, temperature=41)
    assert not sent_timer_frames(fake_spa)


async def test_filter_and_sanitizer_timers(fake_spa: FakeSpa) -> None:
    """Filter now for 4 hours, sanitizer in 30 minutes for 3 hours."""
    async with Spa("127.0.0.1", fake_spa.port, heartbeat_interval=None) as spa:
        await spa.async_set_filter_timer(4)
        await spa.async_set_sanitizer_timer(3, timedelta(minutes=30))
        assert set(spa.scheduled_timers) == {Device.FILTER, Device.SANITIZER}
    filter_frame, sanitizer_frame = sent_timer_frames(fake_spa)
    assert filter_frame.startswith("8888280F0955" + "0000040100")
    assert sanitizer_frame.startswith("8888280F0B55" + "001E030100")


async def test_clear_timer(fake_spa: FakeSpa) -> None:
    """Clearing sends an empty list and forgets the slots."""
    async with Spa("127.0.0.1", fake_spa.port, heartbeat_interval=None) as spa:
        await spa.async_set_filter_timer(2)
        await spa.async_clear_timer(Device.FILTER)
        assert spa.scheduled_timers == {}
    assert sent_timer_frames(fake_spa)[-1] == with_checksum("8888280F09" + "00" * 36)


def test_scheduled_timers_drop_ended_slots() -> None:
    """Slots that have ended are not reported."""
    spa = Spa("127.0.0.1")
    now = datetime.now().astimezone()
    ended = TimerSlot(now - timedelta(hours=3), 2)
    running = TimerSlot(now - timedelta(hours=1), 2)
    spa._timers = {Device.FILTER: (ended,), Device.HEATER: (running,)}
    assert spa.scheduled_timers == {Device.HEATER: (running,)}


def test_negative_delay() -> None:
    """A delay cannot be negative."""
    with pytest.raises(ValueError, match="Negative delay"):
        _slot(1, timedelta(minutes=-1))


async def test_cli_timer(fake_spa: FakeSpa) -> None:
    """espspapy timer: heater in 15 minutes, --temp refused for the filter."""
    parser = build_parser()
    heater = parser.parse_args(
        ["timer", "h", "heater", "--hours", "1", "--in", "15", "--temp", "28"]
    )
    wrong = parser.parse_args(["timer", "h", "filter", "--hours", "2", "--temp", "28"])
    async with Spa("127.0.0.1", fake_spa.port, heartbeat_interval=None) as spa:
        await _set_timer(spa, heater)
        with pytest.raises(ValueError, match="only for the heater"):
            await _set_timer(spa, wrong)
    (frame,) = sent_timer_frames(fake_spa)
    assert frame.startswith("8888280F0A55" + "000F01011C")
