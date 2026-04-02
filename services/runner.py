from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

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


def _to_utc_timestamp(value: Any) -> pd.Timestamp:
    if isinstance(value, pd.Timestamp):
        ts = value
    else:
        ts = pd.to_datetime(value, errors="coerce", utc=True)

    if pd.isna(ts):
        raise ValueError(f"Invalid timestamp in backtest loop: {value!r}")

    if ts.tzinfo is None:
        return ts.tz_localize("UTC")

    return ts.tz_convert("UTC")


def _day_key_from_ts(value: Any) -> str:
    ts = _to_utc_timestamp(value)
    return ts.strftime("%Y-%m-%d")


def _compute_atr(
    df: pd.DataFrame,
    *,
    period: int,
) -> pd.Series:
    safe_period = max(1, int(period or 14))

    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    prev_close = close.shift(1)

    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.rolling(window=safe_period, min_periods=safe_period).mean()
    return atr


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
    exit_mode: str = "static",
    atr_period: int = 14,
    atr_stop_mult: float | None = 1.5,
    atr_take_mult: float | None = 2.5,
) -> RunOutput:
    """
    Expects df to have columns: timestamp, close, signal.
    For ATR exit mode, expects OHLC columns: high, low, close.
    Uses bar time (not wall-clock time) for all backtest risk timing.
    """

    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df).copy()
    else:
        df = df.copy()

    risk_engine = risk_engine or RiskEngine()
    normalized_exit_mode = risk_engine._normalize_exit_mode(exit_mode)

    if normalized_exit_mode == "atr":
        required_cols = {"high", "low", "close"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(
                f"ATR exit mode requires OHLC columns. Missing: {sorted(missing)}"
            )
        df["atr"] = _compute_atr(df, period=atr_period)

    trades: list[dict] = []
    equity_curve: list[dict] = []
    risk_events: list[dict] = []
    liquidated = False
    trade_id = 0

    open_trade_equity_baseline = float(state.cash)

    trades_today = 0
    last_exit_ts: Any = None
    peak_equity = float(state.cash)
    trade_day: str | None = None

    for i in range(len(df)):
        ts = _to_utc_timestamp(df["timestamp"].iloc[i])
        ts_iso = ts.isoformat()
        close = float(df["close"].iloc[i])
        signal = int(df["signal"].iloc[i])

        current_atr: float | None = None
        if normalized_exit_mode == "atr":
            atr_raw = df["atr"].iloc[i]
            if pd.notna(atr_raw):
                current_atr = float(atr_raw)

        day_key = _day_key_from_ts(ts)
        if trade_day != day_key:
            trade_day = day_key
            trades_today = 0

        current_equity = float(engine.mark_equity(state, market_type, close))
        peak_equity = max(peak_equity, current_equity)

        # 1) Hard liquidation guard first
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
                open_trade_equity_baseline = float(state.cash)
                last_exit_ts = getattr(fill, "timestamp", None) or ts_iso
                risk_events.append(
                    {
                        "timestamp": ts_iso,
                        "event": "liquidation",
                        "reason": "maintenance_margin",
                        "meta": {
                            "market_price": close,
                            "maintenance_margin_check": True,
                        },
                    }
                )
                trade_id += 1

        # 2) Risk exit (static or ATR)
        if state.position_qty > 0.0:
            exit_signal = risk_engine.evaluate_long_exit(
                entry_price=state.entry_price,
                market_price=close,
                stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct,
                exit_mode=normalized_exit_mode,
                atr_value=current_atr,
                atr_stop_mult=atr_stop_mult,
                atr_take_mult=atr_take_mult,
            )
            if exit_signal.should_exit:
                trade_id += 1
                state, fill = engine.exit_long(ts_iso, state, close, market_type, trade_id)
                if fill:
                    realized_pnl = float(fill.equity_after) - float(open_trade_equity_baseline)
                    fill.pnl = realized_pnl
                    trades.append(fill.__dict__)
                    open_trade_equity_baseline = float(fill.equity_after)
                    last_exit_ts = getattr(fill, "timestamp", None) or ts_iso
                    risk_events.append(
                        {
                            "timestamp": ts_iso,
                            "event": "risk_event",
                            "reason": exit_signal.reason,
                            "meta": exit_signal.meta or {},
                        }
                    )

        # 3) Entry
        if signal == 1 and state.position_qty == 0.0:
            gate = risk_engine.evaluate_entry_gate(
                now_ts=ts,
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
                state, fill = engine.enter_long(
                    ts_iso,
                    state,
                    close,
                    market_type,
                    leverage,
                    trade_id,
                    qty_override=qty_override,
                )
                if fill:
                    trades.append(fill.__dict__)
                    open_trade_equity_baseline = float(engine.mark_equity(state, market_type, close))
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

        # 4) Strategy exit
        elif signal == -1 and state.position_qty > 0.0:
            trade_id += 1
            state, fill = engine.exit_long(ts_iso, state, close, market_type, trade_id)
            if fill:
                realized_pnl = float(fill.equity_after) - float(open_trade_equity_baseline)
                fill.pnl = realized_pnl
                trades.append(fill.__dict__)
                open_trade_equity_baseline = float(fill.equity_after)
                last_exit_ts = getattr(fill, "timestamp", None) or ts_iso

        eq = engine.mark_equity(state, market_type, close)
        peak_equity = max(peak_equity, float(eq))

        if include_equity:
            if equity_stride <= 1 or (i % equity_stride == 0):
                point = {
                    "timestamp": ts_iso,
                    "equity": float(eq),
                }
                if normalized_exit_mode == "atr":
                    point["atr"] = float(current_atr) if current_atr is not None else None
                equity_curve.append(point)

    final_equity = float(engine.mark_equity(state, market_type, float(df["close"].iloc[-1])))

    return RunOutput(
        state=state,
        trades=trades,
        equity_curve=equity_curve,
        final_equity=final_equity,
        liquidated=liquidated,
        risk_events=risk_events,
    )