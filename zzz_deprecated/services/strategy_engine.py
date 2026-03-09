import pandas as pd


class StrategyEngine:
    @staticmethod
    def ema_crossover(df: pd.DataFrame, short: int = 9, long: int = 21) -> pd.DataFrame:
        d = df.copy()
        d["ema_short"] = d["close"].ewm(span=short, adjust=False).mean()
        d["ema_long"] = d["close"].ewm(span=long, adjust=False).mean()

        # signal: +1 enter long, -1 exit long (or enter short later)
        d["signal"] = 0

        # crossover detection
        prev = d["ema_short"].shift(1) - d["ema_long"].shift(1)
        curr = d["ema_short"] - d["ema_long"]

        # cross up
        d.loc[(prev <= 0) & (curr > 0), "signal"] = 1
        # cross down
        d.loc[(prev >= 0) & (curr < 0), "signal"] = -1

        return d