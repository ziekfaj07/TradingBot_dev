from __future__ import annotations

from typing import Any

import pandas as pd

from services.strategies.ema_crossover import EmaCrossoverStrategy


class StrategyEngine:
    _registry = {
        "ema_crossover": EmaCrossoverStrategy,
    }

    @classmethod
    def available_strategies(cls) -> list[str]:
        return sorted(cls._registry.keys())

    @classmethod
    def build(cls, name: str = "ema_crossover", **kwargs: Any):
        strategy_cls = cls._registry.get(name)
        if strategy_cls is None:
            raise ValueError(
                f"Unknown strategy '{name}'. Available: {', '.join(cls.available_strategies())}"
            )
        return strategy_cls(**kwargs)

    @classmethod
    def run(cls, df: pd.DataFrame, name: str = "ema_crossover", **kwargs: Any) -> pd.DataFrame:
        strategy = cls.build(name=name, **kwargs)
        return strategy.apply(df)

    @staticmethod
    def ema_crossover(df: pd.DataFrame, short: int = 9, long: int = 21) -> pd.DataFrame:
        """
        Backward-compatible shim so existing callers keep working
        during the v0.4.5 transition.
        """
        return EmaCrossoverStrategy(short=short, long=long).apply(df)