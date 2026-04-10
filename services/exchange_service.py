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


def resolve_exchange_env_names(
    *,
    testnet: bool,
    api_key_env: str = "GATEIO_API_KEY",
    api_secret_env: str = "GATEIO_API_SECRET",
    api_passphrase_env: str | None = None,
    live_api_key_env: str | None = None,
    live_api_secret_env: str | None = None,
    live_api_passphrase_env: str | None = None,
    demo_api_key_env: str | None = None,
    demo_api_secret_env: str | None = None,
    demo_api_passphrase_env: str | None = None,
) -> dict[str, str | None]:
    selected_key_env = api_key_env
    selected_secret_env = api_secret_env
    selected_passphrase_env = api_passphrase_env

    if testnet:
        if demo_api_key_env:
            selected_key_env = demo_api_key_env
        if demo_api_secret_env:
            selected_secret_env = demo_api_secret_env
        if demo_api_passphrase_env is not None:
            selected_passphrase_env = demo_api_passphrase_env
    else:
        if live_api_key_env:
            selected_key_env = live_api_key_env
        if live_api_secret_env:
            selected_secret_env = live_api_secret_env
        if live_api_passphrase_env is not None:
            selected_passphrase_env = live_api_passphrase_env

    return {
        "api_key_env": selected_key_env,
        "api_secret_env": selected_secret_env,
        "api_passphrase_env": selected_passphrase_env,
        "credential_profile": "demo" if testnet else "live",
    }


def build_exchange_adapter(
    *,
    exchange_name: str = "gateio",
    market_type: str = "spot",
    testnet: bool = False,
    settle_currency: str = "USDT",
    api_key_env: str = "GATEIO_API_KEY",
    api_secret_env: str = "GATEIO_API_SECRET",
    api_passphrase_env: str | None = None,
    live_api_key_env: str | None = None,
    live_api_secret_env: str | None = None,
    live_api_passphrase_env: str | None = None,
    demo_api_key_env: str | None = None,
    demo_api_secret_env: str | None = None,
    demo_api_passphrase_env: str | None = None,
) -> GateIOAdapter:
    normalized_exchange = _normalize_exchange_name(exchange_name)
    if normalized_exchange != "gateio":
        raise ExchangeConfigurationError(f"Exchange not yet implemented: {normalized_exchange}")

    resolved = resolve_exchange_env_names(
        testnet=testnet,
        api_key_env=api_key_env,
        api_secret_env=api_secret_env,
        api_passphrase_env=api_passphrase_env,
        live_api_key_env=live_api_key_env,
        live_api_secret_env=live_api_secret_env,
        live_api_passphrase_env=live_api_passphrase_env,
        demo_api_key_env=demo_api_key_env,
        demo_api_secret_env=demo_api_secret_env,
        demo_api_passphrase_env=demo_api_passphrase_env,
    )

    creds = gateio_credentials_from_env(
        api_key_env=str(resolved["api_key_env"] or ""),
        api_secret_env=str(resolved["api_secret_env"] or ""),
        api_passphrase_env=resolved["api_passphrase_env"],
    )
    return GateIOAdapter(
        api_key=creds.api_key,
        api_secret=creds.api_secret,
        api_passphrase=creds.api_passphrase,
        market_type=market_type,
        testnet=testnet,
        settle_currency=settle_currency,
    )


def get_exchange_capabilities(
    *,
    exchange_name: str = "gateio",
    market_type: str = "spot",
    testnet: bool = False,
    settle_currency: str = "USDT",
) -> dict[str, Any]:
    adapter = build_exchange_adapter(
        exchange_name=exchange_name,
        market_type=market_type,
        testnet=testnet,
        settle_currency=settle_currency,
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
    settle_currency: str = "USDT",
    enable_live_trading: bool = False,
    dry_run_live: bool = True,
    api_key_env: str = "GATEIO_API_KEY",
    api_secret_env: str = "GATEIO_API_SECRET",
    api_passphrase_env: str | None = None,
    live_api_key_env: str | None = None,
    live_api_secret_env: str | None = None,
    live_api_passphrase_env: str | None = None,
    demo_api_key_env: str | None = None,
    demo_api_secret_env: str | None = None,
    demo_api_passphrase_env: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    normalized_exchange = _normalize_exchange_name(exchange_name)

    resolved = resolve_exchange_env_names(
        testnet=testnet,
        api_key_env=api_key_env,
        api_secret_env=api_secret_env,
        api_passphrase_env=api_passphrase_env,
        live_api_key_env=live_api_key_env,
        live_api_secret_env=live_api_secret_env,
        live_api_passphrase_env=live_api_passphrase_env,
        demo_api_key_env=demo_api_key_env,
        demo_api_secret_env=demo_api_secret_env,
        demo_api_passphrase_env=demo_api_passphrase_env,
    )

    adapter = build_exchange_adapter(
        exchange_name=normalized_exchange,
        market_type=market_type,
        testnet=testnet,
        settle_currency=settle_currency,
        api_key_env=str(resolved["api_key_env"] or ""),
        api_secret_env=str(resolved["api_secret_env"] or ""),
        api_passphrase_env=resolved["api_passphrase_env"],
    )

    creds_present = bool(adapter.credentials.api_key and adapter.credentials.api_secret)
    if not creds_present:
        errors.append(
            f"Missing credentials in env vars {resolved['api_key_env']!r} / {resolved['api_secret_env']!r}."
        )

    if normalized_exchange != "gateio":
        errors.append("Only Gate.io is enabled in this build.")

    normalized_market_type = str(market_type or "spot").strip().lower()
    if normalized_market_type not in {"spot", "swap", "future", "futures", "perp", "perpetual"}:
        errors.append("market_type must be 'spot' or 'swap'.")

    if testnet:
        warnings.append(
            f"exchange_testnet=true. Gate.io testnet/demo routing is active using credential profile={resolved['credential_profile']}."
        )
    else:
        warnings.append(
            f"exchange_testnet=false. Gate.io live routing is active using credential profile={resolved['credential_profile']}."
        )

    if enable_live_trading and dry_run_live:
        warnings.append(
            "enable_live_trading=true while live_dry_run=true. Start is allowed, but all live submits remain simulated until live_dry_run=false."
        )
    if not enable_live_trading and not dry_run_live:
        warnings.append(
            "live_dry_run=false while enable_live_trading=false. Start is allowed, but order submission remains fail-closed because the config is not armed."
        )

    symbol_result = adapter.validate_symbol(symbol=symbol, market_type=market_type)
    if not symbol_result.get("ok"):
        env_label = "Gate.io testnet" if testnet else "Gate.io live"
        errors.append(f"Symbol is not available on {env_label}: {symbol!r}.")

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
    payload["credential_profile"] = resolved["credential_profile"]
    payload["resolved_api_key_env"] = resolved["api_key_env"]
    payload["resolved_api_secret_env"] = resolved["api_secret_env"]
    payload["settle_currency"] = str(settle_currency or "USDT").strip().upper() or "USDT"
    payload["symbol_validation"] = symbol_result
    payload["can_submit_live_orders"] = bool(enable_live_trading) and not bool(dry_run_live) and not errors
    return payload


def connect_exchange(
    exchange_name: str,
    *,
    market_type: str = "spot",
    testnet: bool = False,
    settle_currency: str = "USDT",
) -> dict[str, Any]:
    adapter = build_exchange_adapter(
        exchange_name=exchange_name,
        market_type=market_type,
        testnet=testnet,
        settle_currency=settle_currency,
    )
    return adapter.health_check()


def rehearse_live_order_flow(
    *,
    exchange_name: str = "gateio",
    market_type: str = "swap",
    symbol: str = "BTCUSDT",
    testnet: bool = True,
    settle_currency: str = "USDT",
    enable_live_trading: bool = False,
    dry_run_live: bool = True,
    api_key_env: str = "GATEIO_API_KEY",
    api_secret_env: str = "GATEIO_API_SECRET",
    api_passphrase_env: str | None = None,
    live_api_key_env: str | None = None,
    live_api_secret_env: str | None = None,
    live_api_passphrase_env: str | None = None,
    demo_api_key_env: str | None = None,
    demo_api_secret_env: str | None = None,
    demo_api_passphrase_env: str | None = None,
    side: str = "buy",
    qty: float = 1.0,
    order_type: str = "limit",
    limit_price: float | None = None,
    limit_price_offset_pct: float = 0.10,
    reduce_only: bool = False,
    cancel_after_submit: bool = True,
    client_order_id_prefix: str = "tb-rehearsal",
) -> dict[str, Any]:
    from services.live_execution_service import LiveExecutionService
    import time

    validation = validate_live_config(
        exchange_name=exchange_name,
        market_type=market_type,
        symbol=symbol,
        testnet=testnet,
        settle_currency=settle_currency,
        enable_live_trading=enable_live_trading,
        dry_run_live=dry_run_live,
        api_key_env=api_key_env,
        api_secret_env=api_secret_env,
        api_passphrase_env=api_passphrase_env,
        live_api_key_env=live_api_key_env,
        live_api_secret_env=live_api_secret_env,
        live_api_passphrase_env=live_api_passphrase_env,
        demo_api_key_env=demo_api_key_env,
        demo_api_secret_env=demo_api_secret_env,
        demo_api_passphrase_env=demo_api_passphrase_env,
    )
    if not validation.get("ok"):
        return {
            "ok": False,
            "phase": "validate",
            "validation": validation,
        }

    live = LiveExecutionService(
        exchange_name=exchange_name,
        market_type=market_type,
        testnet=testnet,
        settle_currency=settle_currency,
        api_key_env=api_key_env,
        api_secret_env=api_secret_env,
        api_passphrase_env=api_passphrase_env,
        live_api_key_env=live_api_key_env,
        live_api_secret_env=live_api_secret_env,
        live_api_passphrase_env=live_api_passphrase_env,
        demo_api_key_env=demo_api_key_env,
        demo_api_secret_env=demo_api_secret_env,
        demo_api_passphrase_env=demo_api_passphrase_env,
        client_order_id_prefix=client_order_id_prefix,
        armed=enable_live_trading,
        dry_run=dry_run_live,
    )

    normalized_symbol = live.adapter.normalize_symbol(symbol)
    resolved_price = limit_price
    ticker = None

    if str(order_type or "market").strip().lower() == "limit" and resolved_price is None:
        ticker = live.adapter.fetch_ticker(normalized_symbol)
        last_price = float(ticker.get("last") or ticker.get("bid") or ticker.get("ask") or 0.0)
        if last_price <= 0.0:
            raise ExchangeConfigurationError("Could not derive a rehearsal limit price from the current ticker.")
        offset = max(0.001, float(limit_price_offset_pct or 0.10))
        if str(side or "buy").strip().lower() == "buy":
            resolved_price = last_price * (1.0 - offset)
        else:
            resolved_price = last_price * (1.0 + offset)

    submit = live.submit_order(
        symbol=normalized_symbol,
        side=side,
        qty=qty,
        order_type=order_type,
        price=resolved_price,
        reduce_only=reduce_only,
        client_order_id=f"{live.client_order_id_prefix}-{int(time.time())}",
    ).to_dict()

    cancel = None
    if cancel_after_submit:
        cancel = live.cancel_order(
            symbol=normalized_symbol,
            order_id=str(submit.get("order", {}).get("id") or ""),
        )

    return {
        "ok": True,
        "phase": "rehearsed",
        "validation": validation,
        "normalized_symbol": normalized_symbol,
        "resolved_limit_price": resolved_price,
        "ticker": ticker,
        "submit": submit,
        "cancel": cancel,
    }