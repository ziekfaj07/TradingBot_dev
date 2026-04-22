from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
import copy

from core.execution_models import PortfolioState
from core.math_utils import safe_float, safe_float_or_none, safe_int


@dataclass
class RiskGateResult:
    allowed: bool
    reason: Optional[str] = None
    meta: dict | None = None


@dataclass
class ExitLevels:
    stop_price: float | None = None
    take_price: float | None = None
    exit_family: str = "static"


@dataclass
class ExitSignal:
    should_exit: bool
    reason: str | None = None
    meta: dict | None = None


class RiskEngine:
    """
    v0.6 risk engine:
    - position sizing
    - max drawdown stop
    - max trades per day
    - cooldown logic
    - static stop loss / take profit
    - ATR-based stop loss / take profit
    """

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        return safe_float(value, default)

    def _safe_float_or_none(self, value: Any) -> float | None:
        return safe_float_or_none(value)

    def _safe_int(self, value: Any, default: int = 0) -> int:
        return safe_int(value, default)

    def _normalize_mode(self, value: Any) -> str:
        mode = str(value or "all_in").strip().lower()
        aliases = {
            "allin": "all_in",
            "all-in": "all_in",
            "full_balance": "all_in",
            "full-balance": "all_in",
            "fixedusdt": "fixed_usdt",
            "fixed-usdt": "fixed_usdt",
            "fixedpct": "fixed_pct",
            "fixed-percent": "fixed_pct",
            "fixed_percentage": "fixed_pct",
            "equitypct": "equity_pct",
            "equity-percent": "equity_pct",
            "equity_percentage": "equity_pct",
            "riskpct": "risk_pct",
            "risk-percent": "risk_pct",
            "risk_percentage": "risk_pct",
        }
        return aliases.get(mode, mode)

    def _normalize_exit_mode(self, value: Any) -> str:
        mode = str(value or "static").strip().lower()
        aliases = {
            "fixed": "static",
            "pct": "static",
            "percentage": "static",
            "atr_based": "atr",
            "atr-based": "atr",
        }
        return aliases.get(mode, mode)

    def _normalize_atr_reference_mode(self, value: Any) -> str:
        mode = str(value or "entry").strip().lower()

        aliases = {
            "entry_locked": "entry",
            "entry-lock": "entry",
            "entrylock": "entry",
            "entry_frozen": "entry",
            "entry-frozen": "entry",
            "frozen": "entry",
            "snapshot": "entry",
            "locked": "entry",
            "dynamic": "floating",
            "trail": "floating",
            "trailing": "floating",
        }

        normalized = aliases.get(mode, mode)
        return "floating" if normalized == "floating" else "entry"

    def _coerce_epoch_seconds(self, value: Any) -> float | None:
        if value is None:
            return None

        if isinstance(value, bool):
            return float(int(value))

        if isinstance(value, (int, float)):
            v = float(value)
            if not math.isfinite(v):
                return None
            av = abs(v)
            if av >= 1e17:
                return v / 1_000_000_000.0
            if av >= 1e14:
                return v / 1_000_000.0
            if av >= 1e11:
                return v / 1_000.0
            return v

        if isinstance(value, datetime):
            dt = value
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return float(dt.timestamp())

        timestamp_method = getattr(value, "timestamp", None)
        if callable(timestamp_method):
            try:
                ts = timestamp_method()
            except Exception:
                ts = None
            if ts is not None:
                return self._safe_float_or_none(ts)

        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None

            numeric = self._safe_float_or_none(text)
            if numeric is not None:
                return self._coerce_epoch_seconds(numeric)

            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                return float(dt.timestamp())
            except ValueError:
                return None

        return None

    def _safe_scale(
        self,
        value: float,
        *,
        floor: float | None = None,
        ceiling: float | None = None,
    ) -> float:
        out = self._safe_float(value, 1.0)
        if floor is not None:
            out = max(self._safe_float(floor, 0.0), out)
        if ceiling is not None and self._safe_float(ceiling, 0.0) > 0.0:
            out = min(self._safe_float(ceiling, out), out)
        return out

    def _resolve_stop_distance(
        self,
        *,
        entry_price: float,
        stop_loss_pct: float | None,
        exit_mode: str,
        atr_value: float | None,
        atr_stop_mult: float | None,
    ) -> float | None:
        px = self._safe_float(entry_price, 0.0)
        if px <= 0.0 or not math.isfinite(px):
            return None

        normalized_exit_mode = self._normalize_exit_mode(exit_mode)

        if normalized_exit_mode == "atr":
            atr = self._safe_float(atr_value, 0.0)
            stop_mult = self._safe_float(atr_stop_mult, 0.0)
            dist = atr * stop_mult
            if dist > 0.0 and math.isfinite(dist):
                return dist
            return None

        sl_pct = self._safe_float(stop_loss_pct, 0.0)
        if sl_pct <= 0.0:
            return None

        dist = px * (sl_pct / 100.0)
        if dist > 0.0 and math.isfinite(dist):
            return dist
        return None

    def _resolve_volatility_scale(
        self,
        *,
        entry_price: float,
        atr_value: float | None,
        enable_volatility_scaling: bool,
        volatility_target_pct: float | None,
        min_volatility_scale: float | None,
        max_volatility_scale: float | None,
    ) -> float:
        if not enable_volatility_scaling:
            return 1.0

        px = self._safe_float(entry_price, 0.0)
        atr = self._safe_float(atr_value, 0.0)
        target_pct = self._safe_float(volatility_target_pct, 0.0)

        if px <= 0.0 or atr <= 0.0 or target_pct <= 0.0:
            return 1.0

        realized_atr_pct = (atr / px) * 100.0
        if realized_atr_pct <= 0.0 or not math.isfinite(realized_atr_pct):
            return 1.0

        raw_scale = target_pct / realized_atr_pct
        return self._safe_scale(
            raw_scale,
            floor=min_volatility_scale,
            ceiling=max_volatility_scale,
        )    

    def _clean_telemetry_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            v = float(value)
            if not math.isfinite(v):
                return None
            return round(v, 12)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return [self._clean_telemetry_value(v) for v in value]
        if isinstance(value, dict):
            return {str(k): self._clean_telemetry_value(v) for k, v in value.items()}
        return value

    def _finalize_entry_sizing_telemetry(self, telemetry: dict[str, Any]) -> dict[str, Any]:
        return {str(k): self._clean_telemetry_value(v) for k, v in telemetry.items()}

    def compute_entry_qty_with_meta(
        self,
        *,
        state: PortfolioState,
        market_type: str,
        entry_price: float,
        leverage: float,
        fee_rate: float,
        max_leverage: float,
        max_qty: float,
        sizing_mode: str,
        sizing_value: float | None,
        current_equity: float | None = None,
        stop_loss_pct: float | None = None,
        exit_mode: str = "static",
        atr_value: float | None = None,
        atr_stop_mult: float | None = None,
        enable_volatility_scaling: bool = False,
        volatility_target_pct: float | None = None,
        min_volatility_scale: float | None = None,
        max_volatility_scale: float | None = None,
    ) -> tuple[float | None, dict[str, Any]]:
        px = self._safe_float(entry_price)
        mode = self._normalize_mode(sizing_mode)
        mt = str(market_type or "spot").strip().lower()
        cash = max(0.0, self._safe_float(state.cash))
        equity = max(cash, self._safe_float(current_equity, cash))
        fee_rate_safe = max(0.0, self._safe_float(fee_rate))
        max_qty_safe = max(0.0, self._safe_float(max_qty))
        lev = min(
            max(self._safe_float(leverage, 1.0), 1.0),
            max(self._safe_float(max_leverage, 1.0), 1.0),
        )
        value = self._safe_float(sizing_value, 0.0)
        realized_atr_pct = None
        if px > 0.0:
            atr_safe = self._safe_float(atr_value, 0.0)
            if atr_safe > 0.0:
                realized_atr_pct = (atr_safe / px) * 100.0

        telemetry: dict[str, Any] = {
            "entry_price": px,
            "market_type": mt,
            "cash": cash,
            "current_equity": equity,
            "sizing_mode": mode,
            "sizing_value": value if value > 0.0 else None,
            "fee_rate": fee_rate_safe,
            "max_qty": max_qty_safe,
            "leverage": lev,
            "max_leverage": self._safe_float(max_leverage, 1.0),
            "stop_loss_pct": self._safe_float_or_none(stop_loss_pct),
            "exit_mode": self._normalize_exit_mode(exit_mode),
            "atr_value": self._safe_float_or_none(atr_value),
            "atr_stop_mult": self._safe_float_or_none(atr_stop_mult),
            "enable_volatility_scaling": bool(enable_volatility_scaling),
            "volatility_target_pct": self._safe_float_or_none(volatility_target_pct),
            "min_volatility_scale": self._safe_float_or_none(min_volatility_scale),
            "max_volatility_scale": self._safe_float_or_none(max_volatility_scale),
            "realized_atr_pct": realized_atr_pct,
            "volatility_scale": 1.0,
            "stop_distance": None,
            "risk_budget": None,
            "budget_before_vol_scale": None,
            "budget_after_vol_scale": None,
            "computed_qty_before_clamps": None,
            "computed_qty_after_clamps": None,
            "clamps": [],
            "rejected": False,
            "rejection_reason": None,
        }

        if px <= 0.0 or not math.isfinite(px):
            telemetry["rejected"] = True
            telemetry["rejection_reason"] = "invalid_entry_price"
            return None, self._finalize_entry_sizing_telemetry(telemetry)

        if mode == "all_in":
            telemetry["rejected"] = True
            telemetry["rejection_reason"] = "all_in_uses_engine_default"
            return None, self._finalize_entry_sizing_telemetry(telemetry)

        if value <= 0.0:
            telemetry["rejected"] = True
            telemetry["rejection_reason"] = "invalid_sizing_value"
            return None, self._finalize_entry_sizing_telemetry(telemetry)

        budget = 0.0

        if mode == "fixed_usdt":
            budget = value
            telemetry["budget_before_vol_scale"] = budget

        elif mode == "fixed_pct":
            budget = cash * (value / 100.0)
            telemetry["budget_before_vol_scale"] = budget

        elif mode == "equity_pct":
            budget = equity * (value / 100.0)
            telemetry["budget_before_vol_scale"] = budget

        elif mode == "risk_pct":
            stop_distance = self._resolve_stop_distance(
                entry_price=px,
                stop_loss_pct=stop_loss_pct,
                exit_mode=exit_mode,
                atr_value=atr_value,
                atr_stop_mult=atr_stop_mult,
            )
            telemetry["stop_distance"] = stop_distance

            if stop_distance is None or stop_distance <= 0.0:
                telemetry["rejected"] = True
                telemetry["rejection_reason"] = "invalid_stop_distance"
                return None, self._finalize_entry_sizing_telemetry(telemetry)

            risk_budget = equity * (value / 100.0)
            telemetry["risk_budget"] = risk_budget

            if risk_budget <= 0.0:
                telemetry["computed_qty_before_clamps"] = 0.0
                telemetry["computed_qty_after_clamps"] = 0.0
                return 0.0, self._finalize_entry_sizing_telemetry(telemetry)

            qty = risk_budget / stop_distance
            telemetry["computed_qty_before_clamps"] = qty

            if not math.isfinite(qty) or qty <= 0.0:
                telemetry["computed_qty_after_clamps"] = 0.0
                return 0.0, self._finalize_entry_sizing_telemetry(telemetry)

            vol_scale = self._resolve_volatility_scale(
                entry_price=px,
                atr_value=atr_value,
                enable_volatility_scaling=enable_volatility_scaling,
                volatility_target_pct=volatility_target_pct,
                min_volatility_scale=min_volatility_scale,
                max_volatility_scale=max_volatility_scale,
            )
            telemetry["volatility_scale"] = vol_scale
            qty *= vol_scale

            if mt == "spot":
                max_affordable_budget = cash
                desired_notional = qty * px
                if desired_notional > max_affordable_budget:
                    qty = max_affordable_budget / px if px > 0.0 else 0.0
                    telemetry["clamps"].append("cash_cap")
            else:
                max_notional = cash * lev
                desired_notional = qty * px
                if desired_notional > max_notional and px > 0.0:
                    qty = max_notional / px
                    telemetry["clamps"].append("max_notional_cap")

            if max_qty_safe > 0.0 and qty > max_qty_safe:
                qty = max_qty_safe
                telemetry["clamps"].append("max_qty_cap")

            if not math.isfinite(qty) or qty <= 0.0:
                telemetry["computed_qty_after_clamps"] = 0.0
                return 0.0, self._finalize_entry_sizing_telemetry(telemetry)

            telemetry["computed_qty_after_clamps"] = qty
            return qty, self._finalize_entry_sizing_telemetry(telemetry)

        else:
            telemetry["rejected"] = True
            telemetry["rejection_reason"] = "unsupported_sizing_mode"
            return None, self._finalize_entry_sizing_telemetry(telemetry)

        vol_scale = self._resolve_volatility_scale(
            entry_price=px,
            atr_value=atr_value,
            enable_volatility_scaling=enable_volatility_scaling,
            volatility_target_pct=volatility_target_pct,
            min_volatility_scale=min_volatility_scale,
            max_volatility_scale=max_volatility_scale,
        )
        telemetry["volatility_scale"] = vol_scale
        budget *= vol_scale
        telemetry["budget_after_vol_scale"] = budget

        budget_cap = cash if mt == "spot" else equity
        if budget > budget_cap:
            telemetry["clamps"].append("budget_cap")
        budget = max(0.0, min(budget, budget_cap))

        if budget <= 0.0:
            telemetry["computed_qty_before_clamps"] = 0.0
            telemetry["computed_qty_after_clamps"] = 0.0
            return 0.0, self._finalize_entry_sizing_telemetry(telemetry)

        if mt == "spot":
            notional_after_fee = max(0.0, budget * (1.0 - fee_rate_safe))
            qty = notional_after_fee / px
        else:
            notional = budget * lev
            fee = notional * fee_rate_safe
            if fee >= cash:
                telemetry["computed_qty_before_clamps"] = 0.0
                telemetry["computed_qty_after_clamps"] = 0.0
                telemetry["rejected"] = True
                telemetry["rejection_reason"] = "futures_fee_exceeds_cash"
                return 0.0, self._finalize_entry_sizing_telemetry(telemetry)
            qty = notional / px

        telemetry["computed_qty_before_clamps"] = qty

        if max_qty_safe > 0.0 and qty > max_qty_safe:
            qty = max_qty_safe
            telemetry["clamps"].append("max_qty_cap")

        if not math.isfinite(qty) or qty <= 0.0:
            telemetry["computed_qty_after_clamps"] = 0.0
            return 0.0, self._finalize_entry_sizing_telemetry(telemetry)

        telemetry["computed_qty_after_clamps"] = qty
        return qty, self._finalize_entry_sizing_telemetry(telemetry)

    def compute_entry_qty(
        self,
        *,
        state: PortfolioState,
        market_type: str,
        entry_price: float,
        leverage: float,
        fee_rate: float,
        max_leverage: float,
        max_qty: float,
        sizing_mode: str,
        sizing_value: float | None,
        current_equity: float | None = None,
        stop_loss_pct: float | None = None,
        exit_mode: str = "static",
        atr_value: float | None = None,
        atr_stop_mult: float | None = None,
        enable_volatility_scaling: bool = False,
        volatility_target_pct: float | None = None,
        min_volatility_scale: float | None = None,
        max_volatility_scale: float | None = None,
    ) -> float | None:
        qty, _ = self.compute_entry_qty_with_meta(
            state=state,
            market_type=market_type,
            entry_price=entry_price,
            leverage=leverage,
            fee_rate=fee_rate,
            max_leverage=max_leverage,
            max_qty=max_qty,
            sizing_mode=sizing_mode,
            sizing_value=sizing_value,
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
        return qty

    def evaluate_entry_gate(
        self,
        *,
        now_ts: Any,
        current_equity: float,
        peak_equity: float,
        trades_today: int,
        last_exit_ts: Any,
        max_drawdown_pct: float | None,
        max_trades_per_day: int | None,
        cooldown_seconds: int | None,
    ) -> RiskGateResult:
        now_sec = self._coerce_epoch_seconds(now_ts)
        if now_sec is None:
            now_sec = time.time()

        current_equity_safe = self._safe_float(current_equity, 0.0)
        peak_equity_safe = max(
            self._safe_float(peak_equity, current_equity_safe),
            current_equity_safe,
        )

        max_dd = self._safe_float(max_drawdown_pct, 0.0)
        if max_dd > 0 and peak_equity_safe > 0:
            dd_pct = max(0.0, (peak_equity_safe - current_equity_safe) / peak_equity_safe * 100.0)
            if dd_pct >= max_dd:
                return RiskGateResult(
                    allowed=False,
                    reason="max_drawdown_stop",
                    meta={
                        "drawdown_pct": dd_pct,
                        "max_drawdown_pct": max_dd,
                        "current_equity": current_equity_safe,
                        "peak_equity": peak_equity_safe,
                    },
                )

        max_trades = self._safe_int(max_trades_per_day, 0)
        if max_trades > 0 and trades_today >= max_trades:
            return RiskGateResult(
                allowed=False,
                reason="max_trades_per_day_reached",
                meta={
                    "trades_today": trades_today,
                    "max_trades_per_day": max_trades,
                },
            )

        cooldown = self._safe_int(cooldown_seconds, 0)
        last_exit_sec = self._coerce_epoch_seconds(last_exit_ts)
        if cooldown > 0 and last_exit_sec is not None:
            elapsed = max(0.0, now_sec - last_exit_sec)
            if elapsed < cooldown:
                return RiskGateResult(
                    allowed=False,
                    reason="cooldown_active",
                    meta={
                        "elapsed_seconds": elapsed,
                        "cooldown_seconds": cooldown,
                        "remaining_seconds": max(0.0, cooldown - elapsed),
                    },
                )

        return RiskGateResult(allowed=True)

    def _resolve_static_long_exit_levels(
        self,
        *,
        entry_price: float,
        stop_loss_pct: float | None,
        take_profit_pct: float | None,
        fee_rate: float = 0.0,
    ) -> ExitLevels:
        sl = self._safe_float(stop_loss_pct, 0.0)
        tp = self._safe_float(take_profit_pct, 0.0)
        round_trip_fee = max(0.0, self._safe_float(fee_rate, 0.0) * 2.0)

        stop_price: float | None = None
        take_price: float | None = None

        # IMPORTANT:
        # Stop loss is pure price distance. Do not subtract fees here.
        if sl > 0.0:
            stop_price = entry_price * (1.0 - sl / 100.0)

        # Take profit may widen by round-trip fee so the configured TP can net correctly.
        if tp > 0.0:
            gross_take_pct = (tp / 100.0) + round_trip_fee
            take_price = entry_price * (1.0 + gross_take_pct)

        return ExitLevels(
            stop_price=stop_price,
            take_price=take_price,
            exit_family="static",
        )

    def _resolve_atr_long_exit_levels(
        self,
        *,
        entry_price: float,
        atr_value: float | None,
        atr_stop_mult: float | None,
        atr_take_mult: float | None,
    ) -> ExitLevels:
        atr = self._safe_float(atr_value, 0.0)
        stop_mult = self._safe_float(atr_stop_mult, 0.0)
        take_mult = self._safe_float(atr_take_mult, 0.0)

        if atr <= 0.0:
            return ExitLevels(exit_family="atr")

        stop_price: float | None = None
        take_price: float | None = None

        if stop_mult > 0.0:
            stop_price = entry_price - (atr * stop_mult)
        if take_mult > 0.0:
            take_price = entry_price + (atr * take_mult)

        return ExitLevels(stop_price=stop_price, take_price=take_price, exit_family="atr")

    def resolve_long_exit_levels(
        self,
        *,
        entry_price: float | None,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        exit_mode: str = "static",
        atr_value: float | None = None,
        atr_stop_mult: float | None = None,
        atr_take_mult: float | None = None,
        fee_rate: float = 0.0,
    ) -> ExitLevels:
        ep = self._safe_float(entry_price, 0.0)
        if ep <= 0.0:
            return ExitLevels()

        mode = self._normalize_exit_mode(exit_mode)
        if mode == "atr":
            return self._resolve_atr_long_exit_levels(
                entry_price=ep,
                atr_value=atr_value,
                atr_stop_mult=atr_stop_mult,
                atr_take_mult=atr_take_mult,
            )

        return self._resolve_static_long_exit_levels(
            entry_price=ep,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            fee_rate=fee_rate,
        )

    def evaluate_long_exit(
        self,
        *,
        entry_price: float | None,
        market_price: float,
        stop_loss_pct: float | None,
        take_profit_pct: float | None,
        exit_mode: str = "static",
        atr_value: float | None = None,
        atr_stop_mult: float | None = None,
        atr_take_mult: float | None = None,
        atr_reference_mode: str = "entry",
        peak_price_since_entry: float | None = None,
        fee_rate: float = 0.0,
    ) -> ExitSignal:
        ep = self._safe_float(entry_price, 0.0)
        mp = self._safe_float(market_price, 0.0)
        if ep <= 0.0 or mp <= 0.0:
            return ExitSignal(should_exit=False)

        mode = str(exit_mode or "static").strip().lower()

        if mode == "atr":
            atr = self._safe_float(atr_value, 0.0)
            stop_mult = self._safe_float(atr_stop_mult, 0.0)
            take_mult = self._safe_float(atr_take_mult, 0.0)
            ref_mode = str(atr_reference_mode or "entry").strip().lower()

            if atr <= 0.0:
                return ExitSignal(should_exit=False)

            peak = self._safe_float(peak_price_since_entry, ep)
            if ref_mode == "floating":
                stop_anchor = max(ep, peak)
            else:
                stop_anchor = ep

            if stop_mult > 0.0:
                stop_price = stop_anchor - (atr * stop_mult)
                if mp <= stop_price:
                    return ExitSignal(
                        should_exit=True,
                        reason="atr_stop_loss",
                        meta={
                            "entry_price": ep,
                            "market_price": mp,
                            "atr_value": atr,
                            "atr_stop_mult": stop_mult,
                            "atr_reference_mode": ref_mode,
                            "peak_price_since_entry": peak,
                            "stop_anchor": stop_anchor,
                            "stop_price": stop_price,
                        },
                    )

            if take_mult > 0.0:
                take_price = ep + (atr * take_mult)
                if mp >= take_price:
                    return ExitSignal(
                        should_exit=True,
                        reason="atr_take_profit",
                        meta={
                            "entry_price": ep,
                            "market_price": mp,
                            "atr_value": atr,
                            "atr_take_mult": take_mult,
                            "take_price": take_price,
                        },
                    )

            return ExitSignal(should_exit=False)

        sl = self._safe_float(stop_loss_pct, 0.0)
        tp = self._safe_float(take_profit_pct, 0.0)
        round_trip_fee = max(0.0, 2.0 * self._safe_float(fee_rate, 0.0))

        if sl > 0.0:
            stop_price = ep * (1.0 - sl / 100.0)
            if mp <= stop_price:
                return ExitSignal(
                    should_exit=True,
                    reason="stop_loss",
                    meta={
                        "entry_price": ep,
                        "market_price": mp,
                        "stop_loss_pct": sl,
                        "stop_price": stop_price,
                    },
                )

        if tp > 0.0:
            gross_take_pct = (tp / 100.0) + round_trip_fee
            take_price = ep * (1.0 + gross_take_pct)
            if mp >= take_price:
                return ExitSignal(
                    should_exit=True,
                    reason="take_profit",
                    meta={
                        "entry_price": ep,
                        "market_price": mp,
                        "take_profit_pct": tp,
                        "round_trip_fee_pct": round_trip_fee * 100.0,
                        "take_price": take_price,
                    },
                )

        return ExitSignal(should_exit=False)

    def evaluate_short_exit(
        self,
        *,
        entry_price: float | None,
        market_price: float,
        stop_loss_pct: float | None,
        take_profit_pct: float | None,
        exit_mode: str = "static",
        atr_value: float | None = None,
        atr_stop_mult: float | None = None,
        atr_take_mult: float | None = None,
        atr_reference_mode: str = "entry",
        trough_price_since_entry: float | None = None,
        fee_rate: float = 0.0,
    ) -> ExitSignal:
        ep = self._safe_float(entry_price, 0.0)
        mp = self._safe_float(market_price, 0.0)
        if ep <= 0.0 or mp <= 0.0:
            return ExitSignal(should_exit=False)

        mode = self._normalize_exit_mode(exit_mode)

        if mode == "atr":
            atr = self._safe_float(atr_value, 0.0)
            stop_mult = self._safe_float(atr_stop_mult, 0.0)
            take_mult = self._safe_float(atr_take_mult, 0.0)
            ref_mode = self._normalize_atr_reference_mode(atr_reference_mode)

            if atr <= 0.0:
                return ExitSignal(should_exit=False)

            trough = self._safe_float(trough_price_since_entry, ep)
            stop_anchor = min(ep, trough) if ref_mode == "floating" else ep

            if stop_mult > 0.0:
                stop_price = stop_anchor + (atr * stop_mult)
                if mp >= stop_price:
                    return ExitSignal(
                        should_exit=True,
                        reason="atr_stop_loss",
                        meta={
                            "entry_price": ep,
                            "market_price": mp,
                            "atr_value": atr,
                            "atr_stop_mult": stop_mult,
                            "atr_reference_mode": ref_mode,
                            "trough_price_since_entry": trough,
                            "stop_anchor": stop_anchor,
                            "stop_price": stop_price,
                        },
                    )

            if take_mult > 0.0:
                take_price = ep - (atr * take_mult)
                if mp <= take_price:
                    return ExitSignal(
                        should_exit=True,
                        reason="atr_take_profit",
                        meta={
                            "entry_price": ep,
                            "market_price": mp,
                            "atr_value": atr,
                            "atr_take_mult": take_mult,
                            "take_price": take_price,
                        },
                    )

            return ExitSignal(should_exit=False)

        sl = self._safe_float(stop_loss_pct, 0.0)
        tp = self._safe_float(take_profit_pct, 0.0)
        round_trip_fee = max(0.0, 2.0 * self._safe_float(fee_rate, 0.0))

        if sl > 0.0:
            stop_price = ep * (1.0 + sl / 100.0)
            if mp >= stop_price:
                return ExitSignal(
                    should_exit=True,
                    reason="stop_loss",
                    meta={
                        "entry_price": ep,
                        "market_price": mp,
                        "stop_loss_pct": sl,
                        "stop_price": stop_price,
                    },
                )

        if tp > 0.0:
            gross_take_pct = (tp / 100.0) + round_trip_fee
            take_price = ep * (1.0 - gross_take_pct)
            if mp <= take_price:
                return ExitSignal(
                    should_exit=True,
                    reason="take_profit",
                    meta={
                        "entry_price": ep,
                        "market_price": mp,
                        "take_profit_pct": tp,
                        "round_trip_fee_pct": round_trip_fee * 100.0,
                        "take_price": take_price,
                    },
                )

        return ExitSignal(should_exit=False)

