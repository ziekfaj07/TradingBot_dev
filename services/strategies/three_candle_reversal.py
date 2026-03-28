from __future__ import annotations

import pandas as pd

from services.strategies.base import BaseStrategy


class ThreeCandleReversalStrategy(BaseStrategy):
    name = "three_candle_reversal"
    display_name = "3-Candle Reversal"
    description = (
        "Bullish when the current green candle engulfs the prior 3 red candles. "
        "Bearish when the current red candle engulfs the prior 3 green candles."
    )
    default_params = {
        "min_body_ratio": 0.55,
        "require_full_range_engulf": True,
        "confirm_break_prev_extreme": True,
    }
    min_bars = 4

    @staticmethod
    def _body_ratio(row: pd.Series) -> float:
        high = float(row["high"])
        low = float(row["low"])
        spread = max(high - low, 1e-12)
        body = abs(float(row["close"]) - float(row["open"]))
        return body / spread

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        required = {"open", "high", "low", "close"}
        missing = required.difference(df.columns)
        if missing:
            raise ValueError(
                f"3-candle reversal strategy requires columns: {sorted(required)}"
            )

        d = df.copy()
        d["signal"] = 0
        d["pattern"] = None

        if len(d) < 4:
            return d

        min_body_ratio = float(self.params.get("min_body_ratio", 0.55))
        require_full_range_engulf = bool(
            self.params.get("require_full_range_engulf", True)
        )
        confirm_break_prev_extreme = bool(
            self.params.get("confirm_break_prev_extreme", True)
        )

        for i in range(3, len(d)):
            c0 = d.iloc[i]
            prev3 = d.iloc[i - 3:i]

            current_green = float(c0["close"]) > float(c0["open"])
            current_red = float(c0["close"]) < float(c0["open"])

            prev3_all_red = (prev3["close"] < prev3["open"]).all()
            prev3_all_green = (prev3["close"] > prev3["open"]).all()

            current_body_ok = self._body_ratio(c0) >= min_body_ratio

            prev_high = float(prev3["high"].max())
            prev_low = float(prev3["low"].min())
            prev_open_max = float(prev3["open"].max())
            prev_open_min = float(prev3["open"].min())
            prev_close_max = float(prev3["close"].max())
            prev_close_min = float(prev3["close"].min())

            bullish = False
            bearish = False

            if current_green and prev3_all_red and current_body_ok:
                if require_full_range_engulf:
                    bullish = (
                        float(c0["low"]) <= prev_low
                        and float(c0["high"]) >= prev_high
                    )
                else:
                    bullish = (
                        float(c0["open"]) <= min(prev_open_min, prev_close_min)
                        and float(c0["close"]) >= max(prev_open_max, prev_close_max)
                    )
                if bullish and confirm_break_prev_extreme:
                    bullish = float(c0["close"]) > prev_high

            if current_red and prev3_all_green and current_body_ok:
                if require_full_range_engulf:
                    bearish = (
                        float(c0["high"]) >= prev_high
                        and float(c0["low"]) <= prev_low
                    )
                else:
                    bearish = (
                        float(c0["open"]) >= max(prev_open_max, prev_close_max)
                        and float(c0["close"]) <= min(prev_open_min, prev_close_min)
                    )
                if bearish and confirm_break_prev_extreme:
                    bearish = float(c0["close"]) < prev_low

            if bullish:
                d.at[d.index[i], "signal"] = 1
                d.at[d.index[i], "pattern"] = "bullish_3cr"
            elif bearish:
                d.at[d.index[i], "signal"] = -1
                d.at[d.index[i], "pattern"] = "bearish_3cr"

        return d