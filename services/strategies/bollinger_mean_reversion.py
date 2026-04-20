from __future__ import annotations

from typing import Any

import pandas as pd

from services.strategies.base import BaseStrategy


class BollingerMeanReversion(BaseStrategy):
    name = "bollinger_mean_reversion"
    display_name = "Bollinger Mean Reversion"
    description = (
        "Mean-reversion strategy: long after lower-band re-entry; "
        "short/exit after upper-band re-entry."
    )
    default_params: dict[str, Any] = {
        "length": 20,
        "stddev": 2.0,
        "min_bandwidth_pct": 0.0,
    }
    min_bars = 22

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        d = df.copy()
        if d.empty or "close" not in d.columns:
            d["signal"] = 0
            return d

        length = max(2, int(self.params.get("length", 20)))
        # Backward-compatible aliases from the early BB draft.
        stddev = float(
            self.params.get(
                "stddev",
                self.params.get("std_dev", 2.0),
            )
        )
        min_bandwidth_pct = float(
            self.params.get(
                "min_bandwidth_pct",
                self.params.get("min_bandwidth", 0.0),
            )
        )

        close = pd.to_numeric(d["close"], errors="coerce")
        mid = close.rolling(length).mean()
        std = close.rolling(length).std()

        d["bb_mid"] = mid
        d["bb_std"] = std
        d["bb_upper"] = mid + stddev * std
        d["bb_lower"] = mid - stddev * std
        d["bb_width_pct"] = ((d["bb_upper"] - d["bb_lower"]) / mid.abs()) * 100.0

        d["signal"] = 0
        prev_close = close.shift(1)
        prev_lower = d["bb_lower"].shift(1)
        prev_upper = d["bb_upper"].shift(1)

        vol_ok = d["bb_width_pct"].fillna(0.0) >= min_bandwidth_pct
        long_reentry = (prev_close < prev_lower) & (close > d["bb_lower"])
        short_reentry = (prev_close > prev_upper) & (close < d["bb_upper"])

        d.loc[long_reentry & vol_ok, "signal"] = 1
        d.loc[short_reentry & vol_ok, "signal"] = -1
        d["signal"] = d["signal"].fillna(0).astype(int)
        return d
