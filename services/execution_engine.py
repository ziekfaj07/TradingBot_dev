import math
from typing import Any, Optional, Tuple

from core.execution_models import Fill, PortfolioState


class ExecutionEngine:
    """
    Reusable execution/fill engine for:
    - backtests
    - paper trading
    - live trading later

    Futures model:
    equity = wallet_cash + qty * (mark_price - entry_price)
    """

    def __init__(
        self,
        fee_rate: float = 0.001,
        liquidation_fee_rate: float | None = None,
        slippage_bps: float = 2.0,
        max_leverage: float = 50.0,
        max_qty: float = 10.0,
        maintenance_margin: float = 0.005,
        min_order_notional: float = 5.0,
        min_qty: float = 0.0,
    ):
        self.fee_rate = float(fee_rate)
        self.liquidation_fee_rate = float(
            fee_rate if liquidation_fee_rate is None else liquidation_fee_rate
        )
        self.slippage_bps = float(slippage_bps)
        self.max_leverage = float(max_leverage)
        self.max_qty = float(max_qty)
        self.maintenance_margin = float(maintenance_margin)
        self.min_order_notional = float(min_order_notional)
        self.min_qty = float(min_qty)

    _NUMERIC_EPSILON = 1e-12
    _MONEY_EPSILON = 1e-9

    def apply_slippage(self, price: float, is_buy: bool) -> float:
        slip = self.slippage_bps / 10000.0
        return price * (1.0 + slip) if is_buy else price * (1.0 - slip)

    def _clean_float(self, value: float, eps: float | None = None) -> float:
        epsilon = self._NUMERIC_EPSILON if eps is None else float(eps)
        try:
            v = float(value)
        except (TypeError, ValueError):
            return 0.0

        if not math.isfinite(v):
            return 0.0

        if abs(v) <= epsilon:
            return 0.0

        return float(v)

    def _clean_money(self, value: float) -> float:
        return float(round(self._clean_float(value, eps=self._MONEY_EPSILON), 12))

    def _margin_mode(self, state: PortfolioState) -> str:
        mode = str(getattr(state, "margin_mode", "cross") or "cross").strip().lower()
        return "isolated" if mode == "isolated" else "cross"

    def _get_isolated_margin(self, state: PortfolioState) -> float:
        try:
            return self._clean_money(float(getattr(state, "isolated_margin", 0.0) or 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _set_isolated_margin(self, state: PortfolioState, value: float) -> None:
        try:
            setattr(state, "isolated_margin", self._clean_money(value))
        except Exception:
            pass

    def _passes_entry_floor(self, price: float, qty: float) -> bool:
        px = self._clean_float(price)
        q = self._clean_float(qty)
        if px <= 0.0 or q <= 0.0:
            return False
        if self.min_qty > 0.0 and q < self.min_qty:
            return False
        if self.min_order_notional > 0.0 and (px * q) < self.min_order_notional:
            return False
        return True

    def mark_equity(self, state: PortfolioState, market_type: str, market_price: float) -> float:
        mt = (market_type or "spot").lower()

        if mt == "spot":
            return self._clean_money(float(state.cash + state.position_qty * market_price))

        base_cash = float(state.cash)
        if self._margin_mode(state) == "isolated":
            base_cash += float(self._get_isolated_margin(state))

        if state.position_qty == 0.0 or state.entry_price is None:
            return self._clean_money(base_cash)

        return self._clean_money(
            float(base_cash + state.position_qty * (market_price - state.entry_price))
        )

    def _set_active_trade_id(self, state: PortfolioState, trade_id: int) -> None:
        try:
            setattr(state, "active_trade_id", int(trade_id))
        except Exception:
            pass

    def _get_active_trade_id(self, state: PortfolioState, fallback_trade_id: int) -> int:
        try:
            active_trade_id = getattr(state, "active_trade_id", None)
            if active_trade_id is not None:
                return int(active_trade_id)
        except Exception:
            pass
        return int(fallback_trade_id)

    def _clear_active_trade_id(self, state: PortfolioState) -> None:
        try:
            setattr(state, "active_trade_id", None)
        except Exception:
            pass

    def _add_realized_pnl(self, state: PortfolioState, pnl: float) -> None:
        try:
            current = getattr(state, "realized_pnl", 0.0)
            setattr(state, "realized_pnl", self._clean_money(float(current) + float(pnl)))
        except Exception:
            pass

    def _estimate_entry_fee(self, entry_px: float, qty: float, market_type: str) -> float:
        mt = (market_type or "spot").lower()
        gross_cost_after_fee = float(abs(qty) * entry_px)

        if gross_cost_after_fee <= 0 or self.fee_rate <= 0:
            return 0.0

        if mt == "spot":
            if self.fee_rate >= 1.0:
                return 0.0
            return self._clean_money(gross_cost_after_fee * self.fee_rate / (1.0 - self.fee_rate))

        return self._clean_money(gross_cost_after_fee * self.fee_rate)

    def apply_margin_snapshot(self, state: PortfolioState, snapshot: Any) -> None:
        if snapshot is None:
            return

        field_map = {
            "mark_price": "mark_price",
            "liquidation_price": "liquidation_price",
            "maintenance_margin": "maintenance_margin",
            "maintenance_margin_rate": "maintenance_margin_rate",
            "maintenance_amount": "maintenance_amount",
            "margin_balance": "margin_balance",
            "margin_ratio": "margin_ratio",
            "bankruptcy_price": "bankruptcy_price",
            "margin": "margin",
            "borrowed": "borrowed",
        }

        for src, dest in field_map.items():
            if hasattr(snapshot, src):
                try:
                    setattr(state, dest, getattr(snapshot, src))
                except Exception:
                    pass

    def compute_liquidation_price(
        self,
        state: PortfolioState,
        market_type: str,
        market_price: float,
        leverage: float,
    ) -> bool:
        mt = (market_type or "spot").lower()
        if mt != "futures":
            return False

        if leverage <= 1.0:
            return False

        if state.position_qty == 0.0 or state.entry_price is None:
            return False

        notional = abs(state.position_qty) * market_price
        if notional <= 0:
            return False

        eq = self.mark_equity(state, mt, market_price)
        return eq <= (self.maintenance_margin * notional)

    def enter_long(
        self,
        ts_iso: str,
        state: PortfolioState,
        close: float,
        market_type: str,
        leverage: float,
        trade_id: int,
        qty_override: float | None = None,
    ) -> Tuple[PortfolioState, Optional[Fill]]:
        mt = (market_type or "spot").lower()
        buy_px = self.apply_slippage(close, is_buy=True)

        if buy_px <= 0 or not math.isfinite(buy_px):
            return state, None

        entry_trade_id = int(trade_id)
        requested_qty = None
        if qty_override is not None:
            try:
                requested_qty = float(qty_override)
            except (TypeError, ValueError):
                requested_qty = None

        if requested_qty is not None and (requested_qty <= 0 or not math.isfinite(requested_qty)):
            return state, None

        fee = 0.0

        if mt == "spot":
            if requested_qty is None:
                notional = float(state.cash)
                fee = self._clean_money(notional * self.fee_rate)
                notional_after_fee = max(0.0, notional - fee)
                qty = notional_after_fee / buy_px
                remaining_cash = 0.0
            else:
                qty = float(requested_qty)
                gross_cost_after_fee = qty * buy_px
                if self.fee_rate >= 1.0:
                    return state, None
                fee = self._clean_money(
                    gross_cost_after_fee * self.fee_rate / max(1e-12, (1.0 - self.fee_rate))
                )
                total_cash_needed = gross_cost_after_fee + fee
                if total_cash_needed > float(state.cash) + 1e-12:
                    return state, None
                remaining_cash = self._clean_money(max(0.0, float(state.cash) - total_cash_needed))

            qty = min(float(qty), self.max_qty) if self.max_qty > 0 else float(qty)
            if (not math.isfinite(qty)) or qty <= 0.0 or not self._passes_entry_floor(buy_px, qty):
                return state, None

            state.position_qty = self._clean_float(float(qty))
            state.cash = self._clean_money(float(remaining_cash))
            state.entry_price = self._clean_money(float(buy_px))
            state.side = "long"

        else:
            lev = min(max(float(leverage), 1.0), self.max_leverage)
            margin_mode = self._margin_mode(state)
            available_cash = max(0.0, float(state.cash))

            if margin_mode == "isolated":
                if requested_qty is None:
                    denom = buy_px * ((1.0 / lev) + self.fee_rate)
                    if denom <= 0.0 or not math.isfinite(denom):
                        return state, None
                    qty = available_cash / denom
                else:
                    qty = float(requested_qty)

                qty = min(float(qty), self.max_qty) if self.max_qty > 0 else float(qty)
                if (not math.isfinite(qty)) or qty <= 0.0 or not self._passes_entry_floor(buy_px, qty):
                    return state, None

                notional = qty * buy_px
                initial_margin = self._clean_money(notional / lev)
                fee = self._clean_money(notional * self.fee_rate)
                wallet_needed = self._clean_money(initial_margin + fee)
                if wallet_needed > available_cash + 1e-12:
                    return state, None

                state.cash = self._clean_money(max(0.0, available_cash - wallet_needed))
                self._set_isolated_margin(state, initial_margin)
                state.margin = initial_margin
            else:
                if requested_qty is None:
                    notional = available_cash * lev
                    fee = self._clean_money(notional * self.fee_rate)
                    qty = notional / buy_px
                else:
                    qty = float(requested_qty)
                    notional = qty * buy_px
                    fee = self._clean_money(notional * self.fee_rate)
                    if fee > available_cash + 1e-12:
                        return state, None

                qty = min(float(qty), self.max_qty) if self.max_qty > 0 else float(qty)
                if (not math.isfinite(qty)) or qty <= 0.0 or not self._passes_entry_floor(buy_px, qty):
                    return state, None

                state.cash = self._clean_money(max(0.0, available_cash - fee))
                self._set_isolated_margin(state, 0.0)
                state.margin = 0.0

            state.position_qty = self._clean_float(float(qty))
            state.entry_price = self._clean_money(float(buy_px))
            state.side = "long"

        self._set_active_trade_id(state, entry_trade_id)
        eq_after = self._clean_money(self.mark_equity(state, mt, close))

        fill = Fill(
            timestamp=ts_iso,
            type="ENTRY",
            side="long",
            price=self._clean_money(float(buy_px)),
            qty=self._clean_money(float(state.position_qty)),
            fee=self._clean_money(float(fee)),
            equity_after=self._clean_money(float(eq_after)),
            trade_id=entry_trade_id,
            entry_price=None,
            exit_price=None,
            pnl=None,
        )
        return state, fill

    def exit_long(
        self,
        ts_iso: str,
        state: PortfolioState,
        close: float,
        market_type: str,
        trade_id: int,
    ) -> Tuple[PortfolioState, Optional[Fill]]:
        mt = (market_type or "spot").lower()
        if state.position_qty <= 0.0:
            return state, None

        exit_trade_id = self._get_active_trade_id(state, trade_id)
        sell_px = self.apply_slippage(close, is_buy=False)

        if sell_px <= 0 or not math.isfinite(sell_px):
            return state, None

        exit_qty = float(state.position_qty)
        entry_px = float(state.entry_price) if state.entry_price is not None else float(sell_px)

        if mt == "spot":
            notional = abs(exit_qty) * sell_px
            fee = self._clean_money(notional * self.fee_rate)

            entry_fee = self._estimate_entry_fee(entry_px, exit_qty, mt)
            gross_pnl = self._clean_money(float(exit_qty) * (float(sell_px) - entry_px))
            net_pnl = self._clean_money(float(gross_pnl - entry_fee - fee))

            state.cash = self._clean_money(float(state.cash) + float(notional - fee))

        else:
            gross_pnl = self._clean_money(float(exit_qty) * (float(sell_px) - entry_px))
            notional = abs(exit_qty) * sell_px
            fee = self._clean_money(notional * self.fee_rate)
            net_pnl = self._clean_money(float(gross_pnl - fee))

            if self._margin_mode(state) == "isolated":
                released_margin = self._get_isolated_margin(state)
                state.cash = self._clean_money(float(state.cash) + released_margin + gross_pnl - float(fee))
                self._set_isolated_margin(state, 0.0)
                state.margin = 0.0
            else:
                state.cash = self._clean_money(float(state.cash) + gross_pnl - float(fee))

        self._add_realized_pnl(state, net_pnl)

        state.position_qty = 0.0
        state.entry_price = None
        state.side = None
        self._clear_active_trade_id(state)

        eq_after = self._clean_money(self.mark_equity(state, mt, close))

        fill = Fill(
            timestamp=ts_iso,
            type="EXIT",
            side="long",
            price=self._clean_money(float(sell_px)),
            qty=self._clean_money(float(exit_qty)),
            fee=self._clean_money(float(fee)),
            equity_after=self._clean_money(float(eq_after)),
            entry_price=self._clean_money(float(entry_px)),
            exit_price=self._clean_money(float(sell_px)),
            trade_id=exit_trade_id,
            pnl=self._clean_money(float(net_pnl)),
        )

        return state, fill

    def liquidate(
        self,
        ts_iso: str,
        state: PortfolioState,
        close: float,
        market_type: str,
        trade_id: int,
        exec_price: float | None = None,
        mark_price: float | None = None,
        margin_snapshot: Any | None = None,
    ) -> Tuple[PortfolioState, Optional[Fill]]:
        mt = (market_type or "spot").lower()
        if state.position_qty == 0.0:
            return state, None

        if margin_snapshot is not None:
            self.apply_margin_snapshot(state, margin_snapshot)

        liquidation_trade_id = self._get_active_trade_id(state, trade_id)
        position_side = state.side or "long"

        if exec_price is not None and math.isfinite(float(exec_price)) and float(exec_price) > 0.0:
            exit_px = float(exec_price)
        else:
            exit_px = self.apply_slippage(close, is_buy=False)

        if exit_px <= 0 or not math.isfinite(exit_px):
            state.position_qty = 0.0
            state.entry_price = None
            state.side = None
            self._clear_active_trade_id(state)
            return state, None

        exit_qty = float(state.position_qty)
        entry_px = float(state.entry_price) if state.entry_price is not None else float(exit_px)

        notional = abs(exit_qty) * exit_px
        fee = self._clean_money(notional * self.liquidation_fee_rate)

        if mt == "spot":
            entry_fee = self._estimate_entry_fee(entry_px, exit_qty, mt)
            gross_pnl = self._clean_money(float(exit_qty) * (float(exit_px) - entry_px))
            net_pnl = self._clean_money(float(gross_pnl - entry_fee - fee))
            state.cash = self._clean_money(float(state.cash) + float(notional - fee))
        else:
            gross_pnl = self._clean_money(float(exit_qty) * (float(exit_px) - entry_px))
            net_pnl = self._clean_money(float(gross_pnl - fee))

            if self._margin_mode(state) == "isolated":
                released_margin = self._get_isolated_margin(state)
                state.cash = self._clean_money(float(state.cash) + released_margin + gross_pnl - float(fee))
                self._set_isolated_margin(state, 0.0)
                state.margin = 0.0
            else:
                state.cash = self._clean_money(float(state.cash) + gross_pnl - float(fee))

        self._add_realized_pnl(state, net_pnl)

        state.position_qty = 0.0
        state.entry_price = None
        state.side = None
        self._clear_active_trade_id(state)

        eq_mark_price = float(mark_price) if mark_price is not None else float(close)
        eq_after = self._clean_money(self.mark_equity(state, mt, eq_mark_price))

        fill = Fill(
            timestamp=ts_iso,
            type="LIQUIDATION",
            side=position_side,
            price=self._clean_money(float(exit_px)),
            qty=self._clean_money(float(exit_qty)),
            fee=self._clean_money(float(fee)),
            equity_after=self._clean_money(float(eq_after)),
            entry_price=self._clean_money(float(entry_px)),
            exit_price=self._clean_money(float(exit_px)),
            trade_id=liquidation_trade_id,
            pnl=self._clean_money(float(net_pnl)),
        )

        return state, fill

    def enter_short(self, *args, **kwargs):
        raise NotImplementedError("Short trading is not implemented yet.")

    def exit_short(self, *args, **kwargs):
        raise NotImplementedError("Short trading is not implemented yet.")