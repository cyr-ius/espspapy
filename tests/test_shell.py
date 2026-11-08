"""Tests of the debug shell relay."""

from __future__ import annotations

import asyncio

from espspapy.shell import async_shell


class FakeShell:
    """Debug shell echoing the received commands after a prompt."""

    def __init__(self) -> None:
        self.received: list[str] = []
        self.port = 0

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        writer.write(b"banner\r\n>")
        while line := await reader.readline():
            command = line.decode().strip()
            self.received.append(command)
            writer.write(f"=> {command}\r\n>".encode())
            await writer.drain()
        writer.close()


async def run(lines: list[str]) -> tuple[FakeShell, str]:
    """Run the relay with scripted user lines, return the shell and output."""
    shell = FakeShell()
    server = await asyncio.start_server(shell.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    output: list[str] = []
    queue = iter(lines)

    async def read_line(prompt: str) -> str | None:
        await asyncio.sleep(0.05)  # let the output arrive
        output.append(prompt)
        return next(queue, None)

    async with server:
        await async_shell("127.0.0.1", read_line, output.append, port)
    return shell, "".join(output)


async def test_relay_until_exit() -> None:
    """Commands are sent, output printed, nothing after exit."""
    shell, output = await run(["help", "task-stats", "exit", "help"])
    assert shell.received == ["help", "task-stats"]
    assert "banner" in output and "=> task-stats" in output


async def test_dangerous_command_needs_yes() -> None:
    """reboot and smartconfig are sent only after 'yes'."""
    shell, output = await run(["reboot", "no", "smartconfig", "yes"])
    assert shell.received == ["smartconfig"]
    assert "Not sent." in output
