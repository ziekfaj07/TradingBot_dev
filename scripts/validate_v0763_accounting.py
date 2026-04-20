from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.execution_models import PortfolioState  # noqa: E402
from services.execution_engine import ExecutionEngine  # noqa: E402


def assert_close(name: str, actual: float, expected: float, tol: float = 1e-9) -> None:
    if not math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=tol):
        raise AssertionError(f"{name}: actual={actual!r} expected={expected!r}")


def assert_equal(name: str, actual, expected) -> None:
    if actual != expected:
        raise AssertionError(f"{name}: actual={actual!r} expected={expected!r}")


def main() -> int:
    fee_rate = 0.001
    slippage_bps = 2.0
    engine = ExecutionEngine(
        initial_cash=1000.0,
        fee_rate=fee_rate,
        slippage_bps=slippage_bps,
        max_qty=1_000_000.0,
        min_notional=0.0,
        min_qty=0.0,
    )

    # Slippage convention.
    close = 100.0
    buy_px = engine.apply_slippage(close, is_buy=True)
    sell_px = engine.apply_slippage(close, is_buy=False)
    assert_close("buy slippage", buy_px, 100.02)
    assert_close("sell slippage", sell_px, 99.98)

    # Long: entry buy, exit sell, both commissions charged.
    state = PortfolioState(cash=1000.0)
    state, entry = engine.enter_long(
        ts_iso="1",
        state=state,
        close=100.0,
        market_type="swap",
        leverage=2.0,
        trade_id=1,
        qty_override=1.0,
    )
    assert entry is not None
    assert_equal("long entry side", entry.side, "long")
    long_entry_px = 100.02
    long_entry_fee = long_entry_px * fee_rate
    assert_close("long entry price", entry.price, long_entry_px)
    assert_close("long entry fee", entry.fee, long_entry_fee)

    state, exit_fill = engine.exit_long(
        ts_iso="2",
        state=state,
        close=101.0,
        market_type="swap",
        trade_id=1,
    )
    assert exit_fill is not None
    long_exit_px = 101.0 * (1 - slippage_bps / 10_000.0)
    long_exit_fee = long_exit_px * fee_rate
    expected_long_pnl = (long_exit_px - long_entry_px) - long_entry_fee - long_exit_fee
    assert_equal("long exit side", exit_fill.side, "long")
    assert_close("long exit price", exit_fill.price, long_exit_px)
    assert_close("long net pnl", exit_fill.pnl, expected_long_pnl, tol=1e-8)

    # Short: entry sell, exit buy, both commissions charged. Short qty is absolute; side stores direction.
    state = PortfolioState(cash=1000.0)
    state, short_entry = engine.enter_short(
        ts_iso="3",
        state=state,
        close=100.0,
        market_type="swap",
        leverage=2.0,
        trade_id=2,
        qty_override=1.0,
    )
    assert short_entry is not None
    short_entry_px = 100.0 * (1 - slippage_bps / 10_000.0)
    short_entry_fee = short_entry_px * fee_rate
    assert_equal("short state side", state.side, "short")
    assert_close("short state qty absolute", state.position_qty, 1.0)
    assert_equal("short entry side", short_entry.side, "short")
    assert_close("short entry price", short_entry.price, short_entry_px)
    assert_close("short entry fee", short_entry.fee, short_entry_fee)

    state, short_exit = engine.exit_short(
        ts_iso="4",
        state=state,
        close=99.0,
        market_type="swap",
        trade_id=2,
    )
    assert short_exit is not None
    short_exit_px = 99.0 * (1 + slippage_bps / 10_000.0)
    short_exit_fee = short_exit_px * fee_rate
    expected_short_pnl = (short_entry_px - short_exit_px) - short_entry_fee - short_exit_fee
    assert_equal("short exit side", short_exit.side, "short")
    assert_close("short exit price", short_exit.price, short_exit_px)
    assert_close("short net pnl", short_exit.pnl, expected_short_pnl, tol=1e-8)

    print("[OK] v0.7.6.3 accounting validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
