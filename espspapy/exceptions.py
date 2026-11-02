"""Exceptions for espspapy."""


class SpaException(Exception):
    """General exception."""


class SpaConnectionError(SpaException):
    """The spa cannot be reached or closed the connection."""


class TimeoutExceededError(SpaException):
    """The spa did not answer in time."""


class SpaNotRespondingError(SpaException):
    """The Wi-Fi module answered but the spa control board did not."""


class InvalidFrameError(SpaException):
    """The spa sent an unexpected answer."""


class CommandError(SpaException):
    """The command cannot be executed in the current spa state."""


class PairingError(SpaException):
    """ESP-Touch pairing failed."""
