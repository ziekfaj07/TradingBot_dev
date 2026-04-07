from __future__ import annotations

import os
from typing import Any

import ccxt

from core.interfaces import ExchangeCredentials
from market.exceptions import (
    ExchangeAuthError,
    ExchangeConfigurationError,
    ExchangeConnectionError,
)
from market.models import ExchangeCapabilities


class GateIOAdapter:
    exchange_id = "gateio"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_passphrase: str | None = None,
        market_type: str = "spot",
        testnet: bool = False,
        enable_rate_limit: bool = True,
        timeout_ms: int = 15000,
    ) -> None:
        self.market_type = self._normalize_market_type(market_type)
        self.testnet = bool(testnet)

        self.credentials = ExchangeCredentials(
            api_key=(api_key or "").strip(),
            api_secret=(api_secret or "").strip(),
            api_passphrase=(api_passphrase or "").strip() or None,
        )

        self.client = ccxt.gateio(
            {
                "apiKey": self.credentials.api_key,
                "secret": self.credentials.api_secret,
                "password": self.credentials.api_passphrase,
                "enableRateLimit": enable_rate_limit,
                "timeout": int(timeout_ms),
                "options": {
                    "defaultType": self.market_type,
                    "createMarketBuyOrderRequiresPrice": False,
                },
            }
        )

        if self.testnet:
            self._apply_testnet_urls()

    def _normalize_market_type(self, market_type: str) -> str:
        raw = str(market_type or "spot").strip().lower()
        aliases = {
            "spot": "spot",
            "cash": "spot",
            "swap": "swap",
            "future": "swap",
            "futures": "swap",
            "perp": "swap",
            "perpetual": "swap",
        }
        normalized = aliases.get(raw)
        if normalized is None:
            raise ExchangeConfigurationError(
                f"Unsupported Gate.io market_type: {market_type!r}. Use 'spot' or 'swap'."
            )
        return normalized

    def _apply_testnet_urls(self) -> None:
        urls = dict(getattr(self.client, "urls", {}) or {})
        api_urls = dict(urls.get("api", {}) or {})
        api_urls["public"] = "https://api-testnet.gateapi.io/api/v4"
        api_urls["private"] = "https://api-testnet.gateapi.io/api/v4"
        urls["api"] = api_urls
        self.client.urls = urls

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        raw = str(symbol or "").strip().upper().replace("/", "").replace("-", "")
        if not raw:
            raise ExchangeConfigurationError("Symbol is required.")
        if raw.endswith("USDT"):
            return f"{raw[:-4]}/USDT"
        if raw.endswith("USD"):
            return f"{raw[:-3]}/USD"
        if "/" in str(symbol):
            return str(symbol).strip().upper()
        raise ExchangeConfigurationError(
            f"Unsupported Gate.io symbol format: {symbol!r}. Expected like BTCUSDT or BTC/USDT."
        )

    def _require_credentials(self) -> None:
        if not self.credentials.api_key or not self.credentials.api_secret:
            raise ExchangeConfigurationError(
                "Missing Gate.io API credentials. Set the configured API key and secret env vars first."
            )

    def describe(self) -> dict[str, Any]:
        return {
            "exchange_id": self.exchange_id,
            "market_type": self.market_type,
            "testnet": self.testnet,
            "capabilities": self.capabilities().to_dict(),
        }

    def capabilities(self) -> ExchangeCapabilities:
        has = getattr(self.client, "has", {}) or {}
        return ExchangeCapabilities(
            exchange_id=self.exchange_id,
            name="Gate.io",
            sandbox_supported=True,
            default_type=self.market_type,
            supported_market_types=["spot", "swap"],
            has_fetch_balance=bool(has.get("fetchBalance")),
            has_fetch_ticker=bool(has.get("fetchTicker")),
            has_fetch_open_orders=bool(has.get("fetchOpenOrders")),
            has_fetch_positions=bool(has.get("fetchPositions")),
            has_create_order=bool(has.get("createOrder")),
            has_cancel_order=bool(has.get("cancelOrder")),
        )

    def load_markets(self, reload: bool = False) -> dict[str, Any]:
        try:
            markets = self.client.load_markets(reload=reload)
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

        return {
            "ok": True,
            "exchange": self.exchange_id,
            "market_count": len(markets),
        }

    def health_check(self) -> dict[str, Any]:
        try:
            server_time = self.client.fetch_time()
            markets = self.client.load_markets()
            return {
                "ok": True,
                "exchange": self.exchange_id,
                "market_type": self.market_type,
                "testnet": self.testnet,
                "server_time": server_time,
                "market_count": len(markets),
            }
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

    def validate_symbol(self, symbol: str, market_type: str = "spot") -> dict[str, Any]:
        normalized_market_type = self._normalize_market_type(market_type)
        if normalized_market_type != self.market_type:
            self.client.options["defaultType"] = normalized_market_type
            self.market_type = normalized_market_type

        normalized_symbol = self.normalize_symbol(symbol)

        try:
            markets = self.client.load_markets()
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

        market = markets.get(normalized_symbol)
        return {
            "ok": market is not None,
            "input_symbol": symbol,
            "normalized_symbol": normalized_symbol,
            "market_type": self.market_type,
            "exchange": self.exchange_id,
            "active": bool(getattr(market, "get", lambda *_: False)("active")) if market else False,
            "limits": dict(market.get("limits", {}) or {}) if market else None,
            "precision": dict(market.get("precision", {}) or {}) if market else None,
            "raw_market_id": market.get("id") if market else None,
        }

    def fetch_balance(self) -> dict[str, Any]:
        self._require_credentials()
        try:
            return self.client.fetch_balance()
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

    def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        normalized_symbol = self.normalize_symbol(symbol)
        try:
            return self.client.fetch_ticker(normalized_symbol)
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

    def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        self._require_credentials()
        normalized_symbol = self.normalize_symbol(symbol) if symbol else None
        try:
            return self.client.fetch_open_orders(normalized_symbol)
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

    def fetch_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        self._require_credentials()
        normalized_symbol = self.normalize_symbol(symbol) if symbol else None
        try:
            positions = self.client.fetch_positions([normalized_symbol] if normalized_symbol else None)
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc
        return list(positions or [])

    def create_order(
        self,
        *,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_credentials()
        normalized_symbol = self.normalize_symbol(symbol)
        try:
            return self.client.create_order(
                normalized_symbol,
                str(order_type).lower(),
                str(side).lower(),
                float(amount),
                None if price is None else float(price),
                params or {},
            )
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

    def cancel_order(
        self,
        *,
        order_id: str,
        symbol: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_credentials()
        normalized_symbol = self.normalize_symbol(symbol)
        try:
            return self.client.cancel_order(str(order_id), normalized_symbol, params or {})
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc


def gateio_credentials_from_env(
    *,
    api_key_env: str,
    api_secret_env: str,
    api_passphrase_env: str | None = None,
) -> ExchangeCredentials:
    api_key = os.getenv(str(api_key_env or "").strip(), "").strip()
    api_secret = os.getenv(str(api_secret_env or "").strip(), "").strip()
    api_passphrase = None
    if api_passphrase_env:
        api_passphrase = os.getenv(str(api_passphrase_env).strip(), "").strip() or None

    return ExchangeCredentials(
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase,
    )