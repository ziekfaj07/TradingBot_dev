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
    isolated_margin: float = 0.0
    margin_mode: str = "cross"
    open_fee_paid: float = 0.0

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
            isolated_margin=float(data.get("isolated_margin", 0.0)),
            margin_mode=str(data.get("margin_mode", "cross") or "cross"),
            open_fee_paid=float(data.get("open_fee_paid", 0.0)),
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

    exchange: Optional[str] = None
    symbol: Optional[str] = None
    market_type: Optional[str] = None
    order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    order_status: Optional[str] = None
    execution_source: Optional[str] = None
    reduce_only: Optional[bool] = None
    dry_run: Optional[bool] = None

    # v0.7.4 live execution telemetry
    expected_price: Optional[float] = None
    expected_qty: Optional[float] = None
    submitted_at: Optional[str] = None
    acknowledged_at: Optional[str] = None
    filled_at: Optional[str] = None
    submit_to_ack_ms: Optional[float] = None
    submit_to_fill_ms: Optional[float] = None
    price_slippage: Optional[float] = None
    price_slippage_bps: Optional[float] = None
    qty_delta: Optional[float] = None
    qty_delta_pct: Optional[float] = None
    fill_id: Optional[str] = None
    order_state: Optional[str] = None
    cumulative_qty: Optional[float] = None
    remaining_qty: Optional[float] = None
    contract_size: Optional[float] = None

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
            exchange=data.get("exchange"),
            symbol=data.get("symbol"),
            market_type=data.get("market_type"),
            order_id=data.get("order_id"),
            client_order_id=data.get("client_order_id"),
            order_status=data.get("order_status"),
            execution_source=data.get("execution_source"),
            reduce_only=data.get("reduce_only"),
            dry_run=data.get("dry_run"),
            expected_price=data.get("expected_price"),
            expected_qty=data.get("expected_qty"),
            submitted_at=data.get("submitted_at"),
            acknowledged_at=data.get("acknowledged_at"),
            filled_at=data.get("filled_at"),
            submit_to_ack_ms=data.get("submit_to_ack_ms"),
            submit_to_fill_ms=data.get("submit_to_fill_ms"),
            price_slippage=data.get("price_slippage"),
            price_slippage_bps=data.get("price_slippage_bps"),
            qty_delta=data.get("qty_delta"),
            qty_delta_pct=data.get("qty_delta_pct"),
            fill_id=data.get("fill_id"),
            order_state=data.get("order_state"),
            cumulative_qty=data.get("cumulative_qty"),
            remaining_qty=data.get("remaining_qty"),
            contract_size=data.get("contract_size"),
        )