"""Tests of LAN discovery."""

from __future__ import annotations

import asyncio
import socket

from espspapy import SpaInfo, async_discover
from espspapy.discovery import parse_discovery_answer


def test_parse_answer() -> None:
    """NUL padded JSON, IP taken from the sender when missing."""
    answer = b'{"uid":"ABC","dtype":"spa"}\x00\x00'
    assert parse_discovery_answer(answer, "192.168.1.10") == SpaInfo(
        uid="ABC", ip="192.168.1.10", dtype="spa"
    )
    assert parse_discovery_answer(b"spa_request", "192.168.1.10") is None
    assert parse_discovery_answer(b'{"ip":"1.2.3.4"}', "1.2.3.4") is None


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def test_async_discover() -> None:
    """A simulated spa answers the request on the receive port."""
    loop = asyncio.get_running_loop()
    send_port, receive_port = _free_udp_port(), _free_udp_port()

    class FakeSpa(asyncio.DatagramProtocol):
        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            self.transport = transport

        def datagram_received(self, data: bytes, addr: tuple[str | int, int]) -> None:
            assert data == b"spa_request"
            assert isinstance(self.transport, asyncio.DatagramTransport)
            answer = b'{"uid":"ABC","ip":"192.168.1.10","dtype":"spa"}\x00'
            self.transport.sendto(answer, ("127.0.0.1", receive_port))

    # The fake spa shares the request port (SO_REUSEADDR) on the loopback address.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", send_port))
    transport, _ = await loop.create_datagram_endpoint(FakeSpa, sock=sock)
    try:
        spas = await async_discover(
            0.3,
            broadcast_address="127.0.0.1",
            send_port=send_port,
            receive_port=receive_port,
        )
    finally:
        transport.close()
    assert spas == [SpaInfo(uid="ABC", ip="192.168.1.10", dtype="spa")]
