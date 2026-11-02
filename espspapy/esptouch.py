"""Wi-Fi pairing of a spa with ESP-Touch v1.

The Wi-Fi module of the spa is an Espressif chip. Before joining a network it
sniffs the air and decodes SSID, BSSID and password from the *length* of UDP
broadcast packets (``com.espressif.iot.esptouch``). Once connected, it answers
with its MAC and IP address on UDP port 18266.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import ipaddress
import logging
import socket

from .const import (
    BROADCAST_ADDR,
    ESPTOUCH_DATA_DURATION,
    ESPTOUCH_DATA_OFFSET,
    ESPTOUCH_GUIDE_CODES,
    ESPTOUCH_GUIDE_DURATION,
    ESPTOUCH_LISTEN_PORT,
    ESPTOUCH_MAX_INDEX,
    ESPTOUCH_PACKET_INTERVAL,
    ESPTOUCH_PAYLOAD_BYTE,
    ESPTOUCH_RECEIVE_TIMEOUT,
    ESPTOUCH_RESULT_LEN,
    ESPTOUCH_SEND_TIMEOUT,
    ESPTOUCH_TARGET_PORT,
    ESPTOUCH_UNKNOWN_BSSID,
)
from .exceptions import PairingError
from .helpers import get_local_ip, parse_bssid
from .models import PairingResult

_LOGGER = logging.getLogger(__name__)


def _build_crc8_table() -> tuple[int, ...]:
    table = []
    for value in range(256):
        crc = value
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if crc & 1 else crc >> 1
        table.append(crc)
    return tuple(table)


_CRC8_TABLE = _build_crc8_table()


def crc8(data: bytes) -> int:
    """Return the CRC8 Dallas/Maxim (reflected polynomial 0x8C, ``CRC8``)."""
    crc = 0
    for byte in data:
        crc = _CRC8_TABLE[(byte ^ crc) & 0xFF]
    return crc


def _data_code(value: int, index: int) -> tuple[int, int, int]:
    """Encode a byte and its index into three 9-bit words (``DataCode``)."""
    if index > ESPTOUCH_MAX_INDEX:
        raise ValueError("SSID and password too long for ESP-Touch")
    crc = crc8(bytes((value, index)))
    return (
        (crc & 0xF0) | (value >> 4),
        0x100 | index,
        ((crc & 0x0F) << 4) | (value & 0x0F),
    )


def build_datum_lengths(
    ssid: bytes, bssid: bytes, password: bytes, local_ip: bytes
) -> list[int]:
    """Return the packet lengths of the data code (``DatumCode``)."""
    total_len = 5 + len(local_ip) + len(password) + len(ssid)
    ssid_crc = crc8(ssid)
    bssid_crc = crc8(bssid)

    entries = [(total_len, 0), (len(password), 1), (ssid_crc, 2), (bssid_crc, 3)]
    checksum = total_len ^ len(password) ^ ssid_crc ^ bssid_crc
    for offset, byte in enumerate(local_ip + password + ssid):
        checksum ^= byte
        entries.append((byte, 5 + offset))
    entries.insert(4, (checksum, 4))

    # BSSID bytes are interleaved every 4 entries.
    position = 5
    for offset, byte in enumerate(bssid):
        entries.insert(min(position, len(entries)), (byte, total_len + offset))
        position += 4

    return [
        word + ESPTOUCH_DATA_OFFSET
        for value, index in entries
        for word in _data_code(value, index)
    ]


def build_packets(
    ssid: str, password: str, local_ip: str, bssid: str | None = None
) -> tuple[list[bytes], list[bytes]]:
    """Return the guide and data packets to broadcast."""
    parsed_bssid = parse_bssid(bssid) if bssid else ESPTOUCH_UNKNOWN_BSSID
    guide = [ESPTOUCH_PAYLOAD_BYTE * length for length in ESPTOUCH_GUIDE_CODES]
    datum = [
        ESPTOUCH_PAYLOAD_BYTE * length
        for length in build_datum_lengths(
            ssid.encode(),
            parsed_bssid,
            password.encode(),
            ipaddress.IPv4Address(local_ip).packed,
        )
    ]
    return guide, datum


def expected_answer_marker(ssid: str, password: str) -> int:
    """Return the first byte of the module answer."""
    return (len(ssid.encode()) + len(password.encode()) + 9) & 0xFF


def parse_pairing_answer(data: bytes, marker: int) -> PairingResult | None:
    """Decode the module answer: marker, MAC (6 bytes), IP (4 bytes)."""
    if len(data) < ESPTOUCH_RESULT_LEN or data[0] != marker:
        return None
    return PairingResult(
        mac=data[1:7].hex(":"), ip=str(ipaddress.IPv4Address(data[7:11]))
    )


class _AnswerProtocol(asyncio.DatagramProtocol):
    def __init__(self, marker: int, answer: asyncio.Future[PairingResult]) -> None:
        self._marker = marker
        self._answer = answer

    def datagram_received(self, data: bytes, addr: tuple[str | int, int]) -> None:
        _LOGGER.debug("ESP-Touch answer from %s: %s", addr, data.hex())
        result = parse_pairing_answer(data, self._marker)
        if result and not self._answer.done():
            self._answer.set_result(result)


async def async_pair(
    ssid: str,
    password: str,
    local_ip: str | None = None,
    *,
    bssid: str | None = None,
    send_timeout: float = ESPTOUCH_SEND_TIMEOUT,
    receive_timeout: float = ESPTOUCH_RECEIVE_TIMEOUT,
    target: tuple[str, int] = (BROADCAST_ADDR, ESPTOUCH_TARGET_PORT),
    listen_port: int = ESPTOUCH_LISTEN_PORT,
) -> PairingResult:
    """Send the Wi-Fi credentials to a spa in pairing mode.

    ``local_ip`` is the address of this host on that network (sent to the spa
    so that it can answer), detected when omitted. ``bssid``, the MAC address of the 2.4 GHz access
    point, is optional: a zero BSSID is sent when it is unknown.
    Raises :class:`PairingError` without answer.
    """
    if local_ip is None:
        local_ip = get_local_ip()
    guide, datum = build_packets(ssid, password, local_ip, bssid)
    marker = expected_answer_marker(ssid, password)
    loop = asyncio.get_running_loop()
    answer: asyncio.Future[PairingResult] = loop.create_future()

    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.setblocking(False)
    listener.bind(("", listen_port))
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _AnswerProtocol(marker, answer), sock=listener
    )
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sender.setblocking(False)

    async def _send(packet: bytes) -> None:
        await loop.sock_sendto(sender, packet, target)
        await asyncio.sleep(ESPTOUCH_PACKET_INTERVAL)

    async def _broadcast() -> None:
        try:
            sender.bind((local_ip, 0))
            start = loop.time()
            index = 0
            while loop.time() - start < send_timeout:
                phase_end = loop.time() + ESPTOUCH_GUIDE_DURATION
                while loop.time() < phase_end:
                    for packet in guide:
                        await _send(packet)
                phase_end = loop.time() + ESPTOUCH_DATA_DURATION
                while loop.time() < phase_end:
                    for packet in datum[index : index + 3]:
                        await _send(packet)
                    index = (index + 3) % len(datum)
        except OSError as err:
            if not answer.done():
                answer.set_exception(PairingError(f"Broadcast failed: {err}"))

    broadcast = asyncio.create_task(_broadcast(), name="espspapy-esptouch")
    try:
        async with asyncio.timeout(send_timeout + receive_timeout):
            return await answer
    except TimeoutError as err:
        raise PairingError("No answer from the spa") from err
    finally:
        broadcast.cancel()
        with suppress(asyncio.CancelledError):
            await broadcast
        sender.close()
        transport.close()
