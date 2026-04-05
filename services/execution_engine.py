import math
from typing import Optional, Tuple

from core.execution_models import Fill, PortfolioState
from services.margin_engine import MarginSnapshot


class ExecutionEngine:
    def __init__(
        self,
        fee_rate: float = 0.001,
        slippage_bps: float = 2.0,
        max_leverage: float = 50.0,
        max_qty: float = 10.0,
        maintenance_margin: float = 0.005,
        liquidation_fee_rate: float = 0.005,
    ):
        self.fee_rate = float(fee_rate)
        self.slippage_bps = float(slippage_bps)
        self.max_leverage = float(max_leverage)
        self.max_qty = float(max_qty)
        self.maintenance_margin = float(maintenance_margin)
        self.liquidation_fee_rate = float(liquidation_fee_rate)

    def apply_slippage(self, price: float, is_buy: bool) -> float:
        slip = self.slippage_bps / 10000.0
        return price * (1.0 + slip) if is_buy else price * (1.0 - slip)

    def mark_equity(self, state: PortfolioState, market_type: str, market_price: float) -> float:
        mt = (market_type or "spot").lower()
        if mt == "spot":
            return float(state.cash + state.position_qty * market_price)

        if state.position_qty == 0.0 or state.entry_price is None:
            return float(state.cash)

        qty = abs(float(state.position_qty))
        entry_price = float(state.entry_price)
        side = str(state.side or "long").lower()

        if side == "short":
            unrealized = qty * (entry_price - float(market_price))
        else:
            unrealized = qty * (float(market_price) - entry_price)

        return float(state.cash + unrealized)

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
            setattr(state, "realized_pnl", float(current) + float(pnl))
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
            return float(gross_cost_after_fee * self.fee_rate / (1.0 - self.fee_rate))

        return float(gross_cost_after_fee * self.fee_rate)

    def _reset_margin_state(self, state: PortfolioState) -> None:
        state.entry_notional = 0.0
        state.isolated_margin = 0.0
        state.maintenance_margin = 0.0
        state.maintenance_margin_rate = 0.0
        state.maintenance_amount = 0.0
        state.margin_balance = 0.0
        state.margin_ratio = None
        state.mark_price = None
        state.liquidation_price = None
        state.bankruptcy_price = None

    def apply_margin_snapshot(self, state: PortfolioState, snapshot: MarginSnapshot | None) -> None:
        if snapshot is None:
            self._reset_margin_state(state)
            return

        state.mark_price = float(snapshot.mark_price)
        state.maintenance_margin = float(snapshot.maintenance_margin)
        state.maintenance_margin_rate = float(snapshot.maintenance_margin_rate)
        state.maintenance_amount = float(snapshot.maintenance_amount)
        state.margin_balance = float(snapshot.margin_balance)
        state.margin_ratio = snapshot.margin_ratio
        state.liquidation_price = snapshot.liquidation_price
        state.bankruptcy_price = snapshot.bankruptcy_price
        state.margin_mode = str(snapshot.margin_mode)

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

        if requested_qty is not None:
            if not math.isfinite(requested_qty) or requested_qty <= 0.0:
                return state, None
            if abs(requested_qty) > self.max_qty:
                return state, None

        if mt == "spot":
            if requested_qty is None:
                notional = float(state.cash)
                fee = notional * self.fee_rate
                notional_after_fee = max(0.0, notional - fee)
                qty = notional_after_fee / buy_px
            else:
                qty = float(requested_qty)
                notional_after_fee = qty * buy_px
                if self.fee_rate >= 1.0:
                    return state, None
                fee = notional_after_fee * self.fee_rate / (1.0 - self.fee_rate)
                gross_cash_needed = notional_after_fee + fee
                if gross_cash_needed > float(state.cash) + 1e-12:
                    return state, None

            if (not math.isfinite(qty)) or qty <= 0.0 or abs(qty) > self.max_qty:
                return state, None

            state.position_qty = float(qty)
            if requested_qty is None:
                state.cash = 0.0
            else:
                state.cash = max(0.0, float(state.cash) - (qty * buy_px) - fee)
            state.entry_price = float(buy_px)
            state.side = "long"

        else:
            lev = min(max(float(leverage), 1.0), self.max_leverage)

            if requested_qty is None:
                notional = float(state.cash) * lev
                fee = notional * self.fee_rate
                qty = notional / buy_px
            else:
                qty = float(requested_qty)
                notional = qty * buy_px
                fee = notional * self.fee_rate

            required_margin = notional / lev
            if required_margin + fee > float(state.cash) + 1e-12:
                return state, None

            if (not math.isfinite(qty)) or qty <= 0.0 or abs(qty) > self.max_qty:
                return state, None

            state.cash = max(0.0, float(state.cash) - fee)
            state.position_qty = float(qty)
            state.entry_price = float(buy_px)
            state.side = "long"
            state.entry_notional = float(notional)
            state.margin_mode = str(getattr(state, "margin_mode", "isolated") or "isolated").lower()
            state.isolated_margin = float(required_margin) if state.margin_mode == "isolated" else 0.0

        self._set_active_trade_id(state, entry_trade_id)
        eq_after = self.mark_equity(state, mt, close)

        fill = Fill(
            timestamp=ts_iso,
            type="ENTRY",
            side="long",
            price=float(buy_px),
            qty=float(state.position_qty),
            fee=float(fee),
            equity_after=float(eq_after),
            trade_id=entry_trade_id,
            margin_mode=getattr(state, "margin_mode", None),
        )
        return state, fill

    def exit_long(
        self,
        ts_iso: str,
        state: PortfolioState,
        close: float,
        market_type: str,
        trade_id: int,
        *,
        exit_reason: str = "signal_exit",
        mark_price: float | None = None,
        liquidation_price: float | None = None,
        maintenance_margin: float | None = None,
        margin_balance: float | None = None,
        margin_ratio: float | None = None,
    ) -> Tuple[PortfolioState, Optional[Fill]]:
        mt = (market_type or "spot").lower()

        if state.position_qty <= 0.0 or str(state.side or "long").lower() != "long":
            return state, None

        exit_trade_id = self._get_active_trade_id(state, trade_id)
        sell_px = self.apply_slippage(close, is_buy=False)

        if sell_px <= 0 or not math.isfinite(sell_px):
            return state, None

        exit_qty = float(state.position_qty)
        entry_px = float(state.entry_price) if state.entry_price is not None else float(sell_px)
        entry_fee = self._estimate_entry_fee(entry_px, exit_qty, mt)

        if mt == "spot":
            notional = abs(exit_qty) * sell_px
            fee = notional * self.fee_rate
            gross_pnl = exit_qty * (sell_px - entry_px)
            net_pnl = gross_pnl - entry_fee - fee
            state.cash = float(state.cash) + float(notional - fee)
            liquidation_fee = 0.0
        else:
            gross_pnl = exit_qty * (sell_px - entry_px)
            notional = abs(exit_qty) * sell_px
            fee = notional * self.fee_rate
            net_pnl = gross_pnl - entry_fee - fee
            state.cash = float(state.cash) + gross_pnl - fee
            self._add_realized_pnl(state, net_pnl)
            liquidation_fee = 0.0

        saved_margin_mode = getattr(state, "margin_mode", None)

        state.position_qty = 0.0
        state.entry_price = None
        state.side = None
        self._clear_active_trade_id(state)
        self._reset_margin_state(state)

        eq_after = self.mark_equity(state, mt, close)

        fill = Fill(
            timestamp=ts_iso,
            type="EXIT",
            side="long",
            price=float(sell_px),
            qty=float(exit_qty),
            fee=float(fee),
            equity_after=float(eq_after),
            entry_price=float(entry_px),
            exit_price=float(sell_px),
            trade_id=exit_trade_id,
            pnl=float(net_pnl),
            exit_reason=exit_reason,
            mark_price=mark_price,
            liquidation_price=liquidation_price,
            maintenance_margin=maintenance_margin,
            margin_balance=margin_balance,
            margin_ratio=margin_ratio,
            liquidation_fee=liquidation_fee,
            margin_mode=saved_margin_mode,
        )
        return state, fill

    def enter_short(
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
        if mt != "futures":
            return state, None

        sell_px = self.apply_slippage(close, is_buy=False)
        if sell_px <= 0 or not math.isfinite(sell_px):
            return state, None

        entry_trade_id = int(trade_id)
        requested_qty = None
        if qty_override is not None:
            try:
                requested_qty = float(qty_override)
            except (TypeError, ValueError):
                requested_qty = None

        if requested_qty is not None:
            if not math.isfinite(requested_qty) or requested_qty <= 0.0:
                return state, None
            if abs(requested_qty) > self.max_qty:
                return state, None

        lev = min(max(float(leverage), 1.0), self.max_leverage)

        if requested_qty is None:
            notional = float(state.cash) * lev
            fee = notional * self.fee_rate
            qty = notional / sell_px
        else:
            qty = float(requested_qty)
            notional = qty * sell_px
            fee = notional * self.fee_rate

        required_margin = notional / lev
        if required_margin + fee > float(state.cash) + 1e-12:
            return state, None

        if (not math.isfinite(qty)) or qty <= 0.0 or abs(qty) > self.max_qty:
            return state, None

        state.cash = max(0.0, float(state.cash) - fee)
        state.position_qty = float(qty)
        state.entry_price = float(sell_px)
        state.side = "short"
        state.entry_notional = float(notional)
        state.margin_mode = str(getattr(state, "margin_mode", "isolated") or "isolated").lower()
        state.isolated_margin = float(required_margin) if state.margin_mode == "isolated" else 0.0

        self._set_active_trade_id(state, entry_trade_id)
        eq_after = self.mark_equity(state, mt, close)

        fill = Fill(
            timestamp=ts_iso,
            type="ENTRY",
            side="short",
            price=float(sell_px),
            qty=float(state.position_qty),
            fee=float(fee),
            equity_after=float(eq_after),
            trade_id=entry_trade_id,
            margin_mode=getattr(state, "margin_mode", None),
        )
        return state, fill

    def exit_short(
        self,
        ts_iso: str,
        state: PortfolioState,
        close: float,
        market_type: str,
        trade_id: int,
        *,
        exit_reason: str = "signal_exit",
        mark_price: float | None = None,
        liquidation_price: float | None = None,
        maintenance_margin: float | None = None,
        margin_balance: float | None = None,
        margin_ratio: float | None = None,
    ) -> Tuple[PortfolioState, Optional[Fill]]:
        mt = (market_type or "spot").lower()

        if state.position_qty <= 0.0 or str(state.side or "").lower() != "short":
            return state, None

        exit_trade_id = self._get_active_trade_id(state, trade_id)
        buy_px = self.apply_slippage(close, is_buy=True)

        if buy_px <= 0 or not math.isfinite(buy_px):
            return state, None

        exit_qty = float(state.position_qty)
        entry_px = float(state.entry_price) if state.entry_price is not None else float(buy_px)
        entry_fee = self._estimate_entry_fee(entry_px, exit_qty, mt)

        gross_pnl = exit_qty * (entry_px - buy_px)
        notional = abs(exit_qty) * buy_px
        fee = notional * self.fee_rate
        net_pnl = gross_pnl - entry_fee - fee
        state.cash = float(state.cash) + gross_pnl - fee
        self._add_realized_pnl(state, net_pnl)

        saved_margin_mode = getattr(state, "margin_mode", None)

        state.position_qty = 0.0
        state.entry_price = None
        state.side = None
        self._clear_active_trade_id(state)
        self._reset_margin_state(state)

        eq_after = self.mark_equity(state, mt, close)

        fill = Fill(
            timestamp=ts_iso,
            type="EXIT",
            side="short",
            price=float(buy_px),
            qty=float(exit_qty),
            fee=float(fee),
            equity_after=float(eq_after),
            entry_price=float(entry_px),
            exit_price=float(buy_px),
            trade_id=exit_trade_id,
            pnl=float(net_pnl),
            exit_reason=exit_reason,
            mark_price=mark_price,
            liquidation_price=liquidation_price,
            maintenance_margin=maintenance_margin,
            margin_balance=margin_balance,
            margin_ratio=margin_ratio,
            liquidation_fee=0.0,
            margin_mode=saved_margin_mode,
        )
        return state, fill

    def liquidate(
        self,
        ts_iso: str,
        state: PortfolioState,
        close: float,
        market_type: str,
        trade_id: int,
        *,
        exec_price: float | None = None,
        mark_price: float | None = None,
        margin_snapshot: MarginSnapshot | None = None,
    ) -> Tuple[PortfolioState, Optional[Fill]]:
        mt = (market_type or "spot").lower()

        if state.position_qty == 0.0:
            return state, None

        liquidation_trade_id = self._get_active_trade_id(state, trade_id)
        position_side = str(state.side or "long").lower()

        if exec_price is not None:
            exit_px = float(exec_price)
        else:
            exit_px = self.apply_slippage(close, is_buy=(position_side == "short"))

        if exit_px <= 0 or not math.isfinite(exit_px):
            state.position_qty = 0.0
            state.entry_price = None
            state.side = None
            self._clear_active_trade_id(state)
            self._reset_margin_state(state)
            return state, None

        exit_qty = float(state.position_qty)
        entry_px = float(state.entry_price) if state.entry_price is not None else float(exit_px)
        entry_fee = self._estimate_entry_fee(entry_px, exit_qty, mt)
        notional = abs(exit_qty) * exit_px
        fee = notional * self.fee_rate
        liquidation_fee = notional * self.liquidation_fee_rate

        if mt == "spot":
            gross_pnl = exit_qty * (exit_px - entry_px)
            net_pnl = gross_pnl - entry_fee - fee
            state.cash = float(state.cash) + float(notional - fee)
            liquidation_fee = 0.0
        else:
            if position_side == "short":
                gross_pnl = exit_qty * (entry_px - exit_px)
            else:
                gross_pnl = exit_qty * (exit_px - entry_px)

            net_pnl = gross_pnl - entry_fee - fee - liquidation_fee
            state.cash = float(state.cash) + gross_pnl - fee - liquidation_fee
            state.liquidation_fee_paid = float(getattr(state, "liquidation_fee_paid", 0.0)) + float(liquidation_fee)
            self._add_realized_pnl(state, net_pnl)

        maintenance_margin = None
        margin_balance = None
        margin_ratio = None
        liquidation_price = None
        mark_px = mark_price
        saved_margin_mode = getattr(state, "margin_mode", None)

        if margin_snapshot is not None:
            maintenance_margin = float(margin_snapshot.maintenance_margin)
            margin_balance = float(margin_snapshot.margin_balance)
            margin_ratio = margin_snapshot.margin_ratio
            liquidation_price = margin_snapshot.liquidation_price
            if mark_px is None:
                mark_px = float(margin_snapshot.mark_price)

        state.position_qty = 0.0
        state.entry_price = None
        state.side = None
        self._clear_active_trade_id(state)
        self._reset_margin_state(state)

        eq_after = self.mark_equity(state, mt, close)

        fill = Fill(
            timestamp=ts_iso,
            type="LIQUIDATION",
            side=position_side,
            price=float(exit_px),
            qty=float(exit_qty),
            fee=float(fee),
            equity_after=float(eq_after),
            entry_price=float(entry_px),
            exit_price=float(exit_px),
            trade_id=liquidation_trade_id,
            pnl=float(net_pnl),
            exit_reason="liquidation",
            mark_price=mark_px,
            liquidation_price=liquidation_price,
            maintenance_margin=maintenance_margin,
            margin_balance=margin_balance,
            margin_ratio=margin_ratio,
            liquidation_fee=float(liquidation_fee),
            margin_mode=saved_margin_mode,
        )
        return state, fill