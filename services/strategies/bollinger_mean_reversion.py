from __future__ import annotations

import pandas as pd

from services.strategies.base import BaseStrategy


class BollingerMeanReversion(BaseStrategy):
    name = "bollinger_mean_reversion"
    display_name = "Bollinger Mean Reversion"
    description = (
        "Enter long when price re-enters from below the lower Bollinger band; "
        "optionally emit short signals when price re-enters from above the upper band."
    )
    default_params = {
        "length": 20,
        "std_dev": 2.0,
        "min_bandwidth": 0.01,
        "allow_short": False,
    }
    min_bars = 22

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        if "close" not in df.columns:
            raise ValueError("Bollinger mean reversion strategy requires a 'close' column.")

        d = df.copy()
        d["signal"] = 0

        length = max(2, int(self.params.get("length", 20)))
        std_dev = float(self.params.get("std_dev", 2.0))
        min_bandwidth = max(0.0, float(self.params.get("min_bandwidth", 0.01)))
        allow_short = bool(self.params.get("allow_short", False))

        if len(d) < length + 2:
            return d

        close = pd.to_numeric(d["close"], errors="coerce")
        mid = close.rolling(length, min_periods=length).mean()
        std = close.rolling(length, min_periods=length).std()
        upper = mid + std_dev * std
        lower = mid - std_dev * std
        width = (upper - lower) / mid.replace(0, pd.NA)

        d["bb_mid"] = mid
        d["bb_upper"] = upper
        d["bb_lower"] = lower
        d["bb_width"] = width

        valid = width >= min_bandwidth
        prev_close = close.shift(1)
        prev_lower = lower.shift(1)
        prev_upper = upper.shift(1)

        long_reentry = (prev_close < prev_lower) & (close > lower) & valid
        d.loc[long_reentry.fillna(False), "signal"] = 1

        if allow_short:
            short_reentry = (prev_close > prev_upper) & (close < upper) & valid
            d.loc[short_reentry.fillna(False), "signal"] = -1

        return d
