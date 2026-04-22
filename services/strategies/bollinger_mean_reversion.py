from __future__ import annotations

from typing import Any

import pandas as pd

from services.strategies.base import BaseStrategy


class BollingerMeanReversion(BaseStrategy):
    name = "bollinger_mean_reversion"
    display_name = "Bollinger Mean Reversion"
    description = (
        "Enter long when price re-enters from below the lower Bollinger band; "
        "optionally emit short signals when price re-enters from above the upper band."
    )

    # Canonical names used everywhere: UI, API schema, strategy registry, strategy code.
    default_params = {
        "length": 20,
        "std_dev": 2.0,
        "min_band_width_pct": 0.01,
        "exit_on_mid": True,
        "allow_short": False,
    }
    min_bars = 22

    @staticmethod
    def _coerce_bool(value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)

    @classmethod
    def normalize_params(cls, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Normalize Bollinger params into one canonical naming layer.

        Canonical output:
          length
          std_dev
          min_band_width_pct
          exit_on_mid
          allow_short

        Backward-compatible accepted aliases:
          window, period, lookback      -> length
          stddev, stdev, std, std_mult  -> std_dev
          min_bandwidth_pct             -> min_band_width_pct
          min_bandwidth, min_band_width -> min_band_width_pct
        """
        raw = dict(params or {})
        alias_map = {
            "window": "length",
            "period": "length",
            "lookback": "length",
            "stddev": "std_dev",
            "stdev": "std_dev",
            "std": "std_dev",
            "std_mult": "std_dev",
            "min_bandwidth_pct": "min_band_width_pct",
            "min_bandwidth": "min_band_width_pct",
            "min_band_width": "min_band_width_pct",
        }

        normalized = dict(cls.default_params)
        for key, value in raw.items():
            canonical_key = alias_map.get(str(key), str(key))
            normalized[canonical_key] = value

        normalized["length"] = max(2, int(normalized.get("length", 20)))
        normalized["std_dev"] = max(0.000001, float(normalized.get("std_dev", 2.0)))
        normalized["min_band_width_pct"] = max(
            0.0,
            float(normalized.get("min_band_width_pct", 0.01)),
        )
        normalized["exit_on_mid"] = cls._coerce_bool(normalized.get("exit_on_mid"), True)
        normalized["allow_short"] = cls._coerce_bool(normalized.get("allow_short"), False)

        return normalized

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        if "close" not in df.columns:
            raise ValueError("Bollinger mean reversion strategy requires a 'close' column.")

        params = self.normalize_params(getattr(self, "params", {}) or {})

        d = df.copy()
        d["signal"] = 0

        length = params["length"]
        std_dev = params["std_dev"]
        min_band_width_pct = params["min_band_width_pct"]
        exit_on_mid = params["exit_on_mid"]
        allow_short = params["allow_short"]

        if len(d) < length + 2:
            return d

        close = pd.to_numeric(d["close"], errors="coerce")
        mid = close.rolling(length, min_periods=length).mean()
        std = close.rolling(length, min_periods=length).std()
        upper = mid + std_dev * std
        lower = mid - std_dev * std
        width_pct = ((upper - lower) / mid.replace(0, pd.NA)).fillna(0.0)

        d["bb_mid"] = mid
        d["bb_upper"] = upper
        d["bb_lower"] = lower
        d["bb_band_width_pct"] = width_pct

        # Backward-compatible column name for anything still reading bb_width.
        d["bb_width"] = width_pct

        valid = width_pct >= min_band_width_pct
        prev_close = close.shift(1)
        prev_lower = lower.shift(1)
        prev_upper = upper.shift(1)

        # Price was below the lower band, then re-entered above the lower band.
        long_reentry = (prev_close < prev_lower) & (close >= lower) & valid
        d.loc[long_reentry.fillna(False), "signal"] = 1

        if allow_short:
            # Price was above the upper band, then re-entered below the upper band.
            short_reentry = (prev_close > prev_upper) & (close <= upper) & valid
            d.loc[short_reentry.fillna(False), "signal"] = -1

        if exit_on_mid:
            # Neutral signal when price reaches/re-crosses the mean. The runner decides
            # whether signal=0 exits an open position based on exit_on_signal/state.
            prev_mid = mid.shift(1)
            long_mid_exit = (prev_close < prev_mid) & (close >= mid)
            short_mid_exit = (prev_close > prev_mid) & (close <= mid)
            d.loc[(long_mid_exit | short_mid_exit).fillna(False), "signal"] = 0

        return d
