from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from core.execution_models import PortfolioState


@dataclass(frozen=True)
class MarginTier:
    notional_floor: float
    notional_cap: float
    max_leverage: float
    maintenance_margin_rate: float
    maintenance_amount: float = 0.0


@dataclass
class MarginSnapshot:
    mark_price: float
    notional: float
    unrealized_pnl: float
    collateral: float
    maintenance_margin: float
    maintenance_margin_rate: float
    maintenance_amount: float
    margin_balance: float
    margin_ratio: float | None
    liquidation_price: float | None
    bankruptcy_price: float | None
    should_liquidate: bool
    margin_mode: str
    active_tier_cap: float | None = None


DEFAULT_MARGIN_TIERS: list[MarginTier] = [
    MarginTier(0.0, 50_000.0, 125.0, 0.0040, 0.0),
    MarginTier(50_000.0, 250_000.0, 100.0, 0.0050, 50.0),
    MarginTier(250_000.0, 1_000_000.0, 50.0, 0.0100, 1_300.0),
    MarginTier(1_000_000.0, 5_000_000.0, 25.0, 0.0250, 16_300.0),
    MarginTier(5_000_000.0, float("inf"), 10.0, 0.0500, 141_300.0),
]


class MarginTierResolver:
    def __init__(self, tiers: Sequence[MarginTier] | None = None):
        safe_tiers = list(tiers or DEFAULT_MARGIN_TIERS)
        if not safe_tiers:
            safe_tiers = list(DEFAULT_MARGIN_TIERS)
        self._tiers = sorted(safe_tiers, key=lambda t: float(t.notional_floor))

    def resolve(self, notional: float) -> MarginTier:
        n = max(0.0, float(notional))
        for tier in self._tiers:
            if n >= float(tier.notional_floor) and n <= float(tier.notional_cap):
                return tier
        return self._tiers[-1]


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default

    if isinstance(value, bool):
        v = float(value)
        return v if math.isfinite(v) else default

    if isinstance(value, (int, float)):
        v = float(value)
        return v if math.isfinite(v) else default

    if isinstance(value, str):
        try:
            v = float(value.strip())
        except ValueError:
            return default
        return v if math.isfinite(v) else default

    return default


def derive_mark_price(
    *,
    close: float,
    high: float | None = None,
    low: float | None = None,
    open_price: float | None = None,
    source: str = "close",
) -> float:
    src = str(source or "close").strip().lower()
    c = _safe_float(close, 0.0)
    h = _safe_float(high, c if c > 0.0 else 0.0)
    l = _safe_float(low, c if c > 0.0 else 0.0)
    o = _safe_float(open_price, c if c > 0.0 else 0.0)

    if src == "hlc3":
        base = (h + l + c) / 3.0
    elif src == "ohlc4":
        base = (o + h + l + c) / 4.0
    else:
        base = c

    return base if base > 0.0 and math.isfinite(base) else c


def _normalize_side(side: str | None) -> str:
    s = str(side or "long").strip().lower()
    return "short" if s == "short" else "long"


def _side_multiplier(side: str | None) -> float:
    return -1.0 if _normalize_side(side) == "short" else 1.0


def compute_unrealized_pnl(
    *,
    qty: float,
    entry_price: float,
    mark_price: float,
    side: str | None,
) -> float:
    q = abs(_safe_float(qty, 0.0))
    ep = _safe_float(entry_price, 0.0)
    mp = _safe_float(mark_price, 0.0)
    if q <= 0.0 or ep <= 0.0 or mp <= 0.0:
        return 0.0

    if _normalize_side(side) == "short":
        return q * (ep - mp)
    return q * (mp - ep)


def compute_bankruptcy_price(
    *,
    qty: float,
    entry_price: float,
    collateral: float,
    side: str | None,
) -> float | None:
    q = abs(_safe_float(qty, 0.0))
    ep = _safe_float(entry_price, 0.0)
    c = _safe_float(collateral, 0.0)
    if q <= 0.0 or ep <= 0.0:
        return None

    if _normalize_side(side) == "short":
        px = ep + (c / q)
    else:
        px = ep - (c / q)

    if not math.isfinite(px) or px <= 0.0:
        return None
    return px


def normalize_maintenance_margin_override(value: float | None) -> float | None:
    if value is None:
        return None

    mm = _safe_float(value, -1.0)
    if not math.isfinite(mm):
        raise ValueError("maintenance_margin_override must be a finite float")
    if mm <= 0.0 or mm >= 1.0:
        raise ValueError("maintenance_margin_override must be > 0 and < 1")
    return mm


def compute_liquidation_price_iterative(
    *,
    qty: float,
    entry_price: float,
    collateral: float,
    side: str | None,
    resolver: MarginTierResolver,
    maintenance_margin_override: float | None = None,
    max_iters: int = 12,
) -> tuple[float | None, MarginTier]:
    q = abs(_safe_float(qty, 0.0))
    ep = _safe_float(entry_price, 0.0)
    c = _safe_float(collateral, 0.0)

    if q <= 0.0 or ep <= 0.0:
        fallback_tier = resolver.resolve(0.0)
        return None, fallback_tier

    mm_override = normalize_maintenance_margin_override(maintenance_margin_override)
    guess = ep
    tier = resolver.resolve(q * guess)
    is_long = _normalize_side(side) == "long"

    for _ in range(max_iters):
        tier = resolver.resolve(q * guess)
        mmr = _safe_float(
            mm_override if mm_override is not None else tier.maintenance_margin_rate,
            0.0,
        )
        maint_amount = 0.0 if mm_override is not None else _safe_float(tier.maintenance_amount, 0.0)

        denom = q * (1.0 - mmr) if is_long else q * (1.0 + mmr)
        if denom <= 0.0 or not math.isfinite(denom):
            return None, tier

        if is_long:
            new_px = (q * ep - c - maint_amount) / denom
        else:
            new_px = (q * ep + c + maint_amount) / denom

        if not math.isfinite(new_px) or new_px <= 0.0:
            return None, tier

        if abs(new_px - guess) <= 1e-8:
            return new_px, tier

        guess = new_px

    return (guess if guess > 0.0 and math.isfinite(guess) else None), tier


def evaluate_position_margin(
    *,
    state: PortfolioState,
    market_type: str,
    mark_price: float,
    leverage: float,
    margin_mode: str = "isolated",
    resolver: MarginTierResolver | None = None,
    maintenance_margin_override: float | None = None,
) -> MarginSnapshot | None:
    mt = str(market_type or "spot").strip().lower()
    if mt != "futures":
        return None

    qty = abs(_safe_float(state.position_qty, 0.0))
    entry_price = _safe_float(state.entry_price, 0.0)
    mp = _safe_float(mark_price, 0.0)

    if qty <= 0.0 or entry_price <= 0.0 or mp <= 0.0:
        return None

    mode = str(margin_mode or getattr(state, "margin_mode", "isolated")).strip().lower()
    if mode not in {"isolated", "cross"}:
        mode = "isolated"

    mm_override = normalize_maintenance_margin_override(maintenance_margin_override)
    resolver = resolver or MarginTierResolver()

    isolated_margin = _safe_float(getattr(state, "isolated_margin", 0.0), 0.0)
    wallet_cash = _safe_float(getattr(state, "cash", 0.0), 0.0)

    collateral = isolated_margin if mode == "isolated" else wallet_cash
    unrealized_pnl = compute_unrealized_pnl(
        qty=qty,
        entry_price=entry_price,
        mark_price=mp,
        side=getattr(state, "side", None),
    )
    margin_balance = collateral + unrealized_pnl
    notional = qty * mp

    liq_price, tier = compute_liquidation_price_iterative(
        qty=qty,
        entry_price=entry_price,
        collateral=collateral,
        side=getattr(state, "side", None),
        resolver=resolver,
        maintenance_margin_override=mm_override,
    )

    if liq_price is not None:
        tier = resolver.resolve(qty * liq_price)

    mmr = _safe_float(mm_override if mm_override is not None else tier.maintenance_margin_rate, 0.0)
    maint_amount = 0.0 if mm_override is not None else _safe_float(tier.maintenance_amount, 0.0)
    maintenance_margin = max(0.0, notional * mmr - maint_amount)

    if margin_balance <= 0.0:
        margin_ratio: float | None = float("inf")
    else:
        margin_ratio = maintenance_margin / margin_balance

    is_long = _normalize_side(getattr(state, "side", None)) == "long"
    should_liquidate = False
    if liq_price is not None:
        should_liquidate = mp <= liq_price if is_long else mp >= liq_price
    if margin_balance <= maintenance_margin:
        should_liquidate = True

    bankruptcy_price = compute_bankruptcy_price(
        qty=qty,
        entry_price=entry_price,
        collateral=collateral,
        side=getattr(state, "side", None),
    )

    return MarginSnapshot(
        mark_price=mp,
        notional=notional,
        unrealized_pnl=unrealized_pnl,
        collateral=collateral,
        maintenance_margin=maintenance_margin,
        maintenance_margin_rate=mmr,
        maintenance_amount=maint_amount,
        margin_balance=margin_balance,
        margin_ratio=margin_ratio,
        liquidation_price=liq_price,
        bankruptcy_price=bankruptcy_price,
        should_liquidate=bool(should_liquidate),
        margin_mode=mode,
        active_tier_cap=_safe_float(tier.notional_cap, 0.0),
    )