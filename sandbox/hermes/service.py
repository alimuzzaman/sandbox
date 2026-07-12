from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class HermesService:
    state: Any = None
    routing: Any = None
    jobs: Any = None
    gateway: Any = None
    backup: Any = None
