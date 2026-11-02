"""Tests for espspapy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_fixture(filename: str) -> Any:
    """Load a JSON fixture."""
    return json.loads(
        (Path(__file__).parent / "fixtures" / filename).read_text(encoding="utf-8")
    )
