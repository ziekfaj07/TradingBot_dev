from dataclasses import dataclass
from typing import Optional


@dataclass
class PortfolioState:
    cash: float
    pos_qty: float = 0.0           # +long, -short
    entry_price: Optional[float] = None
    side: Optional[str] = None     # "long" or "short"

    # futures bookkeeping
    margin: float = 0.0
    borrowed: float = 0.0


@dataclass
class Fill:
    timestamp: str
    type: str                      # ENTRY / EXIT / LIQUIDATION
    side: str                      # long / short
    price: float
    qty: float
    fee: float
    equity_after: float
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    trade_id: Optional[int] = None
    pnl: Optional[float] = None