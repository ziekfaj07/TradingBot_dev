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

    @classmethod
    def meta(cls) -> dict[str, Any]:
        return {
            "name": cls.name,
            "display_name": cls.display_name,
            "description": cls.description,
            "default_params": dict(cls.default_params),
            "min_bars": cls.min_bars,
        }