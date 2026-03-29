# services/paper_trading_service.py

from core.execution_models import PortfolioState
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
        self.market_type = market_type
        self.leverage = leverage
        self.allow_short = allow_short

        self.state = PortfolioState(
            cash=starting_balance,
            position_qty=0.0,
            entry_price=0.0,
            equity=starting_balance,
        )

        self.engine = ExecutionEngine(
            fee_rate=taker_fee,
        )

        self.trade_id = 0

    def update(self, price: float, signal: int):
        """
        signal:
            1  = enter long
            -1 = exit long
            0  = hold
        """

        ts = "LIVE"  # You can replace with actual timestamp

        # liquidation check (futures only)
        if self.engine.compute_liquidation_price(
            self.state,
            self.market_type,
            price,
            self.leverage,
        ):
            self.state, fill = self.engine.liquidate(
                ts,
                self.state,
                price,
                self.market_type,
                self.trade_id,
            )
            if fill:
                self.trade_id += 1

        # ENTRY
        if signal == 1 and self.state.position_qty == 0:
            self.trade_id += 1
            self.state, fill = self.engine.enter_long(
                ts,
                self.state,
                price,
                self.market_type,
                self.leverage,
                self.trade_id,
            )

        # EXIT
        elif signal == -1 and self.state.position_qty > 0:
            self.state, fill = self.engine.exit_long(
                ts,
                self.state,
                price,
                self.market_type,
                self.trade_id,
            )

        # mark equity every tick
        equity = self.engine.mark_equity(self.state, self.market_type, price)

        return {
            "balance_usd": round(self.state.cash, 2),
            "position_qty": round(self.state.position_qty, 6),
            "entry_price": round(self.state.entry_price or 0.0, 2),
            "total_equity": round(equity, 2),
        }