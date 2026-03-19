import math
from typing import Optional, Tuple

from core.execution_models import PortfolioState, Fill


class ExecutionEngine:
    """
    Reusable execution/fill engine for:
    - backtests (bar-by-bar simulation)
    - paper trading (same simulation on live bars)
    - live trading (later: swap to real broker, reuse sizing/risk parts)

    IMPORTANT:
    This implementation uses:
    - Spot: simple cash + position model
    - Futures: linear USDT-margined model
        equity = wallet_cash + qty * (mark_price - entry_price)
      (No 'borrowed'/'margin loan' model; that was the source of exploding equity)
    """

    def __init__(
        self,
        fee_rate: float = 0.001,
        slippage_bps: float = 2.0,
        max_leverage: float = 50.0,
        max_qty: float = 10.0,
        maintenance_margin: float = 0.005,
    ):
        self.fee_rate = float(fee_rate)
        self.slippage_bps = float(slippage_bps)
        self.max_leverage = float(max_leverage)
        self.max_qty = float(max_qty)
        self.maintenance_margin = float(maintenance_margin)

    # ---------- Pricing helpers ----------

    def apply_slippage(self, price: float, is_buy: bool) -> float:
        slip = self.slippage_bps / 10000.0
        return price * (1.0 + slip) if is_buy else price * (1.0 - slip)

    def mark_equity(self, state: PortfolioState, market_type: str, market_price: float) -> float:
        mt = (market_type or "spot").lower()

        if mt == "spot":
            return float(state.cash + state.position_qty * market_price)

        # Futures (linear USDT-margined):
        # equity = wallet + unrealized PnL
        if state.position_qty == 0.0 or state.entry_price is None:
            return float(state.cash)

        return float(state.cash + state.position_qty * (market_price - state.entry_price))

    # ---------- Risk / liquidation ----------

    def compute_liquidation_price(self, state: PortfolioState, market_type: str, market_price: float, leverage: float) -> bool:
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

        # Simple guard:
        # liquidate when equity <= maintenance_margin * notional
        return eq <= (self.maintenance_margin * notional)

    # ---------- Actions (Long only; shorts can be added similarly) ----------

    def enter_long(
        self,
        ts_iso: str,
        state: PortfolioState,
        close: float,
        market_type: str,
        leverage: float,
        trade_id: int,
    ) -> Tuple[PortfolioState, Optional[Fill]]:
        mt = (market_type or "spot").lower()
        buy_px = self.apply_slippage(close, is_buy=True)

        # PRICE SAFETY
        if buy_px <= 0 or not math.isfinite(buy_px):
            return state, None

        if mt == "spot":
            # Spend all cash
            notional = float(state.cash)
            fee = notional * self.fee_rate
            notional_after_fee = max(0.0, notional - fee)

            qty = notional_after_fee / buy_px
            # QTY SAFETY
            if (not math.isfinite(qty)) or abs(qty) > self.max_qty:
                return state, None

            state.position_qty = float(qty)
            state.cash = 0.0
            state.entry_price = float(buy_px)
            state.side = "long"

        else:
            # Futures (linear):
            # Notional exposure = wallet_cash * leverage
            lev = min(max(float(leverage), 1.0), self.max_leverage)

            notional = float(state.cash) * lev
            fee = notional * self.fee_rate

            qty = notional / buy_px
            # QTY SAFETY
            if (not math.isfinite(qty)) or abs(qty) > self.max_qty:
                return state, None

            # Pay fee from wallet
            state.cash = max(0.0, float(state.cash) - fee)

            state.position_qty = float(qty)
            state.entry_price = float(buy_px)
            state.side = "long"

        eq_after = self.mark_equity(state, mt, close)
        fill = Fill(
            timestamp=ts_iso,
            type="ENTRY",
            side="long",
            price=float(buy_px),
            qty=float(state.position_qty),
            fee=float(fee),
            equity_after=float(eq_after),
            trade_id=int(trade_id),
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

        sell_px = self.apply_slippage(close, is_buy=False)

        # PRICE SAFETY
        if sell_px <= 0 or not math.isfinite(sell_px):
            return state, None

        exit_qty = float(state.position_qty)
        entry_px = float(state.entry_price) if state.entry_price is not None else float(sell_px)

        if mt == "spot":
            notional = abs(exit_qty) * sell_px
            fee = notional * self.fee_rate
            state.cash = float(notional - fee)

        else:
            # Futures (linear): realize PnL into wallet, then pay exit fee
            pnl = float(exit_qty) * (float(sell_px) - entry_px)
            state.cash = float(state.cash) + pnl

            notional = abs(exit_qty) * sell_px
            fee = notional * self.fee_rate
            state.cash = float(state.cash) - float(fee)

        # reset position before computing ending equity
        state.position_qty = 0.0
        state.entry_price = None
        state.side = None

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
            trade_id=int(trade_id),
            pnl=None,  # caller (BacktestService) sets realized pnl vs baseline if desired
        )

        return state, fill

    def liquidate(
        self,
        ts_iso: str,
        state: PortfolioState,
        close: float,
        market_type: str,
        trade_id: int,
    ) -> Tuple[PortfolioState, Optional[Fill]]:
        mt = (market_type or "spot").lower()
        if state.position_qty == 0.0:
            return state, None

        # For long liquidation, we SELL to close
        exit_px = self.apply_slippage(close, is_buy=False)

        # PRICE SAFETY
        if exit_px <= 0 or not math.isfinite(exit_px):
            # emergency reset
            state.position_qty = 0.0
            state.entry_price = None
            state.side = None
            return state, None

        exit_qty = float(state.position_qty)
        entry_px = float(state.entry_price) if state.entry_price is not None else float(exit_px)

        notional = abs(exit_qty) * exit_px
        fee = notional * self.fee_rate

        if mt == "spot":
            # Spot close position into cash
            state.cash = float(state.cash + state.position_qty * exit_px - fee)
        else:
            # Futures linear: realize pnl and pay fee
            pnl = float(exit_qty) * (float(exit_px) - entry_px)
            state.cash = float(state.cash) + pnl - float(fee)

        # reset
        state.position_qty = 0.0
        state.entry_price = None
        state.side = None

        eq_after = self.mark_equity(state, mt, close)

        fill = Fill(
            timestamp=ts_iso,
            type="LIQUIDATION",
            side=state.side or "long",
            price=float(exit_px),
            qty=float(exit_qty),
            fee=float(fee),
            equity_after=float(eq_after),
            entry_price=float(entry_px),
            exit_price=float(exit_px),
            trade_id=int(trade_id),
            pnl=None,
        )

        return state, fill
    
    def enter_short(self, *args, **kwargs):
        raise NotImplementedError("Short trading is not implemented yet.")

    def exit_short(self, *args, **kwargs):
        raise NotImplementedError("Short trading is not implemented yet.")
