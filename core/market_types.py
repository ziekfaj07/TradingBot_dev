from __future__ import annotations


def normalize_market_type(value: str | None) -> str:
    """Return the bot-wide canonical market type.

    Canonical values:
    - spot: cash/spot market
    - swap: perpetual futures / derivatives market

    We intentionally normalize user-facing aliases such as futures/perp into
    swap because CCXT/Gate.io use swap for perpetual contracts.
    """
    raw = str(value or "spot").strip().lower()
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
        raise ValueError("market_type must be 'spot' or 'swap'")
    return normalized


def is_spot_market(value: str | None) -> bool:
    return normalize_market_type(value) == "spot"


def is_derivatives_market(value: str | None) -> bool:
    return normalize_market_type(value) == "swap"
