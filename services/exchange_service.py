from __future__ import annotations

from typing import Any

from market.exceptions import ExchangeConfigurationError
from market.gateio_adapter import GateIOAdapter, gateio_credentials_from_env
from market.models import LiveConfigValidation


SUPPORTED_EXCHANGES = {"gateio"}


def _normalize_exchange_name(exchange_name: str | None) -> str:
    raw = str(exchange_name or "gateio").strip().lower()
    aliases = {
        "gate": "gateio",
        "gate.io": "gateio",
        "gateio": "gateio",
    }
    normalized = aliases.get(raw, raw)
    if normalized not in SUPPORTED_EXCHANGES:
        raise ExchangeConfigurationError(
            f"Unsupported exchange: {exchange_name!r}. Supported exchanges: {sorted(SUPPORTED_EXCHANGES)}"
        )
    return normalized


def build_exchange_adapter(
    *,
    exchange_name: str = "gateio",
    market_type: str = "spot",
    testnet: bool = False,
    api_key_env: str = "GATEIO_API_KEY",
    api_secret_env: str = "GATEIO_API_SECRET",
    api_passphrase_env: str | None = "GATEIO_API_PASSPHRASE",
) -> GateIOAdapter:
    normalized_exchange = _normalize_exchange_name(exchange_name)
    if normalized_exchange != "gateio":
        raise ExchangeConfigurationError(f"Exchange not yet implemented: {normalized_exchange}")

    creds = gateio_credentials_from_env(
        api_key_env=api_key_env,
        api_secret_env=api_secret_env,
        api_passphrase_env=api_passphrase_env,
    )
    return GateIOAdapter(
        api_key=creds.api_key,
        api_secret=creds.api_secret,
        api_passphrase=creds.api_passphrase,
        market_type=market_type,
        testnet=testnet,
    )


def get_exchange_capabilities(
    *,
    exchange_name: str = "gateio",
    market_type: str = "spot",
    testnet: bool = False,
) -> dict[str, Any]:
    adapter = build_exchange_adapter(
        exchange_name=exchange_name,
        market_type=market_type,
        testnet=testnet,
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
    api_key_env: str = "GATEIO_API_KEY",
    api_secret_env: str = "GATEIO_API_SECRET",
    api_passphrase_env: str | None = "GATEIO_API_PASSPHRASE",
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    normalized_exchange = _normalize_exchange_name(exchange_name)
    adapter = build_exchange_adapter(
        exchange_name=normalized_exchange,
        market_type=market_type,
        testnet=False,
        api_key_env=api_key_env,
        api_secret_env=api_secret_env,
        api_passphrase_env=api_passphrase_env,
    )

    creds_present = bool(adapter.credentials.api_key and adapter.credentials.api_secret)
    if not creds_present:
        errors.append(
            f"Missing credentials in env vars {api_key_env!r} / {api_secret_env!r}."
        )

    if normalized_exchange != "gateio":
        errors.append("Only Gate.io is enabled in this build.")

    if str(market_type or "spot").strip().lower() not in {"spot", "swap", "future", "futures", "perp", "perpetual"}:
        errors.append("market_type must be 'spot' or 'swap'.")

    if enable_live_trading and dry_run_live:
        warnings.append(
            "enable_live_trading=true while dry_run_live=true. This is safe, but real order placement remains disabled until v0.7.2."
        )

    symbol_result = adapter.validate_symbol(symbol=symbol, market_type=market_type)
    if not symbol_result.get("ok"):
        errors.append(f"Symbol is not available on Gate.io: {symbol!r}.")

    result = LiveConfigValidation(
        ok=not errors,
        exchange_name=normalized_exchange,
        market_type=str(market_type or "spot").strip().lower(),
        symbol=symbol,
        credentials_present=creds_present,
        dry_run_live=bool(dry_run_live),
        enable_live_trading=bool(enable_live_trading),
        errors=errors,
        warnings=warnings,
    )
    payload = result.to_dict()
    payload["symbol_validation"] = symbol_result
    return payload


def connect_exchange(
    exchange_name: str,
    *,
    market_type: str = "spot",
    testnet: bool = False,
) -> dict[str, Any]:
    adapter = build_exchange_adapter(
        exchange_name=exchange_name,
        market_type=market_type,
        testnet=testnet,
    )
    return adapter.health_check()