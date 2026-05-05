from __future__ import annotations

import os
from typing import Any

from core.interfaces import ExchangeAdapter, ExchangeCredentials
from core.market_types import market_type_error_label, normalize_market_type
from market.ccxt_adapter import CCXTExchangeAdapter
from market.exceptions import ExchangeConfigurationError, ExchangeConnectionError
from market.gateio_adapter import GateIOAdapter, gateio_credentials_from_env
from market.models import LiveConfigValidation


EXCHANGE_ALIASES = {
    "gate": "gateio",
    "gate.io": "gateio",
    "gateio": "gateio",
}


def _env_name_present(name: str | None) -> bool:
    return bool(str(name or "").strip()) and bool(os.getenv(str(name or "").strip(), "").strip())


def _infer_mode_specific_env_name(base_name: str | None, mode_token: str) -> str | None:
    raw = str(base_name or "").strip()
    if not raw:
        return None

    suffixes = (
        "API_KEY",
        "API_SECRET",
        "API_PASSPHRASE",
    )
    for suffix in suffixes:
        marker = f"_{suffix}"
        if raw.endswith(marker):
            return f"{raw[:-len(marker)]}_{mode_token}{marker}"
    return None


def resolve_exchange_env_names(
    *,
    testnet: bool = False,
    api_key_env: str = "GATEIO_API_KEY",
    api_secret_env: str = "GATEIO_API_SECRET",
    api_passphrase_env: str | None = "GATEIO_API_PASSPHRASE",
    live_api_key_env: str | None = None,
    live_api_secret_env: str | None = None,
    live_api_passphrase_env: str | None = None,
    demo_api_key_env: str | None = None,
    demo_api_secret_env: str | None = None,
    demo_api_passphrase_env: str | None = None,
) -> dict[str, str | None]:
    base_key_env = str(api_key_env or "GATEIO_API_KEY").strip() or "GATEIO_API_KEY"
    base_secret_env = str(api_secret_env or "GATEIO_API_SECRET").strip() or "GATEIO_API_SECRET"
    base_passphrase_env = str(api_passphrase_env).strip() if api_passphrase_env else None

    mode_token = "DEMO" if testnet else "LIVE"
    explicit_key_env = str((demo_api_key_env if testnet else live_api_key_env) or "").strip() or None
    explicit_secret_env = str((demo_api_secret_env if testnet else live_api_secret_env) or "").strip() or None
    explicit_passphrase_env = str((demo_api_passphrase_env if testnet else live_api_passphrase_env) or "").strip() or None

    inferred_key_env = _infer_mode_specific_env_name(base_key_env, mode_token)
    inferred_secret_env = _infer_mode_specific_env_name(base_secret_env, mode_token)
    inferred_passphrase_env = _infer_mode_specific_env_name(base_passphrase_env, mode_token) if base_passphrase_env else None

    use_mode_specific_profile = bool(explicit_key_env or explicit_secret_env or explicit_passphrase_env)
    if not use_mode_specific_profile:
        use_mode_specific_profile = any(
            _env_name_present(name)
            for name in (
                inferred_key_env,
                inferred_secret_env,
                inferred_passphrase_env,
            )
        )

    resolved_key_env = explicit_key_env or (inferred_key_env if use_mode_specific_profile and inferred_key_env else base_key_env)
    resolved_secret_env = explicit_secret_env or (inferred_secret_env if use_mode_specific_profile and inferred_secret_env else base_secret_env)
    resolved_passphrase_env = explicit_passphrase_env or (
        inferred_passphrase_env if use_mode_specific_profile and inferred_passphrase_env else base_passphrase_env
    )

    return {
        "credential_profile": "demo" if testnet else "live",
        "api_key_env": resolved_key_env,
        "api_secret_env": resolved_secret_env,
        "api_passphrase_env": resolved_passphrase_env,
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
    settle_currency: str | None = None,
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

    resolved_envs = resolve_exchange_env_names(
        testnet=testnet,
        api_key_env=api_key_env,
        api_secret_env=api_secret_env,
        api_passphrase_env=api_passphrase_env,
    )

    resolved_api_key_env = str(resolved_envs["api_key_env"] or api_key_env)
    resolved_api_secret_env = str(resolved_envs["api_secret_env"] or api_secret_env)
    resolved_api_passphrase_env = resolved_envs["api_passphrase_env"]

    adapter = build_exchange_adapter(
        exchange_name=normalized_exchange,
        market_type=public_market_type,
        testnet=testnet,
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase,
        api_key_env=resolved_api_key_env,
        api_secret_env=resolved_api_secret_env,
        api_passphrase_env=resolved_api_passphrase_env,
    )

    creds_present = bool(adapter.credentials.api_key and adapter.credentials.api_secret)  # type: ignore[attr-defined]
    if not creds_present:
        errors.append(
            "Missing credentials. Provide API key/secret directly or set env vars "
            f"{resolved_api_key_env!r} / {resolved_api_secret_env!r}."
        )

    if enable_live_trading and dry_run_live:
        warnings.append("enable_live_trading=true while dry_run_live=true. This is safe: order submission remains blocked by dry-run mode.")

    if enable_live_trading and not dry_run_live and not testnet:
        warnings.append("Real live trading is armed on a non-testnet exchange. Confirm this is intentional before starting.")

    try:
        symbol_result = adapter.validate_symbol(symbol=symbol, market_type=public_market_type)
    except ExchangeConnectionError as exc:
        should_retry_live_public = (
            normalized_exchange == "gateio"
            and bool(testnet)
            and "INTERNAL" in str(exc).upper()
        )
        if not should_retry_live_public:
            raise

        fallback_adapter = build_exchange_adapter(
            exchange_name=normalized_exchange,
            market_type=public_market_type,
            testnet=False,
            base_url=None,
            api_key_env="__unused__",
            api_secret_env="__unused__",
            api_passphrase_env=None,
        )
        symbol_result = fallback_adapter.validate_symbol(
            symbol=symbol,
            market_type=public_market_type,
        )
        warnings.append(
            "Gate.io demo public metadata returned an internal error; "
            "symbol validation was retried against the live public catalog."
        )
        symbol_result["validation_source"] = "live_public_fallback"
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
    payload["resolved_api_key_env"] = resolved_api_key_env
    payload["resolved_api_secret_env"] = resolved_api_secret_env
    payload["resolved_api_passphrase_env"] = resolved_api_passphrase_env
    payload["credential_profile"] = resolved_envs["credential_profile"]
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
