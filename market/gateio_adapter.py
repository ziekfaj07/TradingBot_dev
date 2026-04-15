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
        settle_currency: str = "USDT",
        enable_rate_limit: bool = True,
        timeout_ms: int = 15000,
    ) -> None:
        self.market_type = self._normalize_market_type(market_type)
        self.testnet = bool(testnet)
        self.settle_currency = str(settle_currency or "USDT").strip().upper() or "USDT"
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
            self._enable_testnet_mode()

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

    def _enable_testnet_mode(self) -> None:
        if self.market_type == "spot":
            raise ExchangeConfigurationError(
                "Gate.io testnet/demo is not supported for spot in this adapter. "
                "Use market_type='swap' for Gate testnet, or set exchange_testnet=false for live spot."
            )

        try:
            self.client.set_sandbox_mode(True)
        except Exception as exc:
            raise ExchangeConfigurationError(
                f"Failed to enable Gate.io testnet/sandbox mode: {exc}"
            ) from exc

    def normalize_symbol(self, symbol: str) -> str:
        raw = str(symbol or "").strip().upper().replace("-", "")
        if not raw:
            raise ExchangeConfigurationError("Symbol is required.")

        if "/" in raw:
            if self.market_type == "swap":
                if ":" in raw:
                    return raw
                return f"{raw}:{self.settle_currency}"
            return raw.split(":", 1)[0]

        quote = None
        base = None
        if raw.endswith("USDT"):
            base = raw[:-4]
            quote = "USDT"
        elif raw.endswith("USD"):
            base = raw[:-3]
            quote = "USD"

        if not base or not quote:
            raise ExchangeConfigurationError(
                f"Unsupported Gate.io symbol format: {symbol!r}. Expected like BTCUSDT or BTC/USDT."
            )

        normalized = f"{base}/{quote}"
        if self.market_type == "swap":
            return f"{normalized}:{self.settle_currency}"
        return normalized

    def _require_credentials(self) -> None:
        if not self.credentials.api_key or not self.credentials.api_secret:
            raise ExchangeConfigurationError(
                "Missing Gate.io API credentials. Set the configured API key and secret env vars first."
            )

    def _api_environment_label(self) -> str:
        if self.testnet:
            return f"gateio-{self.market_type}-testnet"
        return f"gateio-{self.market_type}-live"

    def describe(self) -> dict[str, Any]:
        return {
            "exchange_id": self.exchange_id,
            "market_type": self.market_type,
            "testnet": self.testnet,
            "environment": self._api_environment_label(),
            "settle_currency": self.settle_currency,
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
            has_fetch_order=bool(has.get("fetchOrder")),
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
            "environment": self._api_environment_label(),
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
                "environment": self._api_environment_label(),
                "settle_currency": self.settle_currency,
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
            if self.testnet and normalized_market_type == "spot":
                raise ExchangeConfigurationError(
                    "Gate.io testnet/demo is not supported for spot in this adapter. "
                    "Use market_type='swap' for Gate testnet, or set exchange_testnet=false for live spot."
                )
            self.client.options["defaultType"] = normalized_market_type
            self.market_type = normalized_market_type

        normalized_symbol = self.normalize_symbol(symbol)
        try:
            markets = self.client.load_markets()
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

        market = markets.get(normalized_symbol)
        return {
            "ok": market is not None,
            "input_symbol": symbol,
            "normalized_symbol": normalized_symbol,
            "market_type": self.market_type,
            "exchange": self.exchange_id,
            "testnet": self.testnet,
            "environment": self._api_environment_label(),
            "settle_currency": self.settle_currency,
            "active": bool(market.get("active")) if market else False,
            "limits": dict(market.get("limits", {}) or {}) if market else None,
            "precision": dict(market.get("precision", {}) or {}) if market else None,
            "raw_market_id": market.get("id") if market else None,
        }

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        normalized_symbol = self.normalize_symbol(symbol)
        safe_limit = max(10, min(int(limit), 1000))

        try:
            rows = self.client.fetch_ohlcv(
                normalized_symbol,
                timeframe=timeframe,
                limit=safe_limit,
            )
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

        bars: list[dict[str, Any]] = []
        for row in rows or []:
            try:
                ts_ms = int(row[0])
                bars.append(
                    {
                        "timestamp": int(ts_ms // 1000),
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[5]),
                        "source": self._api_environment_label(),
                    }
                )
            except (TypeError, ValueError, IndexError):
                continue

        bars.sort(key=lambda x: x["timestamp"])
        return bars

    def fetch_balance(self) -> dict[str, Any]:
        self._require_credentials()
        try:
            return self.client.fetch_balance()
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc


    def market_info(self, symbol: str) -> dict[str, Any]:
        normalized_symbol = self.normalize_symbol(symbol)
        try:
            markets = self.client.load_markets()
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

        market = markets.get(normalized_symbol)
        if not isinstance(market, dict):
            raise ExchangeConfigurationError(f"Market metadata not found for symbol: {normalized_symbol}")
        return market

    def contract_size_for_symbol(self, symbol: str) -> float | None:
        if self.market_type != "swap":
            return None
        market = self.market_info(symbol)
        raw = market.get("info") if isinstance(market.get("info"), dict) else {}
        for candidate in (
            market.get("contractSize"),
            market.get("contract_size"),
            raw.get("quanto_multiplier"),
            raw.get("contract_size"),
            raw.get("order_size_min"),
        ):
            try:
                if candidate is None or candidate == "":
                    continue
                value = float(candidate)
                if value > 0:
                    return value
            except (TypeError, ValueError):
                continue
        return None

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
            return list(self.client.fetch_open_orders(normalized_symbol) or [])
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

    def fetch_order(self, *, order_id: str, symbol: str) -> dict[str, Any]:
        self._require_credentials()
        normalized_symbol = self.normalize_symbol(symbol)
        try:
            return self.client.fetch_order(str(order_id), normalized_symbol)
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

    def fetch_my_trades(
        self,
        symbol: str | None = None,
        since: int | None = None,
        limit: int = 100,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self._require_credentials()
        normalized_symbol = self.normalize_symbol(symbol) if symbol else None
        try:
            rows = self.client.fetch_my_trades(
                normalized_symbol,
                since=None if since is None else int(since),
                limit=max(1, min(int(limit), 200)),
                params=params or {},
            )
            return list(rows or [])
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

    def get_position_mode(self) -> str | None:
        if self.market_type != "swap":
            return None
        balance = self.fetch_balance()
        raw = dict(balance.get("raw") or {})
        info = raw.get("info")
        if isinstance(info, list) and info:
            first = info[0] if isinstance(info[0], dict) else {}
            mode = first.get("position_mode") or first.get("mode")
            if mode is None:
                return None
            mode_text = str(mode).strip().lower()
            if mode_text in {"single", "oneway", "one_way"}:
                return "single"
            if mode_text in {"dual", "hedge", "both"}:
                return "dual"
            return mode_text
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