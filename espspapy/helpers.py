"""Helpers for the spa protocol."""

from __future__ import annotations

import socket


def checksum(frame: str) -> str:
    """Return the checksum of a hex frame: ``255 - sum(bytes) % 255``."""
    return f"{255 - sum(bytes.fromhex(frame)) % 255:02X}"


def with_checksum(frame: str) -> str:
    """Return the hex frame followed by its checksum."""
    frame = frame.upper()
    return frame + checksum(frame)


def work_hours(value: int) -> float | None:
    """Decode a timer byte.

    Bit 7 means active, bits 0-6 count half hours. The spa sends ``0x80``
    when no timer is programmed.
    """
    half_hours = value & 0x7F
    return half_hours / 2 if value & 0x80 and half_hours else None


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
