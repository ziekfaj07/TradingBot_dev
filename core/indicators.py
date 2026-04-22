"""
core/indicators.py
==================
Shared technical indicator helpers used across the codebase.

Centralising these here eliminates the duplicate ATR implementations that
previously lived in both services/runner.py (_build_atr_series) and
services/mode_controller.py (_latest_atr_value_locked).

Usage
-----
    from core.indicators import build_atr_series, latest_atr_value

    # During backtesting (whole series up front):
    atr_series = build_atr_series(df, period=14)
    atr_value  = float(atr_series.iloc[-1]) if atr_series is not None else None

    # During live/paper (incremental, only the latest value):
    atr_value = latest_atr_value(bars_list, period=14)
"""
from __future__ import annotations

from typing import Any

import pandas as pd


# ── constants ──────────────────────────────────────────────────────────────────
BARS_PER_YEAR: dict[str, float] = {
    "1m":  365.0 * 24 * 60,
    "3m":  365.0 * 24 * 20,
    "5m":  365.0 * 24 * 12,
    "15m": 365.0 * 24 * 4,
    "30m": 365.0 * 24 * 2,
    "1h":  365.0 * 24,
    "2h":  365.0 * 12,
    "4h":  365.0 * 6,
    "6h":  365.0 * 4,
    "8h":  365.0 * 3,
    "12h": 365.0 * 2,
    "1d":  365.0,
    "3d":  365.0 / 3.0,
    "1w":  52.0,
}


def bars_per_year(interval: str) -> float:
    """Return the number of bars per calendar year for *interval*.

    Falls back to 365 (daily) for unknown strings.

    >>> bars_per_year("1h")
    8760.0
    >>> bars_per_year("4h")
    2190.0
    """
    key = str(interval or "1d").strip().lower()
    return BARS_PER_YEAR.get(key, 365.0)


# ── True Range / ATR ──────────────────────────────────────────────────────────

def build_atr_series(
    df: Any,
    period: int | None = 14,
) -> pd.Series | None:
    """Compute a simple (SMA-smoothed) ATR series for *df*.

    Parameters
    ----------
    df:     DataFrame with at least 'high', 'low', 'close' columns.
    period: Look-back window.  Values of 1 or below collapse to TR.

    Returns
    -------
    pd.Series of ATR values aligned to df's index, or None if the required
    columns are missing or df is not a DataFrame.
    """
    try:
        p = max(1, int(period or 14))
    except (TypeError, ValueError):
        p = 14

    if not isinstance(df, pd.DataFrame):
        return None

    needed = {"high", "low", "close"}
    if not needed.issubset(df.columns):
        return None

    high  = pd.to_numeric(df["high"],  errors="coerce")
    low   = pd.to_numeric(df["low"],   errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    prev_close = close.shift(1)

    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low  - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.rolling(window=p, min_periods=p).mean()


def latest_atr_value(
    bars: list[dict[str, Any]],
    period: int = 14,
) -> float | None:
    """Return the most recent ATR value computed from a list of bar dicts.

    Used in the live/paper loop where bars are stored as a plain list.
    Returns None if there is not enough data.
    """
    if not bars:
        return None

    df = pd.DataFrame(bars)
    needed = {"high", "low", "close"}
    if df.empty or not needed.issubset(df.columns):
        return None

    atr = build_atr_series(df, period)
    if atr is None or atr.empty:
        return None

    last = atr.iloc[-1]
    if pd.isna(last):
        return None

    try:
        value = float(last)
        return value if value > 0.0 else None
    except (TypeError, ValueError):
        return None
