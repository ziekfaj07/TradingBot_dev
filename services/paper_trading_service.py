from __future__ import annotations

import time

from core.execution_models import PortfolioState
from core.market_types import is_derivatives_market, normalize_market_type
from services.execution_engine import ExecutionEngine


class PaperTradingService:
    def __init__(
        self,
        *,
        starting_balance: float = 10000,
        market_type: str = "spot",
        leverage: float = 1.0,
        taker_fee: float = 0.0004,
        allow_short: bool = False,
    ):
        self.market_type = normalize_market_type(market_type)
        self.leverage = float(leverage or 1.0)
        self.allow_short = bool(allow_short)

        self.state = PortfolioState(
            cash=float(starting_balance),
            position_qty=0.0,
            entry_price=None,
            side=None,
            equity=float(starting_balance),
            realized_pnl=0.0,
            margin_mode="cross",
        )
        self.engine = ExecutionEngine(fee_rate=float(taker_fee))
        self.trade_id = 0

    def update(self, price: float, signal: int):
        px = float(price)
        sig = int(signal or 0)
        ts = str(int(time.time()))
        fill = None

        if self.engine.compute_liquidation_price(self.state, self.market_type, px, self.leverage):
            self.state, fill = self.engine.liquidate(ts, self.state, px, self.market_type, self.trade_id)

        if fill is None:
            qty = float(self.state.position_qty or 0.0)
            if sig == 1:
                if qty < 0.0:
                    self.state, fill = self.engine.exit_short(ts, self.state, px, self.market_type, self.trade_id)
                elif qty == 0.0:
                    self.trade_id += 1
                    self.state, fill = self.engine.enter_long(ts, self.state, px, self.market_type, self.leverage, self.trade_id)
            elif sig == -1:
                if qty > 0.0:
                    self.state, fill = self.engine.exit_long(ts, self.state, px, self.market_type, self.trade_id)
                elif qty == 0.0 and self.allow_short and is_derivatives_market(self.market_type):
                    self.trade_id += 1
                    self.state, fill = self.engine.enter_short(ts, self.state, px, self.market_type, self.leverage, self.trade_id)

        equity = self.engine.mark_equity(self.state, self.market_type, px)
        self.state.equity = equity
        out = {
            "balance_usd": round(float(self.state.cash), 2),
            "position_qty": round(float(self.state.position_qty), 6),
            "entry_price": round(float(self.state.entry_price or 0.0), 2),
            "side": self.state.side,
            "total_equity": round(float(equity), 2),
        }
        if fill is not None:
            out["fill"] = fill.to_dict()
        return out
