"""Tests of ESP-Touch pairing."""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest

from espspapy import PairingError, PairingResult, async_pair
from espspapy.esptouch import (
    build_datum_lengths,
    build_packets,
    crc8,
    expected_answer_marker,
    parse_pairing_answer,
)
from espspapy.helpers import parse_bssid

from . import load_fixture

# Module answer (SSID of 8 bytes, password of 10 bytes).
REAL_ANSWER = bytes.fromhex("1b001a2b3c4d5ec0a8010a")


@pytest.mark.parametrize("vector", load_fixture("esptouch_vectors.json"))
def test_datum_matches_espressif_sdk(vector: dict[str, Any]) -> None:
    """Packet lengths match the Espressif SDK (Java ``DatumCode``)."""
    lengths = build_datum_lengths(
        vector["ssid"].encode(),
        parse_bssid(vector["bssid"]),
        vector["password"].encode(),
        socket.inet_aton(vector["local_ip"]),
    )
    assert lengths == vector["lengths"]


def test_crc8() -> None:
    """CRC8 Dallas/Maxim check value."""
    assert crc8(b"123456789") == 0xA1


def test_packets() -> None:
    """Guide codes and payload."""
    guide, datum = build_packets("milkyway", "secret", "1.2.3.4", "00:00:1A:2B:3C:4D")
    assert [len(packet) for packet in guide] == [515, 514, 513, 512]
    assert len(datum) % 3 == 0
    assert set(b"".join(datum)) == {ord("1")}


def test_packets_without_bssid() -> None:
    """Without BSSID, a zero BSSID is encoded."""
    _, datum = build_packets("milkyway", "secret", "1.2.3.4")
    _, zero = build_packets("milkyway", "secret", "1.2.3.4", "00:00:00:00:00:00")
    assert datum == zero


def test_too_long() -> None:
    """SSID + password over the protocol limit."""
    with pytest.raises(ValueError):
        build_packets("s" * 32, "p" * 90, "1.2.3.4")


def test_parse_real_answer() -> None:
    """Module answer: marker, MAC, IP."""
    marker = expected_answer_marker("milkyway", "x" * 10)
    assert marker == 0x1B
    assert parse_pairing_answer(REAL_ANSWER, marker) == PairingResult(
        mac="00:1a:2b:3c:4d:5e", ip="192.168.1.10"
    )
    assert parse_pairing_answer(REAL_ANSWER, 0x10) is None
    assert parse_pairing_answer(REAL_ANSWER[:5], marker) is None


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def test_async_pair() -> None:
    """A simulated module answers once it has received packets."""
    loop = asyncio.get_running_loop()
    listen_port = _free_udp_port()
    received: list[int] = []

    class Module(asyncio.DatagramProtocol):
        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            self.transport = transport

        def datagram_received(self, data: bytes, addr: tuple[str | int, int]) -> None:
            received.append(len(data))
            if len(received) == 20:
                assert isinstance(self.transport, asyncio.DatagramTransport)
                self.transport.sendto(REAL_ANSWER, ("127.0.0.1", listen_port))

    transport, _ = await loop.create_datagram_endpoint(
        Module, local_addr=("127.0.0.1", 0)
    )
    target = transport.get_extra_info("sockname")
    try:
        result = await async_pair(
            "milkyway",
            "x" * 10,
            "127.0.0.1",
            bssid="00:00:1A:2B:3C:4D",
            send_timeout=5,
            receive_timeout=1,
            target=(target[0], target[1]),
            listen_port=listen_port,
        )
    finally:
        transport.close()
    assert result.ip == "192.168.1.10"
    assert received[:4] == [515, 514, 513, 512]


async def test_async_pair_no_answer() -> None:
    """No answer raises PairingError."""
    with pytest.raises(PairingError):
        await async_pair(
            "milkyway",
            "secret",
            "127.0.0.1",
            send_timeout=0.1,
            receive_timeout=0.1,
            target=("127.0.0.1", _free_udp_port()),
            listen_port=_free_udp_port(),
        )
