from __future__ import annotations

from typing import Any

from services.strategies.base import BaseStrategy
from services.strategies.donchian_breakout import DonchianBreakoutStrategy
from services.strategies.ema_crossover import EmaCrossoverStrategy
from services.strategies.three_candle_reversal import ThreeCandleReversalStrategy
from services.strategies.bollinger_mean_reversion import BollingerMeanReversion


STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    BollingerMeanReversion.name: BollingerMeanReversion,  
    DonchianBreakoutStrategy.name: DonchianBreakoutStrategy,
    EmaCrossoverStrategy.name: EmaCrossoverStrategy,
    ThreeCandleReversalStrategy.name: ThreeCandleReversalStrategy,
}


def get_strategy(name: str, **params: Any) -> BaseStrategy:
    key = (name or "").strip().lower()
    cls = STRATEGY_REGISTRY.get(key)
    if cls is None:
        available = ", ".join(sorted(STRATEGY_REGISTRY.keys()))
        raise ValueError(f"Unknown strategy '{name}'. Available: {available}")
    return cls(**params)


def list_strategies() -> list[dict[str, Any]]:
    return [cls.meta() for cls in STRATEGY_REGISTRY.values()]