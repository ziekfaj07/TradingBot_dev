from __future__ import annotations

"""Market-type normalization helpers.

Public/UI market types are intentionally limited to:
- spot
- futures

Some exchange libraries use a different internal type for perpetual futures.
For example, CCXT/Gate.io expects `swap` for USDT perpetual contracts. Keep
that exchange-specific mapping behind the service/adapter layer so the UI and
API payloads stay user-friendly.
"""

_PUBLIC_SPOT_MARKET_TYPES = {"spot", "cash"}
_PUBLIC_FUTURES_MARKET_TYPES = {"future", "futures", "swap", "perp", "perpetual", "derivative", "derivatives"}


def normalize_market_type(market_type: str | None) -> str:
    """Return the canonical public market type: `spot` or `futures`."""
    mt = str(market_type or "spot").strip().lower()
    if mt in _PUBLIC_SPOT_MARKET_TYPES:
        return "spot"
    if mt in _PUBLIC_FUTURES_MARKET_TYPES:
        return "futures"
    return "spot"


def is_spot_market(market_type: str | None) -> bool:
    return normalize_market_type(market_type) == "spot"


def is_derivatives_market(market_type: str | None) -> bool:
    return normalize_market_type(market_type) == "futures"


def to_exchange_market_type(exchange_name: str | None, market_type: str | None) -> str:
    """Map public market type to the value expected by the exchange adapter.

    Gate.io through CCXT expects `swap` for perpetual futures, while the app and
    UI should present this as `futures`.
    """
    public_type = normalize_market_type(market_type)
    exchange = str(exchange_name or "").strip().lower()
    if public_type == "spot":
        return "spot"
    if exchange in {"gate", "gate.io", "gateio"}:
        return "swap"
    return "swap"


def market_type_error_label() -> str:
    return "market_type must be one of: spot, futures"
