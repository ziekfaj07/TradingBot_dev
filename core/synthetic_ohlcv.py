from __future__ import annotations

import math
from datetime import datetime, timezone

import pandas as pd


_INTERVAL_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
}


def _to_utc(value) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = pd.Timestamp(value).to_pydatetime()

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def build_synthetic_ohlcv(
    *,
    symbol: str = "BTC/USDT",
    interval: str = "1h",
    start=None,
    end=None,
    limit: int = 300,
) -> pd.DataFrame:
    """
    Deterministic local OHLCV generator for dev/test only.

    This is not for performance validation.
    It is for verifying engine wiring when the ISP blocks public CEX endpoints.
    """
    interval = str(interval or "1h")
    step_seconds = _INTERVAL_SECONDS.get(interval, 3600)

    if start is None:
        start_dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
    else:
        start_dt = _to_utc(start)

    if end is None:
        periods = max(80, int(limit or 300))
    else:
        end_dt = _to_utc(end)
        total_seconds = max(0, int((end_dt - start_dt).total_seconds()))
        periods = max(80, min(int(limit or 300), total_seconds // step_seconds + 1))

    base = 50000.0 if "BTC" in str(symbol).upper() else 1000.0

    rows = []
    previous_close = base

    for i in range(periods):
        ts = start_dt + pd.Timedelta(seconds=i * step_seconds)

        wave = math.sin(i / 8.0) * 250.0
        trend = i * 3.5
        close = base + wave + trend

        open_price = previous_close
        high = max(open_price, close) + 35.0 + abs(math.sin(i / 5.0)) * 20.0
        low = min(open_price, close) - 35.0 - abs(math.cos(i / 5.0)) * 20.0
        volume = 10.0 + abs(math.sin(i / 3.0)) * 5.0

        rows.append(
            {
                "timestamp": ts,
                "open": float(open_price),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": float(volume),
            }
        )

        previous_close = close

    df = pd.DataFrame(rows)

    # Cover both common timestamp conventions used in this repo family.
    df["datetime"] = df["timestamp"]
    df["ts"] = (pd.to_datetime(df["timestamp"]).astype("int64") // 1_000_000).astype("int64")

    return df