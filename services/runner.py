from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import pandas as pd

from core.execution_models import PortfolioState
from services.execution_engine import ExecutionEngine
from services.margin_engine import derive_mark_price, evaluate_position_margin, normalize_maintenance_margin_override
from services.risk_engine import RiskEngine


@dataclass
class BacktestStats:
    initial_balance: float
    final_equity: float
    total_return_percent: float
    max_drawdown_percent: float
    total_trades: int
    win_count: int
    loss_count: int
    breakeven_count: int
    win_rate_percent: float
    gross_profit: float
    gross_loss: float
    net_pnl: float
    profit_factor: float
    sharpe: float
    liquidation_count: int

    def to_dict(self) -> dict[str, float | int]:
        return {
            "initial_balance": self.initial_balance,
            "final_equity": self.final_equity,
            "total_return_percent": self.total_return_percent,
            "max_drawdown_percent": self.max_drawdown_percent,
            "total_trades": self.total_trades,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "breakeven_count": self.breakeven_count,
            "win_rate_percent": self.win_rate_percent,
            "gross_profit": self.gross_profit,
            "gross_loss": self.gross_loss,
            "net_pnl": self.net_pnl,
            "profit_factor": self.profit_factor,
            "sharpe": self.sharpe,
            "liquidation_count": self.liquidation_count,
        }


@dataclass
class RunOutput:
    state: PortfolioState
    trades: list[dict]
    equity_curve: list[dict]
    final_equity: float
    liquidated: bool
    stats: BacktestStats
    risk_events: list[dict]
    max_margin_ratio: float | None = None
    closest_liquidation_distance_pct: float | None = None
    last_margin_snapshot: dict[str, Any] | None = None


_NUMERIC_EPSILON = 1e-12
_MONEY_EPSILON = 1e-9


def _clean_float(value: Any, eps: float = _NUMERIC_EPSILON) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0

    if pd.isna(v):
        return 0.0

    if abs(v) <= eps:
        return 0.0

    return v


def _clean_money(value: Any) -> float:
    return round(_clean_float(value, eps=_MONEY_EPSILON), 12)


def _clean_fill_for_output(fill: Any) -> dict[str, Any]:
    raw = dict(fill.__dict__)

    for key in (
        "price",
        "qty",
        "fee",
        "pnl",
        "realized_pnl",
        "unrealized_pnl",
        "equity_after",
        "cash_after",
        "position_qty_after",
        "position_margin_after",
        "notional_after",
    ):
        if key in raw:
            raw[key] = _clean_money(raw[key])

    return raw


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


def _build_atr_series(df: Any, period: int | None) -> pd.Series | None:
    try:
        p = max(1, int(period or 14))
    except (TypeError, ValueError):
        p = 14

    if not isinstance(df, pd.DataFrame):
        return None

    needed = {"high", "low", "close"}
    if not needed.issubset(df.columns):
        return None

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

    return tr.rolling(window=p, min_periods=p).mean()


def _build_backtest_stats(
    *,
    initial_balance: float,
    final_equity: float,
    trades: list[dict],
    equity_curve: list[dict],
) -> BacktestStats:
    initial_balance = _clean_money(initial_balance)
    final_equity = _clean_money(final_equity)

    total_return_percent = (
        ((final_equity - initial_balance) / initial_balance) * 100.0
        if initial_balance > 0.0
        else 0.0
    )

    eq_values: list[float] = []
    if equity_curve:
        eq_values = [_clean_money(p.get("equity", initial_balance)) for p in equity_curve]
    else:
        eq_values = [initial_balance]
        for t in trades:
            fill_type = str(t.get("type", "")).upper()
            if fill_type in {"EXIT", "LIQUIDATION"}:
                eq_values.append(_clean_money(t.get("equity_after", eq_values[-1])))

    if not eq_values:
        eq_values = [initial_balance]

    peak = eq_values[0]
    max_drawdown_percent = 0.0
    for value in eq_values:
        if value > peak:
            peak = value
        if peak > 0.0:
            dd = ((peak - value) / peak) * 100.0
            if dd > max_drawdown_percent:
                max_drawdown_percent = dd

    close_fills = [
        t for t in trades
        if str(t.get("type", "")).upper() in {"EXIT", "LIQUIDATION"}
    ]

    pnls = [_clean_money(t.get("pnl", 0.0)) for t in close_fills]
    wins = [p for p in pnls if p > 0.0]
    losses = [p for p in pnls if p < 0.0]
    liquidation_count = sum(
        1 for t in close_fills if str(t.get("type", "")).upper() == "LIQUIDATION"
    )

    total_trades = len(close_fills)
    win_count = len(wins)
    loss_count = len(losses)
    breakeven_count = total_trades - win_count - loss_count
    win_rate_percent = (win_count / total_trades * 100.0) if total_trades else 0.0

    gross_profit = _clean_money(sum(wins))
    gross_loss = _clean_money(abs(sum(losses)))
    net_pnl = _clean_money(gross_profit - gross_loss)

    if gross_loss > 0.0:
        profit_factor = _clean_money(gross_profit / gross_loss)
    else:
        profit_factor = _clean_money(gross_profit if gross_profit > 0.0 else 0.0)

    eq_np = pd.Series(eq_values, dtype=float)
    if len(eq_np) >= 2:
        rets = eq_np.pct_change().replace([float("inf"), float("-inf")], pd.NA).dropna()
        if not rets.empty and float(rets.std()) > 0.0:
            sharpe = _clean_money(float(rets.mean() / rets.std()) * math.sqrt(365.0))
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    return BacktestStats(
        initial_balance=initial_balance,
        final_equity=final_equity,
        total_return_percent=_clean_money(total_return_percent),
        max_drawdown_percent=_clean_money(max_drawdown_percent),
        total_trades=total_trades,
        win_count=win_count,
        loss_count=loss_count,
        breakeven_count=breakeven_count,
        win_rate_percent=_clean_money(win_rate_percent),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_pnl=net_pnl,
        profit_factor=profit_factor,
        sharpe=sharpe,
        liquidation_count=liquidation_count,
    )


def run_signal_backed_loop(
    df: Any,
    *,
    engine: ExecutionEngine,
    state: PortfolioState,
    market_type: str,
    leverage: float,
    allow_short: bool = False,
    exit_on_signal: bool = True,
    exit_mode: str = "static",
    atr_period: int = 14,
    atr_stop_mult: float = 1.5,
    atr_take_mult: float = 2.5,
    atr_reference_mode: str = "entry",
    margin_mode: str = "cross",
    enable_liquidation: bool = True,
    use_mark_price_for_liquidation: bool = True,
    mark_price_source: str = "close",
    maintenance_margin_override: float | None = None,
    include_equity: bool = False,
    equity_stride: int = 1,
    risk_engine: RiskEngine | None = None,
    
    position_sizing_mode: str = "all-in",
    position_size_value: float | None = None,
    enable_volatility_scaling: bool = False,
    volatility_target_pct: float | None = None,
    min_volatility_scale: float | None = 0.50,
    max_volatility_scale: float | None = 1.50,
    debug_risk_telemetry: bool = False,
    max_drawdown_pct: float | None = None,    

    max_trades_per_day: int | None = None,
    cooldown_seconds: int = 0,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
) -> RunOutput:
    _ = allow_short
    _ = margin_mode
    _ = use_mark_price_for_liquidation
    _ = mark_price_source

    initial_balance = _clean_money(state.cash)

    trades: list[dict] = []
    equity_curve: list[dict] = []
    risk_events: list[dict] = []
    liquidated = False
    trade_id = 0
    max_margin_ratio: float | None = None
    closest_liquidation_distance_pct: float | None = None
    last_margin_snapshot: dict[str, Any] | None = None

    effective_mm_override = normalize_maintenance_margin_override(maintenance_margin_override)

    open_trade_equity_baseline = float(state.cash)

    risk_engine = risk_engine or RiskEngine()
    trades_today = 0
    last_exit_ts: Any = None
    peak_equity = float(state.cash)
    trade_day: str | None = None
    position_peak_price: float | None = None

    needs_atr = (
        str(exit_mode or "static").strip().lower() == "atr"
        or bool(enable_volatility_scaling)
    )

    atr_series = _build_atr_series(df, atr_period) if needs_atr else None    

    for i in range(len(df)):
        bar_was_liquidated = False
        ts = _to_utc_timestamp(df["timestamp"].iloc[i])
        ts_iso = ts.isoformat()
        close = float(df["close"].iloc[i])
        signal = int(df["signal"].iloc[i])

        if state.position_qty > 0.0:
            position_peak_price = (
                close if position_peak_price is None else max(float(position_peak_price), close)
            )
        else:
            position_peak_price = None

        day_key = _day_key_from_ts(ts)
        if trade_day != day_key:
            trade_day = day_key
            trades_today = 0

        current_equity = _clean_money(engine.mark_equity(state, market_type, close))
        peak_equity = max(peak_equity, current_equity)

        effective_mm = float(engine.maintenance_margin)
        if effective_mm_override is not None:
            effective_mm = float(effective_mm_override)

        mark_price = float(close)
        margin_snapshot = None
        should_liquidate = False
        if (
            str(market_type or "spot").strip().lower() == "futures"
            and float(leverage) > 1.0
            and float(state.position_qty) != 0.0
        ):
            high = float(df["high"].iloc[i]) if "high" in df.columns else float(close)
            low = float(df["low"].iloc[i]) if "low" in df.columns else float(close)
            open_price = float(df["open"].iloc[i]) if "open" in df.columns else float(close)
            mark_price = derive_mark_price(
                close=float(close),
                high=high,
                low=low,
                open_price=open_price,
                source=mark_price_source if use_mark_price_for_liquidation else "close",
            )
            margin_snapshot = evaluate_position_margin(
                state=state,
                market_type=market_type,
                mark_price=mark_price,
                leverage=leverage,
                margin_mode=margin_mode,
                maintenance_margin_override=effective_mm_override,
            )
            if margin_snapshot is not None:
                engine.apply_margin_snapshot(state, margin_snapshot)
                snapshot_ratio = getattr(margin_snapshot, "margin_ratio", None)
                if snapshot_ratio is not None:
                    try:
                        ratio_value = float(snapshot_ratio)
                        if math.isfinite(ratio_value):
                            max_margin_ratio = ratio_value if max_margin_ratio is None else max(max_margin_ratio, ratio_value)
                    except (TypeError, ValueError):
                        pass

                liq_px = getattr(margin_snapshot, "liquidation_price", None)
                if liq_px is not None and float(mark_price) > 0.0:
                    try:
                        distance_pct = abs(float(mark_price) - float(liq_px)) / float(mark_price) * 100.0
                        if math.isfinite(distance_pct):
                            closest_liquidation_distance_pct = (
                                distance_pct
                                if closest_liquidation_distance_pct is None
                                else min(closest_liquidation_distance_pct, distance_pct)
                            )
                    except (TypeError, ValueError, ZeroDivisionError):
                        pass

                last_margin_snapshot = {
                    "mark_price": _clean_money(getattr(margin_snapshot, "mark_price", mark_price)),
                    "notional": _clean_money(getattr(margin_snapshot, "notional", 0.0)),
                    "unrealized_pnl": _clean_money(getattr(margin_snapshot, "unrealized_pnl", 0.0)),
                    "collateral": _clean_money(getattr(margin_snapshot, "collateral", 0.0)),
                    "maintenance_margin": _clean_money(getattr(margin_snapshot, "maintenance_margin", 0.0)),
                    "maintenance_margin_rate": _clean_money(getattr(margin_snapshot, "maintenance_margin_rate", effective_mm)),
                    "maintenance_amount": _clean_money(getattr(margin_snapshot, "maintenance_amount", 0.0)),
                    "margin_balance": _clean_money(getattr(margin_snapshot, "margin_balance", 0.0)),
                    "margin_ratio": (
                        _clean_money(getattr(margin_snapshot, "margin_ratio", 0.0))
                        if getattr(margin_snapshot, "margin_ratio", None) is not None
                        and math.isfinite(float(getattr(margin_snapshot, "margin_ratio", 0.0)))
                        else None
                    ),
                    "liquidation_price": (
                        _clean_money(getattr(margin_snapshot, "liquidation_price", 0.0))
                        if getattr(margin_snapshot, "liquidation_price", None) is not None
                        and math.isfinite(float(getattr(margin_snapshot, "liquidation_price", 0.0)))
                        else None
                    ),
                    "bankruptcy_price": (
                        _clean_money(getattr(margin_snapshot, "bankruptcy_price", 0.0))
                        if getattr(margin_snapshot, "bankruptcy_price", None) is not None
                        and math.isfinite(float(getattr(margin_snapshot, "bankruptcy_price", 0.0)))
                        else None
                    ),
                    "should_liquidate": bool(getattr(margin_snapshot, "should_liquidate", False)),
                    "margin_mode": str(getattr(margin_snapshot, "margin_mode", margin_mode or "cross")),
                }
                should_liquidate = bool(enable_liquidation and getattr(margin_snapshot, "should_liquidate", False))

        if should_liquidate:
            state, fill = engine.liquidate(
                ts_iso,
                state,
                close,
                market_type,
                trade_id,
                exec_price=close,
                mark_price=mark_price,
                margin_snapshot=margin_snapshot,
            )
            if fill:
                realized_pnl = _clean_money(
                    float(fill.equity_after) - float(open_trade_equity_baseline)
                )
                fill.pnl = realized_pnl
                fill.equity_after = _clean_money(fill.equity_after)
                trades.append(_clean_fill_for_output(fill))
                liquidated = True
                open_trade_equity_baseline = _clean_money(state.cash)
                last_exit_ts = getattr(fill, "timestamp", None) or ts_iso
                position_peak_price = None
                bar_was_liquidated = True
                risk_events.append(
                    {
                        "timestamp": ts_iso,
                        "event": "liquidation",
                        "reason": "maintenance_margin",
                        "meta": {
                            "maintenance_margin": effective_mm,
                            "mark_price_source": mark_price_source if use_mark_price_for_liquidation else "close",
                            "market_price": _clean_money(mark_price),
                            "liquidation_price": None if last_margin_snapshot is None else last_margin_snapshot.get("liquidation_price"),
                            "margin_ratio": None if last_margin_snapshot is None else last_margin_snapshot.get("margin_ratio"),
                        },
                    }
                )
                trade_id += 1

        atr_value: float | None = None
        if atr_series is not None:
            raw_atr = atr_series.iloc[i]
            if pd.notna(raw_atr):
                try:
                    atr_value = float(raw_atr)
                except (TypeError, ValueError):
                    atr_value = None

        if (not bar_was_liquidated) and state.position_qty > 0.0:
            exit_signal = risk_engine.evaluate_long_exit(
                entry_price=state.entry_price,
                market_price=close,
                stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct,
                exit_mode=exit_mode,
                atr_value=atr_value,
                atr_stop_mult=atr_stop_mult,
                atr_take_mult=atr_take_mult,
                atr_reference_mode=atr_reference_mode,
                peak_price_since_entry=position_peak_price,
            )
            if exit_signal.should_exit:
                trade_id += 1
                state, fill = engine.exit_long(ts_iso, state, close, market_type, trade_id)
                if fill:
                    realized_pnl = _clean_money(
                        float(fill.equity_after) - float(open_trade_equity_baseline)
                    )
                    fill.pnl = realized_pnl
                    fill.equity_after = _clean_money(fill.equity_after)
                    trades.append(_clean_fill_for_output(fill))
                    open_trade_equity_baseline = _clean_money(fill.equity_after)
                    last_exit_ts = getattr(fill, "timestamp", None) or ts_iso
                    position_peak_price = None
                    risk_events.append(
                        {
                            "timestamp": ts_iso,
                            "event": "risk_event",
                            "reason": exit_signal.reason,
                            "meta": exit_signal.meta or {},
                        }
                    )

        if (not bar_was_liquidated) and signal == 1 and state.position_qty == 0.0:
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
                qty_override, sizing_meta = risk_engine.compute_entry_qty_with_meta(
                    state=state,
                    market_type=market_type,
                    entry_price=close,
                    leverage=leverage,
                    fee_rate=engine.fee_rate,
                    max_leverage=engine.max_leverage,
                    max_qty=engine.max_qty,
                    sizing_mode=position_sizing_mode,
                    sizing_value=position_size_value,
                    current_equity=current_equity,
                    stop_loss_pct=stop_loss_pct,
                    exit_mode=exit_mode,
                    atr_value=atr_value,
                    atr_stop_mult=atr_stop_mult,
                    enable_volatility_scaling=enable_volatility_scaling,
                    volatility_target_pct=volatility_target_pct,
                    min_volatility_scale=min_volatility_scale,
                    max_volatility_scale=max_volatility_scale,
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
                    fill.equity_after = _clean_money(getattr(fill, "equity_after", 0.0))
                    trades.append(_clean_fill_for_output(fill))
                    open_trade_equity_baseline = _clean_money(
                        engine.mark_equity(state, market_type, close)
                    )
                    position_peak_price = close
                    trades_today += 1

                    if debug_risk_telemetry:
                        sizing_meta_out = dict(sizing_meta or {})
                        sizing_meta_out["filled_qty"] = _clean_money(getattr(fill, "qty", 0.0))
                        sizing_meta_out["filled_price"] = _clean_money(getattr(fill, "price", close))
                        sizing_meta_out["trade_id"] = getattr(fill, "trade_id", trade_id)
                        risk_events.append(
                            {
                                "timestamp": ts_iso,
                                "event": "entry_sizing",
                                "reason": "entry_qty_computed",
                                "meta": sizing_meta_out,
                            }
                        )

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
            (not bar_was_liquidated)
            and exit_on_signal
            and signal == -1
            and state.position_qty > 0.0
        ):
            trade_id += 1
            state, fill = engine.exit_long(ts_iso, state, close, market_type, trade_id)
            if fill:
                realized_pnl = _clean_money(
                    float(fill.equity_after) - float(open_trade_equity_baseline)
                )
                fill.pnl = realized_pnl
                fill.equity_after = _clean_money(fill.equity_after)
                trades.append(_clean_fill_for_output(fill))
                open_trade_equity_baseline = _clean_money(fill.equity_after)
                last_exit_ts = getattr(fill, "timestamp", None) or ts_iso
                position_peak_price = None

        eq = _clean_money(engine.mark_equity(state, market_type, close))
        peak_equity = max(peak_equity, float(eq))

        if include_equity:
            if equity_stride <= 1 or (i % equity_stride == 0):
                equity_curve.append(
                    {
                        "timestamp": ts_iso,
                        "equity": _clean_money(eq),
                    }
                )

    final_equity = _clean_money(
        engine.mark_equity(state, market_type, float(df["close"].iloc[-1]))
    )

    stats = _build_backtest_stats(
        initial_balance=initial_balance,
        final_equity=final_equity,
        trades=trades,
        equity_curve=equity_curve,
    )

    return RunOutput(
        state=state,
        trades=trades,
        equity_curve=equity_curve,
        final_equity=final_equity,
        liquidated=liquidated,
        stats=stats,
        risk_events=risk_events,
        max_margin_ratio=_clean_money(max_margin_ratio) if max_margin_ratio is not None else None,
        closest_liquidation_distance_pct=(
            _clean_money(closest_liquidation_distance_pct)
            if closest_liquidation_distance_pct is not None
            else None
        ),
        last_margin_snapshot=last_margin_snapshot,
    )
