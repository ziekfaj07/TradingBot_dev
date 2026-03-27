from __future__ import annotations

import pandas as pd

from services.strategies.base import BaseStrategy


class EmaCrossoverStrategy(BaseStrategy):
    name = "ema_crossover"

    def __init__(self, short: int = 9, long: int = 21):
        self.short = short
        self.long = long

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        d = df.copy()

        d["ema_short"] = d["close"].ewm(span=self.short, adjust=False).mean()
        d["ema_long"] = d["close"].ewm(span=self.long, adjust=False).mean()

        d["signal"] = 0

        prev = d["ema_short"].shift(1) - d["ema_long"].shift(1)
        curr = d["ema_short"] - d["ema_long"]

        d.loc[(prev <= 0) & (curr > 0), "signal"] = 1
        d.loc[(prev >= 0) & (curr < 0), "signal"] = -1

        return d