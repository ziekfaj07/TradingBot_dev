from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from core.execution_models import PortfolioState
from services.execution_engine import ExecutionEngine
from services.margin_engine import (
    DEFAULT_MARGIN_TIERS,
    MarginSnapshot,
    MarginTier,
    MarginTierResolver,
    derive_mark_price,
    evaluate_position_margin,
)
from services.risk_engine import RiskEngine


@dataclass
class RunOutput:
    state: PortfolioState
    trades: list[dict]
    equity_curve: list[dict]
    final_equity: float
    liquidated: bool
    risk_events: list[dict]
    liquidation_count: int = 0
    max_margin_ratio: float | None = None
    closest_liquidation_distance_pct: float | None = None
    last_margin_snapshot: dict | None = None


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


def _snapshot_to_dict(snapshot: MarginSnapshot | None) -> dict | None:
    if snapshot is None:
        return None
    return {
        "mark_price": float(snapshot.mark_price),
        "notional": float(snapshot.notional),
        "unrealized_pnl": float(snapshot.unrealized_pnl),
        "collateral": float(snapshot.collateral),
        "maintenance_margin": float(snapshot.maintenance_margin),
        "maintenance_margin_rate": float(snapshot.maintenance_margin_rate),
        "maintenance_amount": float(snapshot.maintenance_amount),
        "margin_balance": float(snapshot.margin_balance),
        "margin_ratio": snapshot.margin_ratio,
        "liquidation_price": snapshot.liquidation_price,
        "bankruptcy_price": snapshot.bankruptcy_price,
        "should_liquidate": bool(snapshot.should_liquidate),
        "margin_mode": snapshot.margin_mode,
        "active_tier_cap": snapshot.active_tier_cap,
    }


def run_signal_backed_loop(
    df: Any,
    *,
    engine: ExecutionEngine,
    state: PortfolioState,
    market_type: str,
    leverage: float,
    allow_short: bool = False,    
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
    exit_on_signal: bool = True,    
    exit_mode: str = "static",
    atr_period: int = 14,
    atr_stop_mult: float | None = 1.5,
    atr_take_mult: float | None = 2.5,
    margin_mode: str = "isolated",
    enable_liquidation: bool = True,
    use_mark_price_for_liquidation: bool = True,
    mark_price_source: str = "close",
    maintenance_margin_override: float | None = None,
    margin_tiers: list[MarginTier] | None = None,
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
    resolver = MarginTierResolver(margin_tiers or DEFAULT_MARGIN_TIERS)

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
    liquidation_count = 0
    trade_id = 0
    open_trade_equity_baseline = float(state.cash)
    trades_today = 0
    last_exit_ts: Any = None
    peak_equity = float(state.cash)
    trade_day: str | None = None
    max_margin_ratio: float | None = None
    closest_liquidation_distance_pct: float | None = None
    last_margin_snapshot: dict | None = None

    state.margin_mode = str(margin_mode or getattr(state, "margin_mode", "isolated")).strip().lower()

    has_high = "high" in df.columns
    has_low = "low" in df.columns
    has_open = "open" in df.columns

    for i in range(len(df)):
        ts = _to_utc_timestamp(df["timestamp"].iloc[i])
        ts_iso = ts.isoformat()

        open_price = float(df["open"].iloc[i]) if has_open and pd.notna(df["open"].iloc[i]) else None
        high_price = float(df["high"].iloc[i]) if has_high and pd.notna(df["high"].iloc[i]) else None
        low_price = float(df["low"].iloc[i]) if has_low and pd.notna(df["low"].iloc[i]) else None
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

        mark_price = derive_mark_price(
            close=close,
            high=high_price,
            low=low_price,
            open_price=open_price,
            source=mark_price_source,
        )

        current_equity = float(engine.mark_equity(state, market_type, close))
        peak_equity = max(peak_equity, current_equity)

        current_margin_snapshot: MarginSnapshot | None = None
        if (
            str(market_type or "spot").lower() == "futures"
            and state.position_qty != 0.0
            and use_mark_price_for_liquidation
        ):
            current_margin_snapshot = evaluate_position_margin(
                state=state,
                market_type=market_type,
                mark_price=mark_price,
                leverage=leverage,
                margin_mode=state.margin_mode,
                resolver=resolver,
                maintenance_margin_override=maintenance_margin_override,
            )
            engine.apply_margin_snapshot(state, current_margin_snapshot)
            last_margin_snapshot = _snapshot_to_dict(current_margin_snapshot)

            if current_margin_snapshot is not None and current_margin_snapshot.margin_ratio is not None:
                ratio = current_margin_snapshot.margin_ratio
                if ratio is not None and math.isfinite(ratio):
                    max_margin_ratio = ratio if max_margin_ratio is None else max(max_margin_ratio, ratio)

            if (
                current_margin_snapshot is not None
                and current_margin_snapshot.liquidation_price is not None
                and current_margin_snapshot.mark_price > 0.0
            ):
                dist_pct = (
                    abs(current_margin_snapshot.mark_price - current_margin_snapshot.liquidation_price)
                    / current_margin_snapshot.mark_price
                    * 100.0
                )
                if math.isfinite(dist_pct):
                    closest_liquidation_distance_pct = (
                        dist_pct
                        if closest_liquidation_distance_pct is None
                        else min(closest_liquidation_distance_pct, dist_pct)
                    )

        # 1) Hard liquidation guard first
        if (
            enable_liquidation
            and str(market_type or "spot").lower() == "futures"
            and state.position_qty != 0.0
            and current_margin_snapshot is not None
            and current_margin_snapshot.liquidation_price is not None
        ):
            liq_px = float(current_margin_snapshot.liquidation_price)
            side = str(state.side or "long").lower()

            if side == "short":
                breached = high_price is not None and high_price >= liq_px
            else:
                breached = low_price is not None and low_price <= liq_px

            if current_margin_snapshot.should_liquidate or breached:
                state, fill = engine.liquidate(
                    ts_iso,
                    state,
                    close,
                    market_type,
                    trade_id,
                    exec_price=liq_px,
                    mark_price=mark_price,
                    margin_snapshot=current_margin_snapshot,
                )
                if fill:
                    trades.append(fill.__dict__)
                    liquidated = True
                    liquidation_count += 1
                    open_trade_equity_baseline = float(state.cash)
                    last_exit_ts = getattr(fill, "timestamp", None) or ts_iso
                    risk_events.append(
                        {
                            "timestamp": ts_iso,
                            "event": "liquidation",
                            "reason": "maintenance_margin",
                            "meta": {
                                "mark_price": mark_price,
                                "close": close,
                                "high": high_price,
                                "low": low_price,
                                "liquidation_price": liq_px,
                                "margin_snapshot": _snapshot_to_dict(current_margin_snapshot),
                            },
                        }
                    )
                    trade_id += 1
                    eq = engine.mark_equity(state, market_type, close)
                    peak_equity = max(peak_equity, float(eq))
                    if include_equity:
                        if equity_stride <= 1 or (i % equity_stride == 0):
                            equity_curve.append(
                                {
                                    "timestamp": ts_iso,
                                    "equity": float(eq),
                                    "mark_price": float(mark_price),
                                    "margin_ratio": None,
                                    "liquidation_price": None,
                                }
                            )
                    continue

        # 2) Risk exit (static or ATR)
        if state.position_qty > 0.0:
            side = str(state.side or "long").lower()

            if side == "short":
                short_entry_price = float(state.entry_price) if state.entry_price is not None else close
                synthetic_market_price = (2.0 * short_entry_price) - close

                exit_signal = risk_engine.evaluate_long_exit(
                    entry_price=short_entry_price,
                    market_price=synthetic_market_price,
                    stop_loss_pct=stop_loss_pct,
                    take_profit_pct=take_profit_pct,
                    exit_mode=normalized_exit_mode,
                    atr_value=current_atr,
                    atr_stop_mult=atr_stop_mult,
                    atr_take_mult=atr_take_mult,
                )

                if exit_signal.should_exit:
                    trade_id += 1
                    state, fill = engine.exit_short(
                        ts_iso,
                        state,
                        close,
                        market_type,
                        trade_id,
                        exit_reason=str(exit_signal.reason or "risk_exit"),
                        mark_price=mark_price if str(market_type or "spot").lower() == "futures" else None,
                        liquidation_price=(
                            current_margin_snapshot.liquidation_price
                            if current_margin_snapshot is not None
                            else None
                        ),
                        maintenance_margin=(
                            current_margin_snapshot.maintenance_margin
                            if current_margin_snapshot is not None
                            else None
                        ),
                        margin_balance=(
                            current_margin_snapshot.margin_balance
                            if current_margin_snapshot is not None
                            else None
                        ),
                        margin_ratio=(
                            current_margin_snapshot.margin_ratio
                            if current_margin_snapshot is not None
                            else None
                        ),
                    )
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

            else:
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
                    state, fill = engine.exit_long(
                        ts_iso,
                        state,
                        close,
                        market_type,
                        trade_id,
                        exit_reason=str(exit_signal.reason or "risk_exit"),
                        mark_price=mark_price if str(market_type or "spot").lower() == "futures" else None,
                        liquidation_price=(
                            current_margin_snapshot.liquidation_price
                            if current_margin_snapshot is not None
                            else None
                        ),
                        maintenance_margin=(
                            current_margin_snapshot.maintenance_margin
                            if current_margin_snapshot is not None
                            else None
                        ),
                        margin_balance=(
                            current_margin_snapshot.margin_balance
                            if current_margin_snapshot is not None
                            else None
                        ),
                        margin_ratio=(
                            current_margin_snapshot.margin_ratio
                            if current_margin_snapshot is not None
                            else None
                        ),
                    )
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

        elif (
            signal == -1
            and state.position_qty == 0.0
            and str(market_type or "spot").lower() == "futures"
            and bool(getattr(risk_engine, "_allow_short_override", True))
        ):
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
                state, fill = engine.enter_short(
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
        elif (
            exit_on_signal
            and signal == -1
            and state.position_qty > 0.0
            and str(state.side or "long").lower() == "long"
        ):            
            trade_id += 1
            state, fill = engine.exit_long(
                ts_iso,
                state,
                close,
                market_type,
                trade_id,
                exit_reason="signal_exit",
                mark_price=mark_price if str(market_type or "spot").lower() == "futures" else None,
                liquidation_price=(
                    current_margin_snapshot.liquidation_price
                    if current_margin_snapshot is not None
                    else None
                ),
                maintenance_margin=(
                    current_margin_snapshot.maintenance_margin
                    if current_margin_snapshot is not None
                    else None
                ),
                margin_balance=(
                    current_margin_snapshot.margin_balance
                    if current_margin_snapshot is not None
                    else None
                ),
                margin_ratio=(
                    current_margin_snapshot.margin_ratio
                    if current_margin_snapshot is not None
                    else None
                ),
            )
            if fill:
                realized_pnl = float(fill.equity_after) - float(open_trade_equity_baseline)
                fill.pnl = realized_pnl
                trades.append(fill.__dict__)
                open_trade_equity_baseline = float(fill.equity_after)
                last_exit_ts = getattr(fill, "timestamp", None) or ts_iso

        elif (
            exit_on_signal
            and signal == 1
            and state.position_qty > 0.0
            and str(state.side or "").lower() == "short"
        ):            
            trade_id += 1
            state, fill = engine.exit_short(
                ts_iso,
                state,
                close,
                market_type,
                trade_id,
                exit_reason="signal_exit",
                mark_price=mark_price if str(market_type or "spot").lower() == "futures" else None,
                liquidation_price=(
                    current_margin_snapshot.liquidation_price
                    if current_margin_snapshot is not None
                    else None
                ),
                maintenance_margin=(
                    current_margin_snapshot.maintenance_margin
                    if current_margin_snapshot is not None
                    else None
                ),
                margin_balance=(
                    current_margin_snapshot.margin_balance
                    if current_margin_snapshot is not None
                    else None
                ),
                margin_ratio=(
                    current_margin_snapshot.margin_ratio
                    if current_margin_snapshot is not None
                    else None
                ),
            )
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
                if str(market_type or "spot").lower() == "futures":
                    point["mark_price"] = float(mark_price)
                    point["margin_ratio"] = (
                        current_margin_snapshot.margin_ratio
                        if current_margin_snapshot is not None
                        else None
                    )
                    point["liquidation_price"] = (
                        current_margin_snapshot.liquidation_price
                        if current_margin_snapshot is not None
                        else None
                    )
                equity_curve.append(point)

    final_equity = float(engine.mark_equity(state, market_type, float(df["close"].iloc[-1])))

    return RunOutput(
        state=state,
        trades=trades,
        equity_curve=equity_curve,
        final_equity=final_equity,
        liquidated=liquidated,
        risk_events=risk_events,
        liquidation_count=liquidation_count,
        max_margin_ratio=max_margin_ratio,
        closest_liquidation_distance_pct=closest_liquidation_distance_pct,
        last_margin_snapshot=last_margin_snapshot,
    )