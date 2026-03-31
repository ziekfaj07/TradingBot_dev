# services/runner.py

from __future__ import annotations

import time

from dataclasses import dataclass
from typing import Any

from core.execution_models import PortfolioState
from services.execution_engine import ExecutionEngine
from services.risk_engine import RiskEngine


@dataclass
class RunOutput:
    state: PortfolioState
    trades: list[dict]
    equity_curve: list[dict]
    final_equity: float
    liquidated: bool
    risk_events: list[dict]


def run_signal_backed_loop(
    df: Any,
    *,
    engine: ExecutionEngine,
    state: PortfolioState,
    market_type: str,
    leverage: float,
    include_equity: bool = False,
    equity_stride: int = 1,
    risk_engine: RiskEngine | None = None,
    position_sizing_mode: str = "all-in",
    position_size_value: float | None = None,
    max_drawdown_pct: float | None = None,
    max_trades_per_day: int | None = None,
    cooldown_seconds: int = 0,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
) -> RunOutput:
    """
    Transplanted from BacktestService (the per-bar loop + equity/trade tracking).
    Expects df to have columns: timestamp, close, signal.
    """

    trades: list[dict] = []
    equity_curve: list[dict] = []
    risk_events: list[dict] = []
    liquidated = False
    trade_id = 0

    # for per-trade pnl tracking
    # baseline starts from initial state cash (same meaning as your initial_balance)
    open_trade_equity_baseline = float(state.cash)

    risk_engine = risk_engine or RiskEngine()
    trades_today = 0
    last_exit_ts: float | None = None
    peak_equity = float(state.cash)

    for i in range(len(df)):
        ts = df["timestamp"].iloc[i]
        ts_iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        close = float(df["close"].iloc[i])
        signal = int(df["signal"].iloc[i])

        current_equity = float(engine.mark_equity(state, market_type, close))
        peak_equity = max(peak_equity, current_equity)

        # Hard liquidation guard first
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
                last_exit_ts = time.time()
                risk_events.append(
                    {
                        "timestamp": ts_iso,
                        "event": "liquidation",
                        "reason": "maintenance_margin",
                    }
                )
                trade_id += 1

        # Static SL/TP
        if state.position_qty > 0.0:
            exit_signal = risk_engine.evaluate_long_exit(
                entry_price=state.entry_price,
                market_price=close,
                stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct,
            )
            if exit_signal.should_exit:
                trade_id += 1
                state, fill = engine.exit_long(ts_iso, state, close, market_type, trade_id)
                if fill:
                    realized_pnl = float(fill.equity_after) - float(open_trade_equity_baseline)
                    fill.pnl - realized_pnl
                    trades.append(fill.__dict__)
                    open_trade_equity_baseline = float(fill.equity_after)
                    risk_events.append(
                        {
                            "timestamp": ts_iso,
                            "event": "risk_event",
                            "reason": exit_signal.reason,
                            "meta": exit_signal.meta or {},
                        }
                    )

        # Entry
        if signal == 1 and state.position_qty == 0.0:
            gate = risk_engine.evaluate_entry_gate(
                now_ts=time.time(),
                current_equity=current_equity,
                peak_equity=peak_equity,
                trades_today=trades_today,
                last_exit_ts=last_exit_ts,
                max_drawdown_pct=max_drawdown_pct,
                max_trades_per_day=max_trades_per_day,
                cooldown_seconds=cooldown_seconds,
            )
            if gate.allowed:
                qty_override = risk_engine.compute_entry_qty(
                    state=state,
                    market_type=market_type,
                    entry_price=close,
                    leverage=leverage,
                    fee_rate=engine.fee_rate,
                    max_leverage=engine.max_leverage,
                    max_qty=engine.max_qty,
                    sizing_mode=position_sizing_mode,
                    sizing_value=position_size_value,
                )

                trade_id += 1
                state, fill = engine.enter_long(ts_iso, state, close, market_type, leverage, trade_id, qty_override=qty_override)
                if fill:
                    trades.append(fill.__dict__)
                    open_trade_equity_baseline = engine.mark_equity(state, market_type, close)
                    trades_today += 1
            else:
                risk_events.append(
                    {
                        "timestamp": ts_iso,
                        "event": "entry_blocked",
                        "reason": gate.reason,
                        "meta": gate.meta or {},
                    }
                )

        elif signal == -1 and state.position_qty > 0.0:
            trade_id += 1
            state, fill = engine.exit_long(ts_iso, state, close, market_type, trade_id)
            if fill:
                realized_pnl = float(fill.equity_after) - float(open_trade_equity_baseline)
                fill.pnl = realized_pnl
                trades.append(fill.__dict__)
                open_trade_equity_baseline = float(fill.equity_after)
                last_exit_ts = time.time()

        # mark-to-market equity
        eq = engine.mark_equity(state, market_type, close)
        peak_equity = max(peak_equity, float(eq))

        if include_equity:
            if equity_stride <= 1 or (i % equity_stride == 0):
                equity_curve.append(
                    {
                        "timestamp": ts_iso, 
                        "equity": float(eq),
                    }
                )

    final_equity = float(engine.mark_equity(state, market_type, float(df["close"].iloc[-1])))

    return RunOutput(
        state=state,
        trades=trades,
        equity_curve=equity_curve,
        final_equity=final_equity,
        liquidated=liquidated,
        risk_events=risk_events,
    )