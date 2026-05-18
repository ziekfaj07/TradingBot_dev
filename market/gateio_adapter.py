from __future__ import annotations

import os
from typing import Any

import ccxt

from core.interfaces import ExchangeCredentials
from core.market_types import is_derivatives_market, normalize_market_type, to_exchange_market_type
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
        base_url: str | None = None,
        enable_rate_limit: bool = True,
        timeout_ms: int = 15000,
    ) -> None:
        self.public_market_type = normalize_market_type(market_type)
        self.market_type = self._normalize_market_type(self.public_market_type)
        self.testnet = bool(testnet)
        self.base_url = (base_url or "").strip().rstrip("/") or None

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

        if self.testnet and hasattr(self.client, "set_sandbox_mode"):
            try:
                self.client.set_sandbox_mode(True)
            except Exception:
                self._apply_testnet_urls()
        elif self.testnet:
            self._apply_testnet_urls()

        if self.base_url:
            self._apply_base_url(self.base_url)

    def _normalize_market_type(self, market_type: str) -> str:
        public_type = normalize_market_type(market_type)
        return to_exchange_market_type(self.exchange_id, public_type)

    def _apply_base_url(self, base_url: str) -> None:
        base = str(base_url or "").strip().rstrip("/")
        if not base:
            return
        urls = dict(getattr(self.client, "urls", {}) or {})
        urls["api"] = self._rewrite_api_url_tree(urls.get("api"), base)
        self.client.urls = urls

    def _apply_testnet_urls(self) -> None:
        urls = dict(getattr(self.client, "urls", {}) or {})
        urls["api"] = self._rewrite_api_url_tree(
            urls.get("api"),
            "https://api-testnet.gateapi.io/api/v4",
        )
        self.client.urls = urls

    def _rewrite_api_url_tree(self, node: Any, base_url: str) -> Any:
        if isinstance(node, dict):
            return {
                key: self._rewrite_api_url_tree(value, base_url)
                for key, value in node.items()
            }
        return str(base_url or "").strip().rstrip("/")

    def normalize_symbol(self, symbol: str) -> str:
        raw_input = str(symbol or "").strip().upper()
        if "/" in raw_input and ":" in raw_input:
            return raw_input

        raw = raw_input.replace("/", "").replace("-", "").replace("_", "")
        if not raw:
            raise ExchangeConfigurationError("Symbol is required.")
        if raw.endswith("USDT"):
            base = raw[:-4]
            quote = "USDT"
        elif raw.endswith("USD"):
            base = raw[:-3]
            quote = "USD"
        elif "/" in raw_input:
            base, quote = raw_input.split("/", 1)
            quote = quote.split(":", 1)[0]
        else:
            raise ExchangeConfigurationError(
                f"Unsupported Gate.io symbol format: {symbol!r}. Expected like BTCUSDT or BTC/USDT."
            )

        pair = f"{base}/{quote}"
        if is_derivatives_market(self.public_market_type) and quote in {"USDT", "USD", "USDC"}:
            return f"{pair}:{quote}"
        return pair

    def _require_credentials(self) -> None:
        if not self.credentials.api_key or not self.credentials.api_secret:
            raise ExchangeConfigurationError(
                "Missing Gate.io API credentials. Set the configured API key and secret env vars first."
            )

    def describe(self) -> dict[str, Any]:
        return {
            "exchange_id": self.exchange_id,
            "market_type": self.public_market_type,
            "exchange_market_type": self.market_type,
            "testnet": self.testnet,
            "capabilities": self.capabilities().to_dict(),
        }

    def capabilities(self) -> ExchangeCapabilities:
        has = getattr(self.client, "has", {}) or {}
        return ExchangeCapabilities(
            exchange_id=self.exchange_id,
            name="Gate.io",
            sandbox_supported=True,
            default_type=self.public_market_type,
            supported_market_types=["spot", "futures"],
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
                "market_type": self.public_market_type,
                "exchange_market_type": self.market_type,
                "testnet": self.testnet,
                "server_time": server_time,
                "market_count": len(markets),
            }
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

    def validate_symbol(self, symbol: str, market_type: str = "spot") -> dict[str, Any]:
        public_market_type = normalize_market_type(market_type)
        normalized_market_type = self._normalize_market_type(public_market_type)
        if normalized_market_type != self.market_type:
            self.client.options["defaultType"] = normalized_market_type
            self.market_type = normalized_market_type
            self.public_market_type = public_market_type

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
            "market_type": self.public_market_type,
            "exchange_market_type": self.market_type,
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

    def set_margin_mode(
        self,
        *,
        margin_mode: str,
        symbol: str,
        leverage: float | None = None,
    ) -> dict[str, Any]:
        self._require_credentials()
        normalized_symbol = self.normalize_symbol(symbol)
        normalized_margin_mode = str(margin_mode or "").strip().lower()
        if not normalized_margin_mode:
            normalized_margin_mode = "cross"

        if getattr(self.client, "has", {}).get("setMarginMode") is False:
            if normalized_margin_mode in {"cross", "cross_margin"}:
                return {
                    "symbol": normalized_symbol,
                    "margin_mode": "cross",
                    "source": "set_margin_mode_skipped",
                    "skipped": True,
                    "reason": "gateio_ccxt_set_margin_mode_unsupported",
                }
            raise ExchangeConfigurationError(
                "Gate.io setMarginMode() is not supported by the current CCXT adapter. "
                "Use margin_mode='cross' or configure isolated margin directly on the exchange before starting."
            )

        params: dict[str, Any] = {}
        if leverage is not None:
            params["leverage"] = float(leverage)
        try:
            result = self.client.set_margin_mode(normalized_margin_mode, normalized_symbol, params)
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc
        payload = dict(result or {})
        payload.setdefault("symbol", normalized_symbol)
        payload.setdefault("margin_mode", normalized_margin_mode)
        payload.setdefault("source", "set_margin_mode")
        return payload

    def set_leverage(
        self,
        *,
        leverage: float,
        symbol: str,
        margin_mode: str | None = None,
    ) -> dict[str, Any]:
        self._require_credentials()
        normalized_symbol = self.normalize_symbol(symbol)
        params: dict[str, Any] = {}
        if margin_mode:
            params["marginMode"] = str(margin_mode).lower()
            params["margin_mode"] = str(margin_mode).lower()
        try:
            result = self.client.set_leverage(float(leverage), normalized_symbol, params)
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc
        payload = dict(result or {})
        payload.setdefault("symbol", normalized_symbol)
        payload.setdefault("leverage", float(leverage))
        payload.setdefault("margin_mode", str(margin_mode).lower() if margin_mode else None)
        payload.setdefault("source", "set_leverage")
        return payload

    def fetch_effective_leverage(
        self,
        *,
        symbol: str,
        margin_mode: str | None = None,
    ) -> dict[str, Any]:
        self._require_credentials()
        normalized_symbol = self.normalize_symbol(symbol)
        try:
            positions = self.client.fetch_positions([normalized_symbol])
            rows = list(positions or [])
            if rows:
                payload = dict(rows[0] or {})
                payload.setdefault("source", "fetch_positions")
                return payload
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

        try:
            payload = dict(self.client.fetch_position(normalized_symbol) or {})
            payload.setdefault("source", "fetch_position")
            return payload
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

    def fetch_order(
        self,
        *,
        order_id: str,
        symbol: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_credentials()
        normalized_symbol = self.normalize_symbol(symbol)
        try:
            return self.client.fetch_order(str(order_id), normalized_symbol, params or {})
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

    def contract_size_for_symbol(self, symbol: str) -> float | None:
        normalized_symbol = self.normalize_symbol(symbol)
        try:
            markets = self.client.load_markets()
            market = markets.get(normalized_symbol)
            if not market:
                return None
            for candidate in (
                market.get("contractSize"),
                market.get("contract_size"),
                (market.get("info") or {}).get("quanto_multiplier")
                if isinstance(market.get("info"), dict)
                else None,
                (market.get("info") or {}).get("contract_size")
                if isinstance(market.get("info"), dict)
                else None,
            ):
                if candidate is None or candidate == "":
                    continue
                value = float(candidate)
                if value > 0.0:
                    return value
        except Exception:
            return None
        return None

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
