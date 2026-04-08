from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class ExchangeCapabilities:
    exchange_id: str
    name: str
    sandbox_supported: bool
    default_type: str
    supported_market_types: list[str]
    has_fetch_balance: bool
    has_fetch_ticker: bool
    has_fetch_open_orders: bool
    has_fetch_order: bool
    has_fetch_positions: bool
    has_create_order: bool
    has_cancel_order: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LiveConfigValidation:
    ok: bool
    exchange_name: str
    market_type: str
    symbol: str
    credentials_present: bool
    dry_run_live: bool
    enable_live_trading: bool
    errors: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
