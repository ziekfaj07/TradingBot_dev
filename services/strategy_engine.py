from __future__ import annotations

from typing import Any

import pandas as pd

from services.strategies.registry import get_strategy


class StrategyEngine:
    @staticmethod
    def apply(
        df: pd.DataFrame,
        strategy_name: str = "ema_crossover",
        strategy_params: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        strategy = get_strategy(strategy_name, **(strategy_params or {}))
        return strategy.apply(df)

    @staticmethod
    def latest_signal(
        df: pd.DataFrame,
        strategy_name: str = "ema_crossover",
        strategy_params: dict[str, Any] | None = None,
    ) -> int:
        strategy = get_strategy(strategy_name, **(strategy_params or {}))
        return strategy.latest_signal(df)

    @staticmethod
    def ema_crossover(
        df: pd.DataFrame,
        short: int = 9,
        long: int = 21,
    ) -> pd.DataFrame:
        # backward-compatible shim for legacy callers
        return StrategyEngine.apply(
            df=df,
            strategy_name="ema_crossover",
            strategy_params={"short": short, "long": long},
        )