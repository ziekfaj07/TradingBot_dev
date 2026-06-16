from __future__ import annotations

from typing import Any

from services.strategies.base import BaseStrategy
from services.strategies.donchian_breakout import (
    DonchianBreakoutStrategy,
    DonchianBreakoutV2Strategy,
)
from services.strategies.ema_crossover import EmaCrossoverStrategy, EmaCrossoverV2Strategy
from services.strategies.three_candle_reversal import (
    ThreeCandleReversalStrategy,
    ThreeCandleReversalV2Strategy,
)
from services.strategies.bollinger_mean_reversion import (
    BollingerMeanReversion,
    BollingerMeanReversionV2,
)


STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    BollingerMeanReversion.name: BollingerMeanReversion,
    BollingerMeanReversionV2.name: BollingerMeanReversionV2,
    DonchianBreakoutStrategy.name: DonchianBreakoutStrategy,
    DonchianBreakoutV2Strategy.name: DonchianBreakoutV2Strategy,
    EmaCrossoverStrategy.name: EmaCrossoverStrategy,
    EmaCrossoverV2Strategy.name: EmaCrossoverV2Strategy,
    ThreeCandleReversalStrategy.name: ThreeCandleReversalStrategy,
    ThreeCandleReversalV2Strategy.name: ThreeCandleReversalV2Strategy,
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
