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
    testnet: bool = False,
    enable_live_trading: bool = False,
    dry_run_live: bool = True,
    api_key_env: str = "GATEIO_API_KEY",
    api_secret_env: str = "GATEIO_API_SECRET",
    api_passphrase_env: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    normalized_exchange = _normalize_exchange_name(exchange_name)
    normalized_market_type = str(market_type or "spot").strip().lower()
    aliases = {
        "spot": "spot",
        "cash": "spot",
        "swap": "swap",
        "future": "swap",
        "futures": "swap",
        "perp": "swap",
        "perpetual": "swap",
    }
    normalized_market_type = aliases.get(normalized_market_type, normalized_market_type)

    if normalized_exchange != "gateio":
        errors.append("Only Gate.io is enabled in this build.")

    if normalized_market_type not in {"spot", "swap"}:
        errors.append("market_type must be 'spot' or 'swap'.")

    if testnet and normalized_market_type == "spot":
        errors.append(
            "Gate.io testnet/demo is not supported for spot in this adapter. "
            "Use market_type='swap' for Gate testnet, or set exchange_testnet=false for live spot."
        )

    creds_present = False
    symbol_result: dict[str, Any] = {
        "ok": False,
        "input_symbol": symbol,
        "normalized_symbol": None,
        "market_type": normalized_market_type,
        "exchange": normalized_exchange,
        "testnet": bool(testnet),
        "environment": f"{normalized_exchange}-{normalized_market_type}-{'testnet' if testnet else 'live'}",
    }

    adapter: GateIOAdapter | None = None
    if not errors:
        adapter = build_exchange_adapter(
            exchange_name=normalized_exchange,
            market_type=normalized_market_type,
            testnet=testnet,
            api_key_env=api_key_env,
            api_secret_env=api_secret_env,
            api_passphrase_env=api_passphrase_env,
        )
        creds_present = bool(adapter.credentials.api_key and adapter.credentials.api_secret)

        if not creds_present:
            errors.append(
                f"Missing credentials in env vars {api_key_env!r} / {api_secret_env!r}."
            )

        try:
            symbol_result = adapter.validate_symbol(symbol=symbol, market_type=normalized_market_type)
            if not symbol_result.get("ok"):
                env_label = "Gate.io testnet" if testnet else "Gate.io live"
                errors.append(f"Symbol is not available on {env_label}: {symbol!r}.")
        except Exception as exc:
            errors.append(str(exc))
    else:
        creds = gateio_credentials_from_env(
            api_key_env=api_key_env,
            api_secret_env=api_secret_env,
            api_passphrase_env=api_passphrase_env,
        )
        creds_present = bool(creds.api_key and creds.api_secret)

    if testnet:
        warnings.append(
            "exchange_testnet=true. Gate.io sandbox/demo routing is active for this validation."
        )

    if enable_live_trading and dry_run_live:
        warnings.append(
            "enable_live_trading=true while live_dry_run=true. Start is allowed, but all live submits remain simulated until live_dry_run=false."
        )
    if not enable_live_trading and not dry_run_live:
        warnings.append(
            "live_dry_run=false while enable_live_trading=false. Start is allowed, but order submission remains fail-closed because the config is not armed."
        )

    result = LiveConfigValidation(
        ok=not errors,
        exchange_name=normalized_exchange,
        market_type=normalized_market_type,
        symbol=symbol,
        credentials_present=creds_present,
        dry_run_live=bool(dry_run_live),
        enable_live_trading=bool(enable_live_trading),
        errors=errors,
        warnings=warnings,
    )
    payload = result.to_dict()
    payload["testnet"] = bool(testnet)
    payload["symbol_validation"] = symbol_result
    payload["can_submit_live_orders"] = bool(enable_live_trading) and not bool(dry_run_live) and not errors
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