from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from core.execution_models import PortfolioState


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
        if value is None:
            return default
        if isinstance(value, bool):
            return float(int(value))
        if isinstance(value, (int, float)):
            v = float(value)
            return v if math.isfinite(v) else default
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return default
            try:
                v = float(text)
                return v if math.isfinite(v) else default
            except ValueError:
                return default
        return default

    def _safe_float_or_none(self, value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return float(int(value))
        if isinstance(value, (int, float)):
            v = float(value)
            return v if math.isfinite(v) else None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                v = float(text)
                return v if math.isfinite(v) else None
            except ValueError:
                return None
        return None

    def _safe_int(self, value: Any, default: int = 0) -> int:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                return default
            return int(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return default
            try:
                return int(float(text))
            except ValueError:
                return default
        return default

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
    ) -> float | None:
        px = self._safe_float(entry_price)
        if px <= 0.0 or not math.isfinite(px):
            return None

        mode = self._normalize_mode(sizing_mode)
        if mode == "all_in":
            return None

        value = self._safe_float(sizing_value, 0.0)
        if value <= 0.0:
            return None

        mt = str(market_type or "spot").strip().lower()
        cash = max(0.0, self._safe_float(state.cash))
        fee_rate_safe = max(0.0, self._safe_float(fee_rate))
        max_qty_safe = max(0.0, self._safe_float(max_qty))
        lev = min(
            max(self._safe_float(leverage, 1.0), 1.0),
            max(self._safe_float(max_leverage, 1.0), 1.0),
        )

        if mode == "fixed_usdt":
            budget = value
        elif mode == "fixed_pct":
            budget = cash * (value / 100.0)
        else:
            return None

        budget = max(0.0, min(budget, cash))
        if budget <= 0.0:
            return 0.0

        if mt == "spot":
            notional_after_fee = max(0.0, budget * (1.0 - fee_rate_safe))
            qty = notional_after_fee / px
        else:
            notional = budget * lev
            fee = notional * fee_rate_safe
            if fee >= cash:
                return 0.0
            qty = notional / px

        if not math.isfinite(qty) or qty <= 0.0:
            return 0.0

        return min(qty, max_qty_safe) if max_qty_safe > 0 else qty

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
    ) -> ExitLevels:
        sl = self._safe_float(stop_loss_pct, 0.0)
        tp = self._safe_float(take_profit_pct, 0.0)

        stop_price: float | None = None
        take_price: float | None = None

        if sl > 0.0:
            stop_price = entry_price * (1.0 - sl / 100.0)
        if tp > 0.0:
            take_price = entry_price * (1.0 + tp / 100.0)

        return ExitLevels(stop_price=stop_price, take_price=take_price, exit_family="static")

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
        )

    def evaluate_long_exit(
        self,
        *,
        entry_price: float | None,
        market_price: float,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        exit_mode: str = "static",
        atr_value: float | None = None,
        atr_stop_mult: float | None = None,
        atr_take_mult: float | None = None,
    ) -> ExitSignal:
        ep = self._safe_float(entry_price, 0.0)
        mp = self._safe_float(market_price, 0.0)

        if ep <= 0.0 or mp <= 0.0:
            return ExitSignal(should_exit=False)

        levels = self.resolve_long_exit_levels(
            entry_price=ep,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            exit_mode=exit_mode,
            atr_value=atr_value,
            atr_stop_mult=atr_stop_mult,
            atr_take_mult=atr_take_mult,
        )

        meta: dict[str, Any] = {
            "entry_price": ep,
            "market_price": mp,
            "stop_price": levels.stop_price,
            "take_price": levels.take_price,
            "exit_family": levels.exit_family,
        }

        if levels.exit_family == "static":
            meta["stop_loss_pct"] = self._safe_float(stop_loss_pct, 0.0)
            meta["take_profit_pct"] = self._safe_float(take_profit_pct, 0.0)
        elif levels.exit_family == "atr":
            meta["atr_value"] = self._safe_float(atr_value, 0.0)
            meta["atr_stop_mult"] = self._safe_float(atr_stop_mult, 0.0)
            meta["atr_take_mult"] = self._safe_float(atr_take_mult, 0.0)

        if levels.stop_price is not None and mp <= levels.stop_price:
            return ExitSignal(should_exit=True, reason="stop_loss", meta=meta)

        if levels.take_price is not None and mp >= levels.take_price:
            return ExitSignal(should_exit=True, reason="take_profit", meta=meta)

        return ExitSignal(should_exit=False, meta=meta)
