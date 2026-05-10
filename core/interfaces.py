from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class ExchangeCredentials:
    api_key: str
    api_secret: str
    api_passphrase: str | None = None


class ExchangeAdapter(Protocol):
    exchange_id: str

    def normalize_symbol(self, symbol: str) -> str: ...

    def describe(self) -> dict[str, Any]: ...

    def health_check(self) -> dict[str, Any]: ...

    def load_markets(self, reload: bool = False) -> dict[str, Any]: ...

    def validate_symbol(self, symbol: str, market_type: str = "spot") -> dict[str, Any]: ...

    def fetch_balance(self) -> dict[str, Any]: ...

    def fetch_ticker(self, symbol: str) -> dict[str, Any]: ...

    def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]: ...

    def fetch_positions(self, symbol: str | None = None) -> list[dict[str, Any]]: ...

    def create_order(
        self,
        *,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def cancel_order(
        self,
        *,
        order_id: str,
        symbol: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...
