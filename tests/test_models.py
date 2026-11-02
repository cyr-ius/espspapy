"""Tests of helpers and status decoding (real frames captured on a spa)."""

from __future__ import annotations

from collections.abc import Callable
import ipaddress
import struct

import pytest

from espspapy import Device, InvalidFrameError, SpaInfo, SpaStatus
from espspapy.helpers import (
    checksum,
    get_current_bssid,
    get_local_ip,
    parse_bssid,
    with_checksum,
    work_hours,
)

FRAME_OFF_E81 = "FFFF110F010001B50000000000000000000028"
FRAME_HEATING = "FFFF110F010700270000000080808026000009"


def test_checksum() -> None:
    """Checksum of the status request and of a real answer."""
    assert checksum("8888060FEE0F01") == "DA"
    assert with_checksum("8888060fee0f01") == "8888060FEE0F01DA"
    assert checksum(FRAME_HEATING[:-2]) == FRAME_HEATING[-2:]


@pytest.mark.parametrize(
    ("value", "expected"), [(0x80, None), (0x00, None), (0x08, None), (0x88, 4.0)]
)
def test_work_hours(value: int, expected: float | None) -> None:
    """Timer byte: active bit and half hours."""
    assert work_hours(value) == expected


def test_parse_bssid() -> None:
    """BSSID with any separator."""
    assert parse_bssid("00:00:1A:2B:3C:4D") == bytes.fromhex("00001a2b3c4d")
    assert parse_bssid("00-00-1a-2b-3c-4d") == bytes.fromhex("00001a2b3c4d")
    with pytest.raises(ValueError):
        parse_bssid("00:00:1A")


def test_status_off_with_error() -> None:
    """Spa off, E81 flashing."""
    status = SpaStatus.from_frame(FRAME_OFF_E81)
    assert not status.power
    assert status.error_code == "E81"
    assert status.current_temperature is None
    assert status.target_temperature is None


def test_status_heating() -> None:
    """Spa on, filter and heater on, 39 °C for 38 °C."""
    status = SpaStatus.from_frame(FRAME_HEATING)
    assert status.power and status.filter and status.heater and status.heating
    assert not (status.jets or status.bubbles or status.sanitizer)
    assert status.current_temperature == 39
    assert status.target_temperature == 38
    assert not status.fahrenheit
    assert status.temperature_range == (10, 40)
    assert status.error_code is None
    assert status.filter_hours is None
    assert status.is_on(Device.FILTER)
    assert not status.is_on(Device.JETS)


def test_status_fahrenheit_and_standby_heater() -> None:
    """Target in °F, heater bit 6 (temperature maintained)."""
    frame = with_checksum("FFFF110F01410062000000008800006400" + "00")
    status = SpaStatus.from_frame(frame)
    assert status.fahrenheit
    assert status.target_temperature == 100
    assert status.current_temperature == 98
    assert status.heater and not status.heating
    assert status.filter_hours == 4.0


def test_status_end_error() -> None:
    """END error code."""
    frame = with_checksum("FFFF110F010101C80000000000000026" + "0000")
    assert SpaStatus.from_frame(frame).error_code == "END"


@pytest.mark.parametrize("frame", ["", "8888060FEE0F01DA", "FFFF110F0201" + "00" * 14])
def test_status_invalid(frame: str) -> None:
    """Frames that are not status reports."""
    with pytest.raises(InvalidFrameError):
        SpaStatus.from_frame(frame)


def test_spa_info() -> None:
    """Identity payload."""
    info = SpaInfo.from_dict({"uid": " ABC ", "ip": "192.168.1.10", "dtype": "spa"})
    assert info == SpaInfo(uid="ABC", ip="192.168.1.10", dtype="spa")
    with pytest.raises(InvalidFrameError):
        SpaInfo.from_dict({"ip": "192.168.1.10"})


def test_get_local_ip() -> None:
    """The local address is a valid IPv4 address."""
    ipaddress.IPv4Address(get_local_ip())


def _fake_ioctl(
    bssid: str, mantissa: int, exponent: int
) -> Callable[[int, int, bytes], bytes]:
    """Return an ioctl answering SIOCGIWAP and SIOCGIWFREQ."""

    def ioctl(fd: int, request: int, buffer: bytes) -> bytes:
        if request == 0x8B15:
            union = b"\x01\x00" + bytes.fromhex(bssid.replace(":", ""))
        else:
            union = struct.pack("=ih", mantissa, exponent)
        return buffer[:16] + union.ljust(16, b"\0")

    return ioctl


@pytest.mark.parametrize(
    ("bssid", "mantissa", "exponent", "expected"),
    [
        ("00:00:1a:2b:3c:4d", 2437, 6, "00:00:1a:2b:3c:4d"),  # 2437 MHz
        ("00:00:1a:2b:3c:4d", 6, 0, "00:00:1a:2b:3c:4d"),  # channel 6
        ("00:00:1a:2b:3c:4d", 5180, 6, None),  # 5 GHz
        ("00:00:1a:2b:3c:4d", 36, 0, None),  # channel 36
        ("00:00:00:00:00:00", 2437, 6, None),  # not associated
        ("44:44:44:44:44:44", 2437, 6, None),  # not associated
    ],
)
def test_get_current_bssid(
    monkeypatch: pytest.MonkeyPatch,
    bssid: str,
    mantissa: int,
    exponent: int,
    expected: str | None,
) -> None:
    """BSSID of the 2.4 GHz access point read from Wireless Extensions."""
    fcntl = pytest.importorskip("fcntl")
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(fcntl, "ioctl", _fake_ioctl(bssid, mantissa, exponent))
    assert get_current_bssid("wlan0") == expected


def test_get_current_bssid_no_wifi(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Wi-Fi interface or failing ioctl: no BSSID."""
    fcntl = pytest.importorskip("fcntl")

    def ioctl(fd: int, request: int, buffer: bytes) -> bytes:
        raise OSError(95, "Operation not supported")

    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(fcntl, "ioctl", ioctl)
    assert get_current_bssid("eth0") is None
    monkeypatch.setattr("espspapy.helpers._wireless_interfaces", list)
    assert get_current_bssid() is None
