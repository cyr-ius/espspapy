"""Tests of the TCP client against a fake spa."""

from __future__ import annotations

import asyncio

import pytest

from espspapy import (
    CommandError,
    Device,
    Spa,
    SpaConnectionError,
    SpaNotRespondingError,
)

from .conftest import UID, FakeSpa


def make_spa(fake_spa: FakeSpa, **kwargs: float | None) -> Spa:
    """Return a client for the fake spa."""
    options = {"retry_delay": 0.01, "heartbeat_interval": None} | kwargs
    return Spa("127.0.0.1", fake_spa.port, **options)  # type: ignore[arg-type]


async def test_status_and_identify(fake_spa: FakeSpa) -> None:
    """Read the status and the identity on a single connection."""
    async with make_spa(fake_spa) as spa:
        status = await spa.async_get_status()
        info = await spa.async_identify()
        assert spa.connected
    assert status.power and status.filter and not status.heater
    assert status.current_temperature == 37
    assert spa.last_status == status
    assert info.uid == UID
    assert fake_spa.connections == 1
    assert not spa.connected


async def test_set_device(fake_spa: FakeSpa) -> None:
    """Switch on only when needed (the spa only knows toggles)."""
    async with make_spa(fake_spa) as spa:
        assert (await spa.async_set_device(Device.BUBBLES, True)).bubbles
        assert (await spa.async_set_device(Device.BUBBLES, True)).bubbles
        assert not (await spa.async_set_device(Device.BUBBLES, False)).bubbles
        assert (await spa.async_toggle(Device.JETS)).jets
    toggles = [msg for msg in fake_spa.received if msg["data"].startswith("8888060F01")]
    assert len(toggles) == 3


async def test_device_refused_when_off(fake_spa: FakeSpa) -> None:
    """A device cannot be switched on while the spa is off."""
    async with make_spa(fake_spa) as spa:
        assert not (await spa.async_set_device(Device.POWER, False)).power
        with pytest.raises(CommandError):
            await spa.async_set_device(Device.HEATER, True)


async def test_set_temperature(fake_spa: FakeSpa) -> None:
    """Target temperature in range, refused out of range."""
    async with make_spa(fake_spa) as spa:
        assert (await spa.async_set_temperature(36)).target_temperature == 36
        with pytest.raises(CommandError):
            await spa.async_set_temperature(41)
    assert fake_spa.target == 36


async def test_retry_when_board_does_not_answer(fake_spa: FakeSpa) -> None:
    """``"result": "timeout"`` is retried, then raised."""
    fake_spa.not_responding = 2
    async with make_spa(fake_spa) as spa:
        assert (await spa.async_get_status()).power
        fake_spa.not_responding = 3
        with pytest.raises(SpaNotRespondingError):
            await spa.async_get_status()


async def test_reconnect_after_drop(fake_spa: FakeSpa) -> None:
    """The connection is reopened when the spa closes it."""
    async with make_spa(fake_spa) as spa:
        await spa.async_get_status()
        fake_spa.drop_next = True
        assert (await spa.async_get_status()).power
    assert fake_spa.connections == 2


async def test_connection_refused(fake_spa: FakeSpa) -> None:
    """An unreachable spa raises SpaConnectionError."""
    await fake_spa.stop()
    spa = make_spa(fake_spa, retries=2)
    with pytest.raises(SpaConnectionError):
        await spa.async_get_status()


async def test_heartbeat(fake_spa: FakeSpa) -> None:
    """Heartbeats are sent periodically and their answers ignored."""
    async with make_spa(fake_spa, heartbeat_interval=0.05) as spa:
        await asyncio.sleep(0.2)
        assert (await spa.async_get_status()).power
    heartbeats = [msg for msg in fake_spa.received if msg["type"] == 0]
    assert len(heartbeats) >= 2
    assert fake_spa.connections == 1


async def test_heartbeat_without_connection(fake_spa: FakeSpa) -> None:
    """No heartbeat before the first connection."""
    spa = make_spa(fake_spa)
    await spa.async_heartbeat()
    assert fake_spa.received == []
