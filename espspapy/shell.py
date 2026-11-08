"""Interactive access to the debug shell of the Wi-Fi module (TCP port 23).

The shell runs on the ESP module, not on the spa control board: it only shows
the module's own diagnostics (FreeRTOS tasks, heap). It has no authentication.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress

from .const import DEBUG_SHELL_PORT

# Commands that disrupt the module: asked for confirmation first.
DANGEROUS_COMMANDS = {
    "reboot": "reboots the Wi-Fi module",
    "smartconfig": "reboots the module in pairing mode: it leaves the Wi-Fi network",
}
EXIT_COMMANDS = {"exit", "quit"}
SHELL_DRAIN_DELAY = 1.0

type ReadLine = Callable[[str], Awaitable[str | None]]
type Write = Callable[[str], None]


async def async_shell(
    host: str,
    read_line: ReadLine,
    write: Write,
    port: int = DEBUG_SHELL_PORT,
) -> None:
    """Relay lines between the user and the debug shell until exit or EOF.

    ``read_line(prompt)`` returns the next user line, ``None`` at end of input;
    ``write`` prints the shell output as it arrives.
    """
    reader, writer = await asyncio.open_connection(host, port)

    async def pump() -> None:
        while data := await reader.read(1024):
            write(data.decode(errors="replace"))
        write("\n[connection closed by the module]\n")

    output = asyncio.create_task(pump())
    try:
        while not output.done():
            line = await read_line("")
            if line is None:
                break
            command = line.strip()
            if command in EXIT_COMMANDS:
                break
            name = command.split()[0] if command else ""
            if name in DANGEROUS_COMMANDS:
                answer = await read_line(
                    f"'{name}' {DANGEROUS_COMMANDS[name]}. Type 'yes' to send it: "
                )
                if (answer or "").strip().lower() != "yes":
                    write("Not sent.\n>")
                    continue
            writer.write(command.encode() + b"\r\n")
            await writer.drain()
    finally:
        # Let the answer to the last command arrive before closing.
        await asyncio.wait({output}, timeout=SHELL_DRAIN_DELAY)
        output.cancel()
        with suppress(asyncio.CancelledError):
            await output
        writer.close()
        with suppress(OSError):
            await writer.wait_closed()
