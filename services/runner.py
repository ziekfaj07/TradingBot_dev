# services/runner.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.execution_models import PortfolioState
from services.execution_engine import ExecutionEngine


@dataclass
class RunOutput:
    state: PortfolioState
    trades: list[dict]
    equity_curve: list[dict]
    final_equity: float
    liquidated: bool


def run_signal_backed_loop(
    df: Any,
    *,
    engine: ExecutionEngine,
    state: PortfolioState,
    market_type: str,
    leverage: float,
    include_equity: bool = False,
    equity_stride: int = 1,
) -> RunOutput:
    """
    Transplanted from BacktestService (the per-bar loop + equity/trade tracking).
    Expects df to have columns: timestamp, close, signal.
    """

    trades: list[dict] = []
    equity_curve: list[dict] = []

    liquidated = False
    trade_id = 0

    # for per-trade pnl tracking
    # baseline starts from initial state cash (same meaning as your initial_balance)
    open_trade_equity_baseline = float(state.cash)

    for i in range(len(df)):
        ts = df["timestamp"].iloc[i]
        ts_iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        close = float(df["close"].iloc[i])
        signal = int(df["signal"].iloc[i])

        if (
            engine.compute_liquidation_price(
                state=state,
                market_type=market_type,
                market_price=close,
                leverage=leverage,
            )
            and state.position_qty != 0.0
        ):
            state, fill = engine.liquidate(ts_iso, state, close, market_type, trade_id)
            if fill:
                trades.append(fill.__dict__)
                liquidated = True
                open_trade_equity_baseline = state.cash
            trade_id += 1

        if signal == 1 and state.position_qty == 0.0:
            trade_id += 1
            state, fill = engine.enter_long(ts_iso, state, close, market_type, leverage, trade_id)
            if fill:
                trades.append(fill.__dict__)
                open_trade_equity_baseline = engine.mark_equity(state, market_type, close)

        elif signal == -1 and state.position_qty > 0.0:
            trade_id += 1
            state, fill = engine.exit_long(ts_iso, state, close, market_type, trade_id)
            if fill:
                realized_pnl = float(fill.equity_after) - float(open_trade_equity_baseline)
                fill.pnl = realized_pnl
                trades.append(fill.__dict__)
                open_trade_equity_baseline = float(fill.equity_after)

        # mark-to-market equity
        eq = engine.mark_equity(state, market_type, close)
        if include_equity:
            if equity_stride <= 1 or (i % equity_stride == 0):
                equity_curve.append({"timestamp": ts_iso, "equity": float(eq)})

    final_equity = float(engine.mark_equity(state, market_type, float(df["close"].iloc[-1])))

    return RunOutput(
        state=state,
        trades=trades,
        equity_curve=equity_curve,
        final_equity=final_equity,
        liquidated=liquidated,
    )