"""Local control of connected spas."""

from .discovery import async_discover
from .esptouch import async_pair
from .exceptions import (
    CommandError,
    InvalidFrameError,
    PairingError,
    SpaConnectionError,
    SpaException,
    SpaNotRespondingError,
    TimeoutExceededError,
)
from .helpers import get_local_ip
from .models import Device, PairingResult, SpaInfo, SpaStatus
from .spa import Spa
from .timers import TimerSlot, build_timer_frame

__all__ = [
    "CommandError",
    "Device",
    "InvalidFrameError",
    "PairingError",
    "PairingResult",
    "Spa",
    "SpaConnectionError",
    "SpaException",
    "SpaInfo",
    "SpaNotRespondingError",
    "SpaStatus",
    "TimeoutExceededError",
    "TimerSlot",
    "async_discover",
    "async_pair",
    "build_timer_frame",
    "get_local_ip",
]
