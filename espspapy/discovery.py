"""Discovery of the spas of the local network (UDP broadcast)."""

from __future__ import annotations

import asyncio
import json
import logging
import socket

from .const import (
    BROADCAST_ADDR,
    DISCOVERY_RECEIVE_PORT,
    DISCOVERY_REQUEST,
    DISCOVERY_SEND_PORT,
    DISCOVERY_TIMEOUT,
)
from .exceptions import InvalidFrameError
from .models import SpaInfo

_LOGGER = logging.getLogger(__name__)


def parse_discovery_answer(data: bytes, sender_ip: str) -> SpaInfo | None:
    """Decode a discovery answer (JSON, NUL padded)."""
    text = data.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    if "{" not in text:
        return None
    try:
        info = SpaInfo.from_dict(json.loads(text))
    except (ValueError, InvalidFrameError) as err:
        _LOGGER.debug("Ignored answer from %s: %s", sender_ip, err)
        return None
    if info.ip is None:
        return SpaInfo(uid=info.uid, ip=sender_ip, dtype=info.dtype)
    return info


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.found: dict[str, SpaInfo] = {}

    def datagram_received(self, data: bytes, addr: tuple[str | int, int]) -> None:
        _LOGGER.debug("Discovery answer from %s: %s", addr, data)
        if info := parse_discovery_answer(data, str(addr[0])):
            self.found[info.uid] = info


def _udp_socket(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)
    sock.bind(("", port))
    return sock


async def async_discover(
    timeout: float = DISCOVERY_TIMEOUT,
    *,
    broadcast_address: str = BROADCAST_ADDR,
    send_port: int = DISCOVERY_SEND_PORT,
    receive_port: int = DISCOVERY_RECEIVE_PORT,
) -> list[SpaInfo]:
    """Broadcast ``spa_request`` and return the spas that answered.

    The spas answer on ``receive_port`` (10500), whatever the source port.
    """
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        _DiscoveryProtocol, sock=_udp_socket(receive_port)
    )
    sender = _udp_socket(send_port)
    try:
        await loop.sock_sendto(
            sender, DISCOVERY_REQUEST, (broadcast_address, send_port)
        )
        await asyncio.sleep(timeout)
    finally:
        sender.close()
        transport.close()
    return list(protocol.found.values())
