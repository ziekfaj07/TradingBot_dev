from __future__ import annotations

import pandas as pd

from services.strategies.base import BaseStrategy


class DonchianBreakoutStrategy(BaseStrategy):
    name = "donchian_breakout"
    display_name = "Donchian Breakout"
    description = "Breakout above or below prior N-bar channel."
    default_params = {"lookback": 20}
    min_bars = 20

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        required = {"high", "low", "close"}
        missing = required.difference(df.columns)
        if missing:
            raise ValueError(
                f"Donchian breakout strategy requires columns: {sorted(required)}"
            )

        d = df.copy()
        lookback = int(self.params.get("lookback", 20))

        if len(d) < lookback + 1:
            d["signal"] = 0
            return d

        d["channel_high"] = d["high"].rolling(lookback).max().shift(1)
        d["channel_low"] = d["low"].rolling(lookback).min().shift(1)

        d["signal"] = 0
        d.loc[d["close"] > d["channel_high"], "signal"] = 1
        d.loc[d["close"] < d["channel_low"], "signal"] = -1

        return d