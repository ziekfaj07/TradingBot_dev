from __future__ import annotations

from typing import Any

import ccxt

from core.interfaces import ExchangeCredentials
from core.market_types import is_derivatives_market, normalize_market_type, to_exchange_market_type
from market.exceptions import ExchangeAuthError, ExchangeConfigurationError, ExchangeConnectionError
from market.models import ExchangeCapabilities


class CCXTExchangeAdapter:
    """Generic CCXT adapter for future exchange onboarding.

    The app uses public `market_type` values (`spot`, `futures`) while the
    adapter translates to each exchange library's internal type. This keeps the
    UI stable and lets a new exchange be introduced by passing exchange_name,
    optional base_url, and credentials rather than hard-coding a new route.
    """

    def __init__(
        self,
        *,
        exchange_name: str,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_passphrase: str | None = None,
        market_type: str = "spot",
        testnet: bool = False,
        base_url: str | None = None,
        enable_rate_limit: bool = True,
        timeout_ms: int = 15000,
    ) -> None:
        self.exchange_id = self._normalize_exchange_name(exchange_name)
        self.public_market_type = normalize_market_type(market_type)
        self.market_type = to_exchange_market_type(self.exchange_id, self.public_market_type)
        self.testnet = bool(testnet)
        self.base_url = (base_url or "").strip() or None
        self.credentials = ExchangeCredentials(
            api_key=(api_key or "").strip(),
            api_secret=(api_secret or "").strip(),
            api_passphrase=(api_passphrase or "").strip() or None,
        )

        exchange_cls = getattr(ccxt, self.exchange_id, None)
        if exchange_cls is None:
            raise ExchangeConfigurationError(f"Unsupported CCXT exchange: {exchange_name!r}")

        config: dict[str, Any] = {
            "apiKey": self.credentials.api_key,
            "secret": self.credentials.api_secret,
            "password": self.credentials.api_passphrase,
            "enableRateLimit": enable_rate_limit,
            "timeout": int(timeout_ms),
            "options": {"defaultType": self.market_type},
        }
        self.client = exchange_cls(config)

        if self.base_url:
            self._apply_base_url(self.base_url)
        if self.testnet and hasattr(self.client, "set_sandbox_mode"):
            try:
                self.client.set_sandbox_mode(True)
            except Exception:
                # Some exchanges expose sandbox URLs manually or not at all.
                pass

    @staticmethod
    def _normalize_exchange_name(exchange_name: str | None) -> str:
        raw = str(exchange_name or "gateio").strip().lower().replace(".", "")
        aliases = {"gate": "gateio", "gateio": "gateio", "binanceusdm": "binanceusdm"}
        return aliases.get(raw, raw)

    def _apply_base_url(self, base_url: str) -> None:
        base = str(base_url or "").strip().rstrip("/")
        if not base:
            return
        urls = dict(getattr(self.client, "urls", {}) or {})
        api_urls = dict(urls.get("api", {}) or {})
        api_urls["public"] = base
        api_urls["private"] = base
        urls["api"] = api_urls
        self.client.urls = urls

    def _require_credentials(self) -> None:
        if not self.credentials.api_key or not self.credentials.api_secret:
            raise ExchangeConfigurationError("Missing API credentials for exchange adapter.")

    def normalize_symbol(self, symbol: str, market_type: str | None = None) -> str:
        raw = str(symbol or "").strip().upper()
        if not raw:
            raise ExchangeConfigurationError("Symbol is required.")
        if ":" in raw and "/" in raw:
            return raw
        cleaned = raw.replace("-", "/").replace("_", "/")
        if "/" in cleaned:
            return cleaned
        for quote in ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH"):
            if cleaned.endswith(quote) and len(cleaned) > len(quote):
                base = cleaned[: -len(quote)]
                pair = f"{base}/{quote}"
                if is_derivatives_market(market_type or self.public_market_type) and quote in {"USDT", "USDC", "USD"}:
                    return f"{pair}:{quote}"
                return pair
        return raw

    def describe(self) -> dict[str, Any]:
        return {
            "exchange_id": self.exchange_id,
            "market_type": self.public_market_type,
            "exchange_market_type": self.market_type,
            "testnet": self.testnet,
            "base_url_configured": bool(self.base_url),
            "capabilities": self.capabilities().to_dict(),
        }

    def capabilities(self) -> ExchangeCapabilities:
        has = getattr(self.client, "has", {}) or {}
        return ExchangeCapabilities(
            exchange_id=self.exchange_id,
            name=getattr(self.client, "name", self.exchange_id),
            sandbox_supported=bool(getattr(self.client, "urls", {}).get("test") or hasattr(self.client, "set_sandbox_mode")),
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
            return {"ok": True, "exchange": self.exchange_id, "market_count": len(markets)}
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

    def health_check(self) -> dict[str, Any]:
        try:
            server_time = self.client.fetch_time() if self.client.has.get("fetchTime") else None
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
        public_type = normalize_market_type(market_type)
        exchange_type = to_exchange_market_type(self.exchange_id, public_type)
        self.client.options["defaultType"] = exchange_type
        self.public_market_type = public_type
        self.market_type = exchange_type
        normalized_symbol = self.normalize_symbol(symbol, public_type)
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
            "active": bool(market.get("active")) if market else False,
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
        try:
            return self.client.fetch_ticker(self.normalize_symbol(symbol))
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

    def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        self._require_credentials()
        try:
            return self.client.fetch_open_orders(self.normalize_symbol(symbol) if symbol else None)
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

    def fetch_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        self._require_credentials()
        try:
            normalized = self.normalize_symbol(symbol) if symbol else None
            return list(self.client.fetch_positions([normalized] if normalized else None) or [])
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

    def create_order(self, *, symbol: str, order_type: str, side: str, amount: float, price: float | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._require_credentials()
        try:
            return self.client.create_order(self.normalize_symbol(symbol), str(order_type).lower(), str(side).lower(), float(amount), None if price is None else float(price), params or {})
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

    def cancel_order(self, *, order_id: str, symbol: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._require_credentials()
        try:
            return self.client.cancel_order(str(order_id), self.normalize_symbol(symbol), params or {})
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

    def set_margin_mode(
        self,
        *,
        margin_mode: str,
        symbol: str,
        leverage: float | None = None,
    ) -> dict[str, Any]:
        self._require_credentials()
        normalized_symbol = self.normalize_symbol(symbol, self.public_market_type)
        params: dict[str, Any] = {}
        if leverage is not None:
            params["leverage"] = float(leverage)
        try:
            result = self.client.set_margin_mode(str(margin_mode).lower(), normalized_symbol, params)
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc
        payload = dict(result or {})
        payload.setdefault("symbol", normalized_symbol)
        payload.setdefault("margin_mode", str(margin_mode).lower())
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
        normalized_symbol = self.normalize_symbol(symbol, self.public_market_type)
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
        normalized_symbol = self.normalize_symbol(symbol, self.public_market_type)
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
        normalized_symbol = self.normalize_symbol(symbol, self.public_market_type)
        try:
            return self.client.fetch_order(str(order_id), normalized_symbol, params or {})
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

    def fetch_my_trades(
        self,
        symbol: str,
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self._require_credentials()
        normalized_symbol = self.normalize_symbol(symbol, self.public_market_type)
        try:
            return list(
                self.client.fetch_my_trades(
                    normalized_symbol,
                    since=since,
                    limit=limit,
                    params=params or {},
                )
                or []
            )
        except ccxt.AuthenticationError as exc:
            raise ExchangeAuthError(str(exc)) from exc
        except Exception as exc:
            raise ExchangeConnectionError(str(exc)) from exc

    def get_position_mode(self) -> str | None:
        try:
            if hasattr(self.client, "fetch_position_mode"):
                payload = self.client.fetch_position_mode()
                if isinstance(payload, dict):
                    mode = payload.get("mode") or payload.get("positionMode")
                    return str(mode) if mode else None
            return None
        except Exception:
            return None

    def contract_size_for_symbol(self, symbol: str) -> float | None:
        normalized_symbol = self.normalize_symbol(symbol, self.public_market_type)
        try:
            markets = self.client.load_markets()
            market = markets.get(normalized_symbol)
            if not market:
                return None
            info = market.get("info") if isinstance(market.get("info"), dict) else {}
            for candidate in (
                market.get("contractSize"),
                market.get("contract_size"),
                info.get("contractSize"),
                info.get("contract_size"),
                info.get("quanto_multiplier"),
            ):
                if candidate is None or candidate == "":
                    continue
                value = float(candidate)
                if value > 0.0:
                    return value
        except Exception:
            return None
        return None
