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

    def fetch_order(
        self,
        *,
        order_id: str,
        symbol: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def fetch_my_trades(
        self,
        symbol: str,
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...

    def get_position_mode(self) -> str | None: ...

    def contract_size_for_symbol(self, symbol: str) -> float | None: ...

    def set_margin_mode(
        self,
        *,
        margin_mode: str,
        symbol: str,
        leverage: float | None = None,
    ) -> dict[str, Any]: ...

    def set_leverage(
        self,
        *,
        leverage: float,
        symbol: str,
        margin_mode: str | None = None,
    ) -> dict[str, Any]: ...

    def fetch_effective_leverage(
        self,
        *,
        symbol: str,
        margin_mode: str | None = None,
    ) -> dict[str, Any]: ...

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
