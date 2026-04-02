from typing import Optional, Dict
import pandas as pd

from services.strategies.base import BaseStrategy


class BollingerMeanReversion(BaseStrategy):
    name = "bollinger_mean_reversion"
    display_name = "Bollinger Mean Reversion"
    description = "Enter long when price crosses below lower band, exit when it crosses back above."
    default_params = {"length": 20, "std_dev": 2.0, "min_bandwidth": 0.01}
    min_bars = 20

    def __init__(self, params: Dict):
        self.length = params.get("length", 20)
        self.std_dev = params.get("std_dev", 2.0)
        self.min_bandwidth = params.get("min_bandwidth", 0.01)  # volatility filter

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        df["bb_mid"] = df["close"].rolling(self.length).mean()
        df["bb_std"] = df["close"].rolling(self.length).std()

        df["bb_upper"] = df["bb_mid"] + self.std_dev * df["bb_std"]
        df["bb_lower"] = df["bb_mid"] - self.std_dev * df["bb_std"]

        # Bandwidth = (upper - lower) / mid
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

        return df

    def generate_signal(
        self,
        df: pd.DataFrame,
        allow_short: bool = False
    ) -> Optional[str]:

        if len(df) < self.length + 2:
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2]

        # --- Volatility filter ---
        if last["bb_width"] < self.min_bandwidth:
            return None

        # =========================
        # LONG SIGNAL
        # =========================
        prev_outside_lower = prev["close"] < prev["bb_lower"]
        reentry_long = last["close"] > last["bb_lower"]

        if prev_outside_lower and reentry_long:
            return "buy"

        # =========================
        # SHORT SIGNAL
        # =========================
        if allow_short:
            prev_outside_upper = prev["close"] > prev["bb_upper"]
            reentry_short = last["close"] < last["bb_upper"]

            if prev_outside_upper and reentry_short:
                return "sell"

        return None