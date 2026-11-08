"""Local TCP client of a spa."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress
import json
import logging
import time
from types import TracebackType
from typing import Any, Self

from .const import (
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    FRAME_SET_TEMPERATURE,
    FRAME_STATUS,
    HEARTBEAT_INTERVAL,
    MSG_TYPE_COMMAND,
    MSG_TYPE_HEARTBEAT,
    MSG_TYPE_IDENTIFY,
    MSG_TYPE_REPORT,
    RESULT_OK,
    RETRY_DELAY,
    SPA_TCP_PORT,
)
from .exceptions import (
    CommandError,
    InvalidFrameError,
    SpaConnectionError,
    SpaException,
    SpaNotRespondingError,
    TimeoutExceededError,
)
from .helpers import with_checksum
from .models import Device, SpaInfo, SpaStatus
from .timers import TimerSlot, build_timer_frame

_LOGGER = logging.getLogger(__name__)

RETRYABLE_ERRORS = (
    SpaConnectionError,
    TimeoutExceededError,
    SpaNotRespondingError,
    InvalidFrameError,
)


class Spa:
    """Spa reachable on the local network (TCP 8990).

    The connection is persistent: opened on demand, kept between requests and
    reopened after an error. Used as an async context manager, a heartbeat is
    sent every ``heartbeat_interval`` seconds (``None`` disables it; call
    :meth:`async_heartbeat` yourself in that case).

    Example::

        async with Spa("192.168.1.10") as spa:
            status = await spa.async_get_status()
            await spa.async_set_device(Device.BUBBLES, True)
    """

    def __init__(
        self,
        host: str,
        port: int = SPA_TCP_PORT,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        retry_delay: float = RETRY_DELAY,
        heartbeat_interval: float | None = HEARTBEAT_INTERVAL,
    ) -> None:
        """Initialize the client."""
        self.host = host
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self.retry_delay = retry_delay
        self.heartbeat_interval = heartbeat_interval
        self.last_status: SpaStatus | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._last_sid = 0

    async def __aenter__(self) -> Self:
        """Connect and start the heartbeat."""
        await self.async_connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop the heartbeat and close the connection."""
        await self.async_close()

    @property
    def connected(self) -> bool:
        """Return True if the TCP connection is open."""
        return self._writer is not None and not self._writer.is_closing()

    async def async_connect(self) -> None:
        """Open the connection and start the heartbeat."""
        async with self._lock:
            await self._connect()
        if self.heartbeat_interval and self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(self.heartbeat_interval),
                name=f"espspapy-heartbeat-{self.host}",
            )

    async def async_close(self) -> None:
        """Stop the heartbeat and close the connection."""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        async with self._lock:
            await self._disconnect()

    async def async_heartbeat(self) -> None:
        """Send a heartbeat if the connection is open."""
        async with self._lock:
            if self._writer is None:
                return
            try:
                await self._write(self._writer, MSG_TYPE_HEARTBEAT, "")
            except OSError as err:
                _LOGGER.debug("Heartbeat failed: %s", err)
                await self._disconnect()

    async def async_identify(self) -> SpaInfo:
        """Return the spa identity (uid)."""
        report = await self._async_request(MSG_TYPE_IDENTIFY, "", MSG_TYPE_IDENTIFY)
        data = report.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError as err:
                raise InvalidFrameError(f"Invalid identity: {data}") from err
        if not isinstance(data, dict):
            raise InvalidFrameError(f"Unexpected identification answer: {report}")
        return SpaInfo.from_dict(data)

    async def async_send_frame(self, frame: str) -> SpaStatus:
        """Send a hex frame (checksum added) and return the resulting status."""
        report = await self._async_request(
            MSG_TYPE_COMMAND, with_checksum(frame), MSG_TYPE_REPORT
        )
        self.last_status = SpaStatus.from_frame(str(report.get("data", "")))
        return self.last_status

    async def async_get_status(self) -> SpaStatus:
        """Return the spa status."""
        return await self.async_send_frame(FRAME_STATUS)

    async def async_toggle(self, device: Device) -> SpaStatus:
        """Toggle a device."""
        return await self.async_send_frame(device.frame)

    async def async_set_device(self, device: Device, on: bool) -> SpaStatus:
        """Switch a device on or off.

        The spa only knows toggles: the status is read first so that the toggle
        is sent only when needed.
        """
        current = await self.async_get_status()
        if current.is_on(device) == on:
            return current
        if device is not Device.POWER and on and not current.power:
            raise CommandError("The spa is off")
        return await self.async_toggle(device)

    async def async_set_temperature(self, value: int) -> SpaStatus:
        """Set the target temperature, in the unit configured on the spa."""
        current = await self.async_get_status()
        low, high = current.temperature_range
        if not low <= value <= high:
            raise CommandError(f"Temperature {value} out of range {low}-{high}")
        return await self.async_send_frame(f"{FRAME_SET_TEMPERATURE}{value:02X}")

    async def async_set_timers(
        self, device: Device, slots: Sequence[TimerSlot]
    ) -> SpaStatus:
        """Replace the timers of the filter, heater or sanitizer.

        Slots are sent as delays from now; an empty list clears the timers.
        The control board applies timer frames without answering (the module
        then reports ``timeout``): the frame is sent once, never retried, and
        the status is read afterwards. Check its timer bytes to confirm.
        """
        report = await self._exchange(
            MSG_TYPE_COMMAND,
            with_checksum(build_timer_frame(device, slots)),
            MSG_TYPE_REPORT,
        )
        try:
            self.last_status = SpaStatus.from_frame(str(report.get("data", "")))
        except InvalidFrameError:
            _LOGGER.debug("Timer frame sent, no status in the answer: %s", report)
        else:
            return self.last_status
        return await self.async_get_status()

    async def _async_request(
        self, msg_type: int, data: str, answer_type: int
    ) -> dict[str, Any]:
        """Send a message and return the answer, retrying transient errors."""
        for attempt in range(1, self.retries + 1):
            try:
                report = await self._exchange(msg_type, data, answer_type)
                result = str(report.get("result", RESULT_OK))
                if result.lower() != RESULT_OK:
                    raise SpaNotRespondingError(
                        f"The spa control board did not answer ({result})"
                    )
            except RETRYABLE_ERRORS as err:
                _LOGGER.debug("Attempt %d/%d failed: %s", attempt, self.retries, err)
                if attempt == self.retries:
                    raise
                await asyncio.sleep(self.retry_delay)
            else:
                return report
        raise SpaException("No attempt made")

    async def _exchange(
        self, msg_type: int, data: str, answer_type: int
    ) -> dict[str, Any]:
        async with self._lock:
            try:
                reader, writer = await self._connect()
                sid = await self._write(writer, msg_type, data)
                async with asyncio.timeout(self.timeout):
                    while True:
                        line = await reader.readline()
                        if not line:
                            raise SpaConnectionError("Connection closed by the spa")
                        _LOGGER.debug("Received: %s", line.decode().rstrip())
                        report = json.loads(line)
                        # Heartbeat answers and older sids are ignored.
                        if (
                            isinstance(report, dict)
                            and report.get("type") == answer_type
                            and str(report.get("sid")) == sid
                        ):
                            return report
            except TimeoutError as err:
                await self._disconnect()
                raise TimeoutExceededError(
                    f"No answer from {self.host}:{self.port}"
                ) from err
            except OSError as err:
                await self._disconnect()
                raise SpaConnectionError(
                    f"Cannot communicate with {self.host}:{self.port}: {err}"
                ) from err
            except ValueError as err:
                await self._disconnect()
                raise InvalidFrameError(f"Invalid answer: {err}") from err
            except SpaException:
                await self._disconnect()
                raise

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self._reader is None or self._writer is None:
            async with asyncio.timeout(self.timeout):
                self._reader, self._writer = await asyncio.open_connection(
                    self.host, self.port
                )
            _LOGGER.debug("Connected to %s:%d", self.host, self.port)
        return self._reader, self._writer

    async def _disconnect(self) -> None:
        if self._writer is not None:
            self._writer.close()
            with suppress(OSError):
                await self._writer.wait_closed()
            _LOGGER.debug("Disconnected from %s:%d", self.host, self.port)
        self._reader = None
        self._writer = None

    async def _write(
        self, writer: asyncio.StreamWriter, msg_type: int, data: str
    ) -> str:
        # Unique sid, even for two messages sent within the same millisecond.
        self._last_sid = max(int(time.time() * 1000), self._last_sid + 1)
        sid = str(self._last_sid)
        payload = json.dumps(
            {"data": data, "sid": sid, "type": msg_type}, separators=(",", ":")
        )
        _LOGGER.debug("Send: %s", payload)
        # Raw JSON, without delimiter.
        writer.write(payload.encode())
        await writer.drain()
        return sid

    async def _heartbeat_loop(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            await self.async_heartbeat()
