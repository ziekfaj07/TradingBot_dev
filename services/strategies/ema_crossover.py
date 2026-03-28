from __future__ import annotations

import pandas as pd

from services.strategies.base import BaseStrategy


class EmaCrossoverStrategy(BaseStrategy):
    name = "ema_crossover"
    display_name = "EMA Crossover"
    description = "Classic fast/slow EMA crossover."
    default_params = {"short": 9, "long": 21}
    min_bars = 21

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        d = df.copy()

        short = int(self.params.get("short", 9))
        long = int(self.params.get("long", 21))

        if "close" not in d.columns:
            raise ValueError("EMA crossover strategy requires a 'close' column.")

        if len(d) < max(short, long):
            d["signal"] = 0
            return d

        d["ema_short"] = d["close"].ewm(span=short, adjust=False).mean()
        d["ema_long"] = d["close"].ewm(span=long, adjust=False).mean()

        prev = d["ema_short"].shift(1) - d["ema_long"].shift(1)
        curr = d["ema_short"] - d["ema_long"]

        d["signal"] = 0
        d.loc[(prev <= 0) & (curr > 0), "signal"] = 1
        d.loc[(prev >= 0) & (curr < 0), "signal"] = -1

        return d