from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class PortfolioState:
    cash: float
    position_qty: float = 0.0
    entry_price: Optional[float] = None
    side: Optional[str] = None
    equity: float = 0.0
    liquidation_price: Optional[float] = None
    realized_pnl: float = 0.0
    active_trade_id: Optional[int] = None
    margin: float = 0.0
    borrowed: float = 0.0
    margin_mode: str = "cross"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None):
        if not data:
            return None
        return cls(
            cash=float(data.get("cash", 0.0)),
            position_qty=float(data.get("position_qty", 0.0)),
            entry_price=data.get("entry_price"),
            side=data.get("side"),
            equity=float(data.get("equity", 0.0)),
            liquidation_price=data.get("liquidation_price"),
            realized_pnl=float(data.get("realized_pnl", 0.0)),
            active_trade_id=data.get("active_trade_id"),
            margin=float(data.get("margin", 0.0)),
            borrowed=float(data.get("borrowed", 0.0)),
            margin_mode=str(data.get("margin_mode", "cross") or "cross"),
        )


@dataclass
class Fill:
    timestamp: str
    type: str
    side: str
    price: float
    qty: float
    fee: float
    equity_after: float
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    trade_id: Optional[int] = None
    pnl: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None):
        if not data:
            return None
        return cls(
            timestamp=str(data.get("timestamp", "")),
            type=str(data.get("type", "")),
            side=str(data.get("side", "")),
            price=float(data.get("price", 0.0)),
            qty=float(data.get("qty", 0.0)),
            fee=float(data.get("fee", 0.0)),
            equity_after=float(data.get("equity_after", 0.0)),
            entry_price=data.get("entry_price"),
            exit_price=data.get("exit_price"),
            trade_id=data.get("trade_id"),
            pnl=data.get("pnl"),
        )