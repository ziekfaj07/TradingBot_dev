from __future__ import annotations

from dataclasses import dataclass
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
    metrics: dict


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
    atr_reference_mode: str = "entry",
    include_trades: bool = True,
    include_risk_events: bool = True,
    debug_risk_telemetry: bool = False,
    max_equity_points: int | None = 2000,
    max_trades_returned: int | None = None,
    max_risk_events_returned: int | None = None,
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
    normalized_atr_reference_mode = risk_engine._normalize_atr_reference_mode(
        atr_reference_mode
    )

    if normalized_exit_mode == "atr":
        required_cols = {"high", "low", "close"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(
                f"ATR exit mode requires OHLC columns. Missing: {sorted(missing)}"
            )
        df["atr"] = _compute_atr(df, period=atr_period)

    row_count = len(df)

    safe_equity_stride = max(1, int(equity_stride or 1))
    if include_equity and max_equity_points is not None and max_equity_points > 0:
        min_stride_for_cap = max(
            1, (row_count + max_equity_points - 1) // max_equity_points
        )
        safe_equity_stride = max(safe_equity_stride, min_stride_for_cap)

    timestamps = df["timestamp"].tolist()
    closes = pd.to_numeric(df["close"], errors="coerce").tolist()
    signals = (
        pd.to_numeric(df["signal"], errors="coerce").fillna(0).astype(int).tolist()
    )
    atr_values = df["atr"].tolist() if normalized_exit_mode == "atr" else None

    trades: list[dict] = []
    equity_curve: list[dict] = []
    risk_events: list[dict] = []
    liquidated = False
    trade_id = 0

    initial_balance = float(state.cash)    
    open_trade_equity_baseline = float(state.cash)

    trades_today = 0
    last_exit_ts: Any = None
    peak_equity = float(state.cash)
    trade_day: str | None = None

    active_atr_value: float | None = None
    active_stop_price: float | None = None
    active_take_price: float | None = None

    for i in range(row_count):
        ts = _to_utc_timestamp(timestamps[i])
        ts_iso = ts.isoformat()
        close = float(closes[i])
        signal = int(signals[i])

        current_atr: float | None = None
        if normalized_exit_mode == "atr":
            atr_raw = atr_values[i] if atr_values is not None else None
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

                active_atr_value = None
                active_stop_price = None
                active_take_price = None

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
            atr_for_exit: float | None = current_atr
            if (
                normalized_exit_mode == "atr"
                and normalized_atr_reference_mode == "entry"
            ):
                atr_for_exit = active_atr_value

            exit_signal = risk_engine.evaluate_long_exit(
                entry_price=state.entry_price,
                market_price=close,
                stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct,
                exit_mode=normalized_exit_mode,
                atr_value=atr_for_exit,
                atr_stop_mult=atr_stop_mult,
                atr_take_mult=atr_take_mult,
            )

            if exit_signal.meta is None:
                exit_signal.meta = {}

            exit_signal.meta["atr_reference_mode"] = normalized_atr_reference_mode
            exit_signal.meta["atr_active_value"] = atr_for_exit
            exit_signal.meta["atr_entry_value"] = active_atr_value
            exit_signal.meta["active_stop_price"] = active_stop_price
            exit_signal.meta["active_take_price"] = active_take_price

            if exit_signal.should_exit:
                trade_id += 1
                state, fill = engine.exit_long(
                    ts_iso, state, close, market_type, trade_id
                )
                if fill:
                    realized_pnl = float(fill.equity_after) - float(
                        open_trade_equity_baseline
                    )
                    fill.pnl = realized_pnl
                    trades.append(fill.__dict__)
                    open_trade_equity_baseline = float(fill.equity_after)
                    last_exit_ts = getattr(fill, "timestamp", None) or ts_iso
                    active_atr_value = None
                    active_stop_price = None
                    active_take_price = None
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
            if (
                normalized_exit_mode == "atr"
                and normalized_atr_reference_mode == "entry"
                and (current_atr is None or current_atr <= 0.0)
            ):
                risk_events.append(
                    {
                        "timestamp": ts_iso,
                        "event": "entry_blocked",
                        "reason": "atr_not_ready",
                        "meta": {
                            "exit_mode": normalized_exit_mode,
                            "atr_reference_mode": normalized_atr_reference_mode,
                            "atr_value": current_atr,
                            "atr_period": atr_period,
                        },
                    }
                )
            else:
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
                        open_trade_equity_baseline = float(
                            engine.mark_equity(state, market_type, close)
                        )
                        trades_today += 1

                        if normalized_exit_mode == "atr":
                            if normalized_atr_reference_mode == "entry":
                                active_atr_value = (
                                    float(current_atr)
                                    if current_atr is not None
                                    else None
                                )
                            else:
                                active_atr_value = None

                            entry_levels = risk_engine.resolve_long_exit_levels(
                                entry_price=state.entry_price,
                                stop_loss_pct=stop_loss_pct,
                                take_profit_pct=take_profit_pct,
                                exit_mode=normalized_exit_mode,
                                atr_value=(
                                    active_atr_value
                                    if normalized_atr_reference_mode == "entry"
                                    else current_atr
                                ),
                                atr_stop_mult=atr_stop_mult,
                                atr_take_mult=atr_take_mult,
                            )
                            active_stop_price = entry_levels.stop_price
                            active_take_price = entry_levels.take_price

                            trades[-1]["atr_reference_mode"] = (
                                normalized_atr_reference_mode
                            )
                            trades[-1]["atr_entry_value"] = active_atr_value
                            trades[-1]["stop_price"] = active_stop_price
                            trades[-1]["take_price"] = active_take_price
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
                realized_pnl = float(fill.equity_after) - float(
                    open_trade_equity_baseline
                )
                fill.pnl = realized_pnl
                trades.append(fill.__dict__)
                open_trade_equity_baseline = float(fill.equity_after)
                last_exit_ts = getattr(fill, "timestamp", None) or ts_iso
                active_atr_value = None
                active_stop_price = None
                active_take_price = None

        eq = engine.mark_equity(state, market_type, close)
        peak_equity = max(peak_equity, float(eq))

        if include_equity:
            if safe_equity_stride <= 1 or (i % safe_equity_stride == 0):
                point = {
                    "timestamp": ts_iso,
                    "equity": float(eq),
                }

                if normalized_exit_mode == "atr" and debug_risk_telemetry:
                    point["atr"] = (
                        float(current_atr) if current_atr is not None else None
                    )
                    point["atr_reference_mode"] = normalized_atr_reference_mode
                    point["atr_active_value"] = (
                        float(active_atr_value)
                        if active_atr_value is not None
                        else None
                    )
                    point["active_stop_price"] = active_stop_price
                    point["active_take_price"] = active_take_price

                equity_curve.append(point)

    final_equity = float(engine.mark_equity(state, market_type, float(closes[-1])))

    returned_trades = trades if include_trades else []
    returned_risk_events = risk_events if include_risk_events else []

    if max_trades_returned is not None and max_trades_returned >= 0:
        returned_trades = returned_trades[-max_trades_returned:]

    if max_risk_events_returned is not None and max_risk_events_returned >= 0:
        returned_risk_events = returned_risk_events[-max_risk_events_returned:]

    closed_trades = [
        t
        for t in trades
        if str(t.get("type", "")).upper() == "EXIT" and t.get("pnl") is not None
    ]

    total_trades = len(closed_trades)
    wins = sum(1 for t in closed_trades if float(t.get("pnl", 0.0)) > 0.0)
    losses = sum(1 for t in closed_trades if float(t.get("pnl", 0.0)) < 0.0)

    gross_profit = sum(
        float(t.get("pnl", 0.0))
        for t in closed_trades
        if float(t.get("pnl", 0.0)) > 0.0
    )
    gross_loss = abs(
        sum(
            float(t.get("pnl", 0.0))
            for t in closed_trades
            if float(t.get("pnl", 0.0)) < 0.0
        )
    )

    win_rate = (wins / total_trades) if total_trades > 0 else 0.0
    avg_pnl = (
        sum(float(t.get("pnl", 0.0)) for t in closed_trades) / total_trades
        if total_trades > 0
        else 0.0
    )
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    starting_equity = initial_balance
    peak_seen = starting_equity
    max_drawdown_pct_seen = 0.0

    if equity_curve:
        equity_scan_source = equity_curve
    else:
        equity_scan_source = [{"equity": starting_equity}]
        for t in trades:
            eq_after = t.get("equity_after")
            if eq_after is not None:
                equity_scan_source.append({"equity": float(eq_after)})
        equity_scan_source.append({"equity": final_equity})

    for point in equity_scan_source:
        eq_val = float(point.get("equity", final_equity))
        if eq_val > peak_seen:
            peak_seen = eq_val
        if peak_seen > 0:
            dd = (peak_seen - eq_val) / peak_seen
            if dd > max_drawdown_pct_seen:
                max_drawdown_pct_seen = dd

    metrics = {
        "bar_count": row_count,
        "trade_count": total_trades,
        "win_count": wins,
        "loss_count": losses,
        "win_rate": win_rate,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "avg_pnl": avg_pnl,
        "profit_factor": profit_factor,
        "final_equity": final_equity,
        "return_pct": (
            ((final_equity - starting_equity) / starting_equity) * 100.0
            if starting_equity > 0.0
            else 0.0
        ),
        "max_drawdown_pct": max_drawdown_pct_seen * 100.0,
        "liquidated": liquidated,
        "returned_trade_count": len(returned_trades),
        "returned_risk_event_count": len(returned_risk_events),
        "returned_equity_point_count": len(equity_curve),
        "equity_stride_used": safe_equity_stride,
    }

    return RunOutput(
        state=state,
        trades=returned_trades,
        equity_curve=equity_curve,
        final_equity=final_equity,
        liquidated=liquidated,
        risk_events=returned_risk_events,
        metrics=metrics,
    )