from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class BaseStrategy(ABC):
    """
    Base interface for all strategies.

    Contract:
    - input: OHLCV dataframe
    - output: dataframe copy with at least a `signal` column
    - signal semantics:
        1  = bullish / buy / enter long
       -1  = bearish / sell / exit long / enter short later
        0  = hold / no action
    """

    name: str = "base"
    display_name: str = "Base Strategy"
    description: str = ""
    default_params: dict[str, Any] = {}
    min_bars: int = 1

    def __init__(self, **params: Any) -> None:
        merged = dict(self.default_params)
        merged.update(params or {})
        self.params = merged

    @abstractmethod
    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError

    def latest_signal(self, df: pd.DataFrame) -> int:
        out = self.apply(df)
        if out.empty or "signal" not in out.columns:
            return 0
        try:
            return int(out["signal"].fillna(0).iloc[-1])
        except Exception:
            return 0

    def apply_volume_spike_filter(self, df: pd.DataFrame) -> pd.DataFrame:
        """Gate non-zero signals behind a current-volume spike.

        Version-2 strategies use this helper after computing their normal
        signals. The rolling baseline is shifted by one bar so the current
        candle's volume is not included in its own threshold.
        """
        if "signal" not in df.columns:
            return df
        if "volume" not in df.columns:
            raise ValueError(
                f"{self.display_name} requires a 'volume' column for volume spike filtering."
            )

        multiplier = max(0.000001, float(self.params.get("volume_spike_mult", 1.5)))
        lookback = max(1, int(self.params.get("volume_spike_lookback", 20)))

        d = df.copy()
        volume = pd.to_numeric(d["volume"], errors="coerce").fillna(0.0)
        baseline = volume.rolling(lookback, min_periods=lookback).mean().shift(1)
        threshold = baseline * multiplier
        spike_ok = (baseline > 0.0) & (volume >= threshold)

        d["volume_spike_baseline"] = baseline
        d["volume_spike_threshold"] = threshold
        d["volume_spike_ok"] = spike_ok.fillna(False)

        blocked = (d["signal"].fillna(0) != 0) & (~d["volume_spike_ok"])
        d.loc[blocked, "signal"] = 0
        if "pattern" in d.columns:
            d.loc[blocked, "pattern"] = None

        return d

    @classmethod
    def meta(cls) -> dict[str, Any]:
        return {
            "name": cls.name,
            "display_name": cls.display_name,
            "description": cls.description,
            "default_params": dict(cls.default_params),
            "min_bars": cls.min_bars,
        }
