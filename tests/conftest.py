"""Fixtures: a fake spa speaking the TCP 8990 protocol."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
from typing import Any

import pytest

from espspapy.helpers import checksum
from espspapy.models import Device

UID = "0K040220262022010600003082"
TOGGLES = {device.frame: device.bit for device in Device}


class FakeSpa:
    """In-memory spa answering like the real Wi-Fi module."""

    def __init__(self) -> None:
        self.flags = 0b000011  # power + filter
        self.temperature = 37
        self.target = 38
        self.received: list[dict[str, Any]] = []
        self.connections = 0
        self.not_responding = 0  # next commands answered with "timeout"
        self.drop_next = False  # close the connection on the next command
        self.server: asyncio.Server | None = None
        self.port = 0

    def status_frame(self) -> str:
        """Return the current status frame."""
        frame = (
            f"FFFF110F01{self.flags:02X}00{self.temperature:02X}00000000"
            f"808080{self.target:02X}0000"
        )
        return frame + checksum(frame)

    def answer(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Return the answer to a message."""
        sid = msg["sid"]
        if msg["type"] == 0:
            return {"sid": sid, "result": "ok", "type": 0}
        if msg["type"] == 3:
            data = json.dumps({"ip": "127.0.0.1", "uid": UID, "dtype": "spa"})
            return {"sid": sid, "data": data, "result": "ok", "type": 3}
        if self.not_responding:
            self.not_responding -= 1
            return {"sid": sid, "data": "", "result": "timeout", "type": 2}
        frame, crc = msg["data"][:-2], msg["data"][-2:]
        assert checksum(frame) == crc
        if frame in TOGGLES:
            self.flags ^= 1 << TOGGLES[frame]
        elif frame.startswith("8888050F0C"):
            self.target = int(frame[10:12], 16)
        return {"sid": sid, "data": self.status_frame(), "result": "ok", "type": 2}

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.connections += 1
        decoder = json.JSONDecoder()
        buffer = ""
        try:
            while data := await reader.read(1024):
                buffer += data.decode()
                while buffer:
                    try:
                        msg, end = decoder.raw_decode(buffer)
                    except ValueError:
                        break
                    buffer = buffer[end:]
                    self.received.append(msg)
                    if self.drop_next and msg["type"] == 1:
                        self.drop_next = False
                        return
                    writer.write(json.dumps(self.answer(msg)).encode() + b"\n")
                    await writer.drain()
        finally:
            writer.close()

    async def start(self) -> None:
        """Listen on a free local port."""
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Stop listening."""
        if self.server:
            self.server.close()
            self.server.close_clients()
            await self.server.wait_closed()


@pytest.fixture
async def fake_spa() -> AsyncIterator[FakeSpa]:
    """Start a fake spa."""
    spa = FakeSpa()
    await spa.start()
    yield spa
    await spa.stop()
