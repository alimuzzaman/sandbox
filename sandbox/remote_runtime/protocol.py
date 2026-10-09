"""Declared control-protocol range of this checkout (spec 061 FR-001, FR-006).

Bump ``CONTROL_PROTOCOL_SPOKEN`` whenever a controller-to-runtime transport
payload or receipt shape changes. Raise ``CONTROL_PROTOCOL_OLDEST_SERVED``
when this runtime stops accepting controllers that speak an older version.
``sandbox/remote_runtime/shapes.py`` enforces the bump: changed payload keys
under an unchanged version fail the shape test.
This module is under ``sandbox/``, so the runtime revision digest covers it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CONTROL_PROTOCOL_SPOKEN = 2
CONTROL_PROTOCOL_OLDEST_SERVED = 1

UNIT_ENVIRONMENT_NAME = "SANDBOX_REMOTE_MCP_CONTROL_PROTOCOL"
_VALUE_RE = re.compile(r"([1-9][0-9]{0,5}):([1-9][0-9]{0,5})")


@dataclass(frozen=True)
class ControlProtocol:
    spoken: int
    oldest_served: int

    def __post_init__(self):
        if (not isinstance(self.spoken, int) or isinstance(self.spoken, bool)
                or not isinstance(self.oldest_served, int)
                or isinstance(self.oldest_served, bool)
                or self.spoken < 1 or self.oldest_served < 1
                or self.oldest_served > self.spoken):
            raise ValueError("control protocol range is invalid")

    def encode(self) -> str:
        return f"{self.spoken}:{self.oldest_served}"

    def as_mapping(self) -> dict:
        return {"spoken": self.spoken, "oldest_served": self.oldest_served}


def local_protocol() -> ControlProtocol:
    return ControlProtocol(CONTROL_PROTOCOL_SPOKEN, CONTROL_PROTOCOL_OLDEST_SERVED)


def parse_protocol(value) -> ControlProtocol | None:
    """Parse ``spoken:oldest`` as written into the unit; None if absent or invalid."""
    if not isinstance(value, str):
        return None
    match = _VALUE_RE.fullmatch(value.strip())
    if match is None:
        return None
    try:
        return ControlProtocol(int(match.group(1)), int(match.group(2)))
    except ValueError:
        return None


def unit_environment_line(protocol: ControlProtocol | None = None) -> str:
    protocol = protocol or local_protocol()
    return f"Environment={UNIT_ENVIRONMENT_NAME}={protocol.encode()}"
