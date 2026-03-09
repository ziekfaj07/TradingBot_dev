import pandas as pd


class EMAStrategy:

    def __init__(self, short_period=9, long_period=21):
        self.short_period = short_period
        self.long_period = long_period

    def generate_signal(self, prices):

        if len(prices) < self.long_period:
            return "NOT_ENOUGH_DATA"

        df = pd.DataFrame(prices, columns=["close"])

        df["ema_short"] = df["close"].ewm(span=self.short_period).mean()
        df["ema_long"] = df["close"].ewm(span=self.long_period).mean()

        latest = df.iloc[-1]
        previous = df.iloc[-2]

        # Golden cross
        if previous["ema_short"] < previous["ema_long"] and \
           latest["ema_short"] > latest["ema_long"]:
            return "BUY"

        # Death cross
        if previous["ema_short"] > previous["ema_long"] and \
           latest["ema_short"] < latest["ema_long"]:
            return "SELL"

        return "HOLD"