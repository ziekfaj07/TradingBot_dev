from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from core.execution_models import PortfolioState


@dataclass
class RiskGateResult:
    allowed: bool
    reason: Optional[str] = None
    meta: dict | None = None


@dataclass
class ExitSignal:
    should_exit: bool
    reason: Optional[str] = None
    meta: dict | None = None


class RiskEngine:
    """
    v0.6 risk engine:
    - position sizing
    - max drawdown stop
    - max trades per day
    - cooldown logic
    - static stop loss / take profit

    Defaults are intentionally no-op / backward-compatible.
    """

    def _safe_float(self, value: object, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _safe_int(self, value: object, default: int = 0) -> int:
        try:
            if value is None:
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    def _normalize_mode(self, value: object) -> str:
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

    def _coerce_epoch_seconds(self, value: object) -> float | None:
        if value is None:
            return None

        if isinstance(value, (int, float)):
            v = float(value)
            if not math.isfinite(v):
                return None

            av = abs(v)
            # ns / us / ms / s
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

        if hasattr(value, "timestamp"):
            try:
                ts = value.timestamp()
                return float(ts) if ts is not None else None
            except Exception:
                pass

        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None

            try:
                return self._coerce_epoch_seconds(float(text))
            except Exception:
                pass

            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                return float(dt.timestamp())
            except Exception:
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
        """
        Returns:
        - None => keep old engine behavior (all-in)
        - float qty => use this qty
        """
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
        fee_rate = max(0.0, self._safe_float(fee_rate))
        max_qty = max(0.0, self._safe_float(max_qty))
        lev = min(max(self._safe_float(leverage, 1.0), 1.0), max(self._safe_float(max_leverage, 1.0), 1.0))

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
            notional_after_fee = max(0.0, budget * (1.0 - fee_rate))
            qty = notional_after_fee / px
        else:
            notional = budget * lev
            fee = notional * fee_rate
            if fee >= cash:
                return 0.0
            qty = notional / px

        if not math.isfinite(qty) or qty <= 0.0:
            return 0.0

        return min(qty, max_qty) if max_qty > 0 else qty

    def evaluate_entry_gate(
        self,
        *,
        now_ts: float | int | str | datetime | None,
        current_equity: float,
        peak_equity: float,
        trades_today: int,
        last_exit_ts: float | int | str | datetime | None,
        max_drawdown_pct: float | None,
        max_trades_per_day: int | None,
        cooldown_seconds: int | None,
    ) -> RiskGateResult:
        now_sec = self._coerce_epoch_seconds(now_ts)
        if now_sec is None:
            now_sec = time.time()

        current_equity = self._safe_float(current_equity, 0.0)
        peak_equity = max(self._safe_float(peak_equity, current_equity), current_equity)

        max_dd = self._safe_float(max_drawdown_pct, 0.0)
        if max_dd > 0 and peak_equity > 0:
            dd_pct = max(0.0, (peak_equity - current_equity) / peak_equity * 100.0)
            if dd_pct >= max_dd:
                return RiskGateResult(
                    allowed=False,
                    reason="max_drawdown_stop",
                    meta={
                        "drawdown_pct": dd_pct,
                        "max_drawdown_pct": max_dd,
                        "current_equity": current_equity,
                        "peak_equity": peak_equity,
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

    def evaluate_long_exit(
        self,
        *,
        entry_price: float | None,
        market_price: float,
        stop_loss_pct: float | None,
        take_profit_pct: float | None,
    ) -> ExitSignal:
        ep = self._safe_float(entry_price, 0.0)
        mp = self._safe_float(market_price, 0.0)
        if ep <= 0.0 or mp <= 0.0:
            return ExitSignal(should_exit=False)

        sl = self._safe_float(stop_loss_pct, 0.0)
        tp = self._safe_float(take_profit_pct, 0.0)

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
            take_price = ep * (1.0 + tp / 100.0)
            if mp >= take_price:
                return ExitSignal(
                    should_exit=True,
                    reason="take_profit",
                    meta={
                        "entry_price": ep,
                        "market_price": mp,
                        "take_profit_pct": tp,
                        "take_price": take_price,
                    },
                )

        return ExitSignal(should_exit=False)