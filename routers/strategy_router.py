from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from services.market_data_service import GateIOService
from services.strategy_engine import StrategyEngine
from services.strategies.registry import get_strategy, list_strategies

router = APIRouter(prefix="/api/strategies", tags=["strategies"])

market_service = GateIOService()


def _bars_to_df(bars: list[dict]) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(bars).copy()

    expected = ["timestamp", "open", "high", "low", "close", "volume"]
    for col in expected:
        if col not in df.columns:
            df[col] = 0.0

    return df[expected]


@router.get("")
def get_strategy_catalog():
    return {"strategies": list_strategies()}


@router.get("/{strategy_name}")
def get_strategy_info(strategy_name: str):
    try:
        strategy = get_strategy(strategy_name)
        return strategy.meta()
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{strategy_name}/preview")
def preview_strategy(
    strategy_name: str,
    symbol: str,
    interval: str = "1m",
    limit: int = Query(default=200, ge=20, le=2000),
    short: int | None = None,
    long: int | None = None,
    lookback: int | None = None,
    min_body_ratio: float | None = None,
    require_full_range_engulf: bool | None = None,
    confirm_break_prev_extreme: bool | None = None,
    volume_spike_mult: float | None = Query(default=None, gt=0.0),
    volume_spike_lookback: int | None = Query(default=None, ge=1),
):
    try:
        params: dict[str, Any] = {}

        optional_params = {
            "short": short,
            "long": long,
            "lookback": lookback,
            "min_body_ratio": min_body_ratio,
            "require_full_range_engulf": require_full_range_engulf,
            "confirm_break_prev_extreme": confirm_break_prev_extreme,
            "volume_spike_mult": volume_spike_mult,
            "volume_spike_lookback": volume_spike_lookback,
        }

        for key, value in optional_params.items():
            if value is not None:
                params[key] = value

        bars = market_service.get_candles(symbol=symbol, interval=interval, limit=limit)
        df = _bars_to_df(bars)

        out = StrategyEngine.apply(
            df=df,
            strategy_name=strategy_name,
            strategy_params=params,
        )

        latest_signal = 0
        if not out.empty and "signal" in out.columns:
            latest_signal = int(out["signal"].fillna(0).iloc[-1])

        signal_rows = out[out["signal"] != 0].copy() if "signal" in out.columns else pd.DataFrame()
        preview_rows = signal_rows.tail(20).to_dict(orient="records")

        return {
            "symbol": symbol.upper(),
            "interval": interval,
            "strategy_name": strategy_name,
            "strategy_params": params,
            "bars": len(df),
            "latest_signal": latest_signal,
            "signals": preview_rows,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
