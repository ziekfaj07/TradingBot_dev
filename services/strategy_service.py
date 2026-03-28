from __future__ import annotations

import pandas as pd

from services.strategies.ema_crossover import EmaCrossoverStrategy

class EMAStrategy:

    def __init__(self, short_period=9, long_period=21):
        self.impl = EmaCrossoverStrategy(short=short_period, long=long_period)

    def generate_signal(self, prices):
        if isinstance(prices, pd.DataFrame):
            df = prices.copy()
        else:
            df = pd.DataFrame(prices)

        if "close" not in df.columns:
            if len(df.columns) == 1:
                df.columns = ["close"]
            else:
                raise ValueError("EMA strategy requires a 'close' column.")

        signal = self.impl.latest_signal(df)

        if signal > 0:
            return "BUY"
        if signal < 0:
            return "SELL"
        return "HOLD"