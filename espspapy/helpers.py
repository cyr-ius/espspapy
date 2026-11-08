"""Helpers for the spa protocol."""

from __future__ import annotations

import contextlib
import os
import socket
import struct
import sys

from .const import IWREQ_SIZE, SIOCGIWAP, SIOCGIWFREQ, SYS_CLASS_NET


def checksum(frame: str) -> str:
    """Return the checksum of a hex frame: ``255 - sum(bytes) % 255``."""
    return f"{255 - sum(bytes.fromhex(frame)) % 255:02X}"


def with_checksum(frame: str) -> str:
    """Return the hex frame followed by its checksum."""
    frame = frame.upper()
    return frame + checksum(frame)


def work_hours(value: int) -> float | None:
    """Decode a timer byte.

    Bit 7 means active. Bits 0-6 drop by one every 30 minutes; the filter set
    from the control panel reads ``0x82`` (2 hours), ``0x86`` (4 hours) and
    ``0x8A`` (6 hours), so they are decoded as ``bits / 2 + 1`` remaining
    hours. The spa sends ``0x80`` when no timer is programmed.
    """
    bits = value & 0x7F
    return bits / 2 + 1 if value & 0x80 and bits else None


def parse_bssid(value: str) -> bytes:
    """Parse ``aa:bb:cc:dd:ee:ff`` (any separator) into 6 bytes."""
    cleaned = "".join(char for char in value if char in "0123456789abcdefABCDEF")
    if len(cleaned) != 12:
        raise ValueError(f"Invalid BSSID: {value}")
    return bytes.fromhex(cleaned)


def get_local_ip() -> str:
    """Return the IP address of this host on the LAN (nothing is sent).

    Connecting a UDP socket sends no packet: it only asks the OS which
    interface (and so which local address) would be used for that route.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("10.255.255.255", 1))
        return str(sock.getsockname()[0])


def _wireless_interfaces() -> list[str]:
    """Return the Wi-Fi interfaces of this host (Linux)."""
    try:
        names = sorted(os.listdir(SYS_CLASS_NET))
    except OSError:
        return []
    return [
        name
        for name in names
        if os.path.exists(os.path.join(SYS_CLASS_NET, name, "wireless"))
        or os.path.exists(os.path.join(SYS_CLASS_NET, name, "phy80211"))
    ]


def _iwreq(sock: socket.socket, request: int, interface: str) -> bytes:
    """Run a Wireless Extensions ioctl and return the ``iwreq`` union."""
    import fcntl  # Unix only

    buffer = interface.encode()[:15].ljust(IWREQ_SIZE, b"\0")
    return fcntl.ioctl(sock.fileno(), request, buffer)[16:]


def _is_2ghz(mantissa: int, exponent: int) -> bool:
    """Return True for a 2.4 GHz ``iw_freq`` (frequency or channel number)."""
    if exponent == 0 and mantissa <= 1000:
        return 1 <= mantissa <= 14
    return 2.4e9 <= mantissa * 10.0**exponent < 2.5e9


def get_current_bssid(interface: str | None = None) -> str | None:
    """Return the BSSID of the 2.4 GHz access point this host is connected to.

    This is what the phone apps send with ESP-Touch: the access point the phone
    is connected to. Only works on Linux, when this host is itself on Wi-Fi.
    Every Wi-Fi interface is tried unless ``interface`` is given. Returns
    ``None`` when no interface is connected to a 2.4 GHz access point (wired
    host, 5 GHz connection, other OS): the BSSID must then be asked for.
    """
    if not sys.platform.startswith("linux"):
        return None
    interfaces = [interface] if interface else _wireless_interfaces()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for name in interfaces:
            with contextlib.suppress(OSError):
                # struct sockaddr: family (2 bytes), then the MAC address.
                bssid = _iwreq(sock, SIOCGIWAP, name)[2:8]
                # struct iw_freq: mantissa (s32), exponent (s16).
                mantissa, exponent = struct.unpack(
                    "=ih", _iwreq(sock, SIOCGIWFREQ, name)[:6]
                )
                # Not associated: zero, broadcast or 44:44:… address.
                if len(set(bssid)) > 1 and _is_2ghz(mantissa, exponent):
                    return bssid.hex(":")
    return None
