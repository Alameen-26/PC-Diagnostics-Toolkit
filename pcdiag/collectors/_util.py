"""Small unit-conversion and parsing helpers shared by collectors.

Units: the toolkit reports sizes in binary gigabytes (GiB, 1024**3 bytes)
because that is what Windows Explorer and Task Manager show, even though
they label it "GB". Disk vendors use decimal GB, so a "500 GB" drive shows
up here as ~465.8.

Added in v0.1.0
"""
from __future__ import annotations

from typing import Any, Optional

GIB = 1024 ** 3
MIB = 1024 ** 2


def bytes_to_gb(value: Any, ndigits: int = 2) -> Optional[float]:
    """Convert a byte count to GiB, rounded; None stays None."""
    num = to_float(value)
    return None if num is None else round(num / GIB, ndigits)


def bytes_to_mb(value: Any, ndigits: int = 1) -> Optional[float]:
    """Convert a byte count to MiB, rounded; None stays None."""
    num = to_float(value)
    return None if num is None else round(num / MIB, ndigits)


def to_float(value: Any) -> Optional[float]:
    """Best-effort float conversion; returns None for blanks and junk."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> Optional[int]:
    """Best-effort int conversion; returns None for blanks and junk."""
    num = to_float(value)
    return None if num is None else int(num)


def rnd(value: Optional[float], ndigits: int = 1) -> Optional[float]:
    """Round a value that may be None."""
    return None if value is None else round(value, ndigits)
