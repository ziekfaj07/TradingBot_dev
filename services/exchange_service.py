from __future__ import annotations

import os
from typing import Any

from core.interfaces import ExchangeAdapter, ExchangeCredentials
from core.market_types import market_type_error_label, normalize_market_type
from market.ccxt_adapter import CCXTExchangeAdapter
from market.exceptions import ExchangeConfigurationError
from market.gateio_adapter import GateIOAdapter, gateio_credentials_from_env
from market.models import LiveConfigValidation


EXCHANGE_ALIASES = {
    "gate": "gateio",
    "gate.io": "gateio",
    "gateio": "gateio",
}


def _normalize_exchange_name(exchange_name: str | None) -> str:
    raw = str(exchange_name or "gateio").strip().lower()
    compact = raw.replace(".", "")
    return EXCHANGE_ALIASES.get(raw, EXCHANGE_ALIASES.get(compact, compact))


def _credentials_from_values_or_env(
    *,
    api_key: str | None = None,
    api_secret: str | None = None,
    api_passphrase: str | None = None,
    api_key_env: str = "GATEIO_API_KEY",
    api_secret_env: str = "GATEIO_API_SECRET",
    api_passphrase_env: str | None = "GATEIO_API_PASSPHRASE",
) -> ExchangeCredentials:
    direct_key = (api_key or "").strip()
    direct_secret = (api_secret or "").strip()
    direct_passphrase = (api_passphrase or "").strip() or None
    if direct_key or direct_secret:
        return ExchangeCredentials(
            api_key=direct_key,
            api_secret=direct_secret,
            api_passphrase=direct_passphrase,
        )

    if str(api_key_env or "").strip().upper().startswith("GATEIO"):
        return gateio_credentials_from_env(
            api_key_env=api_key_env,
            api_secret_env=api_secret_env,
            api_passphrase_env=api_passphrase_env,
        )

    return ExchangeCredentials(
        api_key=os.getenv(str(api_key_env or "").strip(), "").strip(),
        api_secret=os.getenv(str(api_secret_env or "").strip(), "").strip(),
        api_passphrase=(os.getenv(str(api_passphrase_env).strip(), "").strip() or None) if api_passphrase_env else None,
    )


def build_exchange_adapter(
    *,
    exchange_name: str = "gateio",
    market_type: str = "spot",
    testnet: bool = False,
    base_url: str | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
    api_passphrase: str | None = None,
    api_key_env: str = "GATEIO_API_KEY",
    api_secret_env: str = "GATEIO_API_SECRET",
    api_passphrase_env: str | None = "GATEIO_API_PASSPHRASE",
) -> ExchangeAdapter:
    normalized_exchange = _normalize_exchange_name(exchange_name)
    public_market_type = normalize_market_type(market_type)
    creds = _credentials_from_values_or_env(
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase,
        api_key_env=api_key_env,
        api_secret_env=api_secret_env,
        api_passphrase_env=api_passphrase_env,
    )

    if normalized_exchange == "gateio":
        return GateIOAdapter(
            api_key=creds.api_key,
            api_secret=creds.api_secret,
            api_passphrase=creds.api_passphrase,
            market_type=public_market_type,
            testnet=testnet,
            base_url=base_url,
        )

    return CCXTExchangeAdapter(
        exchange_name=normalized_exchange,
        api_key=creds.api_key,
        api_secret=creds.api_secret,
        api_passphrase=creds.api_passphrase,
        market_type=public_market_type,
        testnet=testnet,
        base_url=base_url,
    )


def get_exchange_capabilities(
    *,
    exchange_name: str = "gateio",
    market_type: str = "spot",
    testnet: bool = False,
    base_url: str | None = None,
) -> dict[str, Any]:
    adapter = build_exchange_adapter(
        exchange_name=exchange_name,
        market_type=market_type,
        testnet=testnet,
        base_url=base_url,
        api_key_env="__unused__",
        api_secret_env="__unused__",
        api_passphrase_env=None,
    )
    return adapter.describe()


def validate_live_config(
    *,
    exchange_name: str = "gateio",
    market_type: str = "spot",
    symbol: str = "BTCUSDT",
    enable_live_trading: bool = False,
    dry_run_live: bool = True,
    testnet: bool = False,
    base_url: str | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
    api_passphrase: str | None = None,
    api_key_env: str = "GATEIO_API_KEY",
    api_secret_env: str = "GATEIO_API_SECRET",
    api_passphrase_env: str | None = "GATEIO_API_PASSPHRASE",
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    normalized_exchange = _normalize_exchange_name(exchange_name)
    public_market_type = normalize_market_type(market_type)
    if str(market_type or "").strip().lower() not in {"", "spot", "cash", "future", "futures", "swap", "perp", "perpetual"}:
        errors.append(market_type_error_label())

    adapter = build_exchange_adapter(
        exchange_name=normalized_exchange,
        market_type=public_market_type,
        testnet=testnet,
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase,
        api_key_env=api_key_env,
        api_secret_env=api_secret_env,
        api_passphrase_env=api_passphrase_env,
    )

    creds_present = bool(adapter.credentials.api_key and adapter.credentials.api_secret)  # type: ignore[attr-defined]
    if not creds_present:
        errors.append(f"Missing credentials. Provide API key/secret directly or set env vars {api_key_env!r} / {api_secret_env!r}.")

    if enable_live_trading and dry_run_live:
        warnings.append("enable_live_trading=true while dry_run_live=true. This is safe: order submission remains blocked by dry-run mode.")

    if enable_live_trading and not dry_run_live and not testnet:
        warnings.append("Real live trading is armed on a non-testnet exchange. Confirm this is intentional before starting.")

    symbol_result = adapter.validate_symbol(symbol=symbol, market_type=public_market_type)
    if not symbol_result.get("ok"):
        errors.append(f"Symbol is not available on {normalized_exchange}: {symbol!r}.")

    result = LiveConfigValidation(
        ok=not errors,
        exchange_name=normalized_exchange,
        market_type=public_market_type,
        symbol=symbol,
        credentials_present=creds_present,
        dry_run_live=bool(dry_run_live),
        enable_live_trading=bool(enable_live_trading),
        errors=errors,
        warnings=warnings,
    )
    payload = result.to_dict()
    payload["mode_hint"] = "demo" if testnet else "live"
    payload["testnet"] = bool(testnet)
    payload["base_url_configured"] = bool(base_url)
    payload["symbol_validation"] = symbol_result
    return payload


def connect_exchange(
    exchange_name: str,
    *,
    market_type: str = "spot",
    testnet: bool = False,
    base_url: str | None = None,
) -> dict[str, Any]:
    adapter = build_exchange_adapter(
        exchange_name=exchange_name,
        market_type=market_type,
        testnet=testnet,
        base_url=base_url,
    )
    return adapter.health_check()
