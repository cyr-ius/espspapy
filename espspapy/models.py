"""Data models of the spa."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
from typing import Any

from .const import (
    ERROR_CODE_END,
    ERROR_CODE_MIN,
    HEATER_STANDBY_BIT,
    TEMP_RANGE_CELSIUS,
    TEMP_RANGE_FAHRENHEIT,
)
from .exceptions import InvalidFrameError
from .helpers import checksum, work_hours

_LOGGER = logging.getLogger(__name__)


class Device(Enum):
    """Spa device: (status bit in byte 5, toggle frame)."""

    POWER = (0, "8888060F014000")
    FILTER = (1, "8888060F010004")
    HEATER = (2, "8888060F010010")
    JETS = (3, "8888060F011000")
    BUBBLES = (4, "8888060F010400")
    SANITIZER = (5, "8888060F010001")

    @property
    def bit(self) -> int:
        """Return the status bit."""
        return self.value[0]

    @property
    def frame(self) -> str:
        """Return the toggle frame (without checksum)."""
        return self.value[1]


@dataclass(frozen=True, slots=True)
class SpaInfo:
    """Spa identity, as sent by the discovery and identification answers."""

    uid: str
    ip: str | None = None
    dtype: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpaInfo:
        """Build from the JSON payload sent by the spa."""
        uid = data.get("uid")
        if not isinstance(uid, str) or not uid.strip():
            raise InvalidFrameError(f"Answer without uid: {data}")
        ip = data.get("ip")
        dtype = data.get("dtype")
        return cls(
            uid=uid.strip(),
            ip=str(ip) if ip is not None else None,
            dtype=str(dtype) if dtype is not None else None,
        )


@dataclass(frozen=True, slots=True)
class PairingResult:
    """Answer of the spa Wi-Fi module once connected (ESP-Touch)."""

    mac: str
    ip: str


@dataclass(frozen=True, slots=True)
class SpaStatus:
    """Decoded ``FFFF…0F01`` status frame."""

    power: bool
    filter: bool
    heater: bool
    heating: bool
    jets: bool
    bubbles: bool
    sanitizer: bool
    fahrenheit: bool
    current_temperature: int | None
    target_temperature: int | None
    error_code: str | None
    filter_hours: float | None
    sanitizer_hours: float | None
    heater_hours: float | None
    raw: str

    @classmethod
    def from_frame(cls, frame: str) -> SpaStatus:
        """Decode a status frame."""
        frame = frame.upper()
        if not frame.startswith("FFFF") or len(frame) < 38 or frame[6:10] != "0F01":
            raise InvalidFrameError(f"Unknown status frame: {frame}")
        if checksum(frame[:-2]) != frame[-2:]:
            _LOGGER.warning("Invalid checksum: %s", frame)

        data = bytes.fromhex(frame)
        flags, flashing, temp_or_error, target = data[5], data[6], data[7], data[15]

        fahrenheit = TEMP_RANGE_FAHRENHEIT[0] <= target <= TEMP_RANGE_FAHRENHEIT[1]
        celsius = TEMP_RANGE_CELSIUS[0] <= target <= TEMP_RANGE_CELSIUS[1]

        error_code: str | None = None
        error_flashing = bool(flashing & 0x01)
        if error_flashing and ERROR_CODE_MIN <= temp_or_error < ERROR_CODE_END:
            if (
                temp_or_error < 182
                or 190 <= temp_or_error <= 197
                or temp_or_error == 199
            ):
                error_code = f"E{temp_or_error - 100}"
        elif error_flashing and temp_or_error == ERROR_CODE_END:
            error_code = "END"

        def bit(position: int) -> bool:
            return bool(flags & 1 << position)

        return cls(
            power=bit(Device.POWER.bit),
            filter=bit(Device.FILTER.bit),
            heater=bit(Device.HEATER.bit) or bit(HEATER_STANDBY_BIT),
            heating=bit(Device.HEATER.bit),
            jets=bit(Device.JETS.bit),
            bubbles=bit(Device.BUBBLES.bit),
            sanitizer=bit(Device.SANITIZER.bit),
            fahrenheit=fahrenheit,
            current_temperature=(
                temp_or_error if temp_or_error < ERROR_CODE_MIN else None
            ),
            target_temperature=target if fahrenheit or celsius else None,
            error_code=error_code,
            filter_hours=work_hours(data[12]),
            sanitizer_hours=work_hours(data[13]),
            heater_hours=work_hours(data[14]),
            raw=frame,
        )

    @property
    def temperature_range(self) -> tuple[int, int]:
        """Return the allowed target temperature range, in the spa unit."""
        return TEMP_RANGE_FAHRENHEIT if self.fahrenheit else TEMP_RANGE_CELSIUS

    def is_on(self, device: Device) -> bool:
        """Return True if the device is on."""
        return bool(getattr(self, device.name.lower()))
