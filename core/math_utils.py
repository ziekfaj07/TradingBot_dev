from __future__ import annotations

import math
from typing import Any


def safe_float(value: Any, default: float = 0.0) -> float:
    """
    Convert value to finite float.

    Returns default for:
    - None
    - empty strings
    - invalid strings
    - NaN / inf
    - unsupported objects
    """
    if value is None:
        return default

    if isinstance(value, bool):
        v = float(int(value))
        return v if math.isfinite(v) else default

    if isinstance(value, (int, float)):
        v = float(value)
        return v if math.isfinite(v) else default

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            v = float(text)
        except ValueError:
            return default
        return v if math.isfinite(v) else default

    return default


def safe_float_or_none(value: Any) -> float | None:
    """
    Convert value to finite float, or None if conversion is not valid.
    """
    if value is None:
        return None

    v = safe_float(value, float("nan"))
    return v if math.isfinite(v) else None


def safe_int(value: Any, default: int = 0) -> int:
    """
    Convert value to int safely.

    Supports floats and numeric strings.
    Returns default for invalid / non-finite values.
    """
    if value is None:
        return default

    if isinstance(value, bool):
        return int(value)

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            return default
        return int(value)

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            v = float(text)
        except ValueError:
            return default
        if not math.isfinite(v):
            return default
        return int(v)

    return default


def safe_int_or_none(value: Any) -> int | None:
    """
    Convert value to int, or None if conversion is not valid.
    """
    if value is None:
        return None

    sentinel = object()

    try:
        if isinstance(value, str) and not value.strip():
            return None

        v = safe_int(value, sentinel)  # type: ignore[arg-type]
        if v is sentinel:
            return None
        return int(v)
    except Exception:
        return None


def clean_float(value: Any, eps: float = 1e-12) -> float:
    """
    Convert value to float and collapse microscopic dust values to zero.
    """
    v = safe_float(value, 0.0)
    if abs(v) <= float(eps):
        return 0.0
    return float(v)


def clean_money(value: Any, eps: float = 1e-9) -> float:
    """
    Convert value to float, remove dust, and round to stable precision.
    """
    return float(round(clean_float(value, eps=eps), 12))