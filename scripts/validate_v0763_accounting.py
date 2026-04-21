"""
v0.7.6.3 accounting sanity validator.

Purpose:
- Validate the canonical slippage + commission formulas independently of
  ExecutionEngine constructor signatures.
- Run cross-platform from project root with:
    Windows: .\\TradingBot311_venv\\Scripts\\python.exe scripts\\validate_v0763_accounting.py
    macOS:   ./TradingBot311_venv/bin/python scripts/validate_v0763_accounting.py

This script intentionally does NOT instantiate ExecutionEngine because that
constructor has changed across bot phases. It is a stable accounting oracle.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, getcontext
from pathlib import Path
import sys

getcontext().prec = 28

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class TradeCase:
    name: str
    side: str  # "long" or "short"
    entry_close: Decimal
    exit_close: Decimal
    qty: Decimal
    slippage_bps: Decimal
    fee_rate: Decimal


def apply_slippage(close: Decimal, side: str, action: str, bps: Decimal) -> Decimal:
    """Canonical execution-price model.

    action is the actual market action: "buy" or "sell".
    Buy pays upward slippage. Sell receives downward slippage.
    """
    slip = bps / Decimal("10000")
    if action == "buy":
        return close * (Decimal("1") + slip)
    if action == "sell":
        return close * (Decimal("1") - slip)
    raise ValueError(f"Unsupported action: {action!r}")


def fee(qty: Decimal, price: Decimal, fee_rate: Decimal) -> Decimal:
    return abs(qty * price) * fee_rate


def net_pnl(case: TradeCase) -> dict[str, Decimal | str]:
    if case.side == "long":
        entry_action = "buy"
        exit_action = "sell"
    elif case.side == "short":
        entry_action = "sell"
        exit_action = "buy"
    else:
        raise ValueError(f"Unsupported side: {case.side!r}")

    entry_price = apply_slippage(case.entry_close, case.side, entry_action, case.slippage_bps)
    exit_price = apply_slippage(case.exit_close, case.side, exit_action, case.slippage_bps)
    entry_fee = fee(case.qty, entry_price, case.fee_rate)
    exit_fee = fee(case.qty, exit_price, case.fee_rate)

    if case.side == "long":
        gross = case.qty * (exit_price - entry_price)
    else:
        gross = case.qty * (entry_price - exit_price)

    return {
        "name": case.name,
        "side": case.side,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "entry_fee": entry_fee,
        "exit_fee": exit_fee,
        "gross_pnl": gross,
        "net_pnl": gross - entry_fee - exit_fee,
    }


def assert_close(actual: Decimal, expected: Decimal, label: str, tolerance: Decimal = Decimal("0.000000000001")) -> None:
    if abs(actual - expected) > tolerance:
        raise AssertionError(f"{label}: actual={actual} expected={expected}")


def validate_math() -> None:
    cases = [
        TradeCase(
            name="long_winner",
            side="long",
            entry_close=Decimal("100"),
            exit_close=Decimal("110"),
            qty=Decimal("2"),
            slippage_bps=Decimal("2"),
            fee_rate=Decimal("0.001"),
        ),
        TradeCase(
            name="short_winner",
            side="short",
            entry_close=Decimal("100"),
            exit_close=Decimal("90"),
            qty=Decimal("2"),
            slippage_bps=Decimal("2"),
            fee_rate=Decimal("0.001"),
        ),
    ]

    long_result = net_pnl(cases[0])
    assert_close(long_result["entry_price"], Decimal("100.0200"), "long buy slippage")
    assert_close(long_result["exit_price"], Decimal("109.9780"), "long sell slippage")
    assert_close(long_result["entry_fee"], Decimal("0.2000400"), "long entry fee")
    assert_close(long_result["exit_fee"], Decimal("0.2199560"), "long exit fee")
    assert_close(long_result["gross_pnl"], Decimal("19.9160"), "long gross pnl")
    assert_close(long_result["net_pnl"], Decimal("19.4960040"), "long net pnl")

    short_result = net_pnl(cases[1])
    assert_close(short_result["entry_price"], Decimal("99.9800"), "short sell-entry slippage")
    assert_close(short_result["exit_price"], Decimal("90.0180"), "short buy-cover slippage")
    assert_close(short_result["entry_fee"], Decimal("0.1999600"), "short entry fee")
    assert_close(short_result["exit_fee"], Decimal("0.1800360"), "short exit fee")
    assert_close(short_result["gross_pnl"], Decimal("19.9240"), "short gross pnl")
    assert_close(short_result["net_pnl"], Decimal("19.5440040"), "short net pnl")

    print("[OK] Canonical slippage and commission math validated")
    for result in (long_result, short_result):
        print(
            f"[OK] {result['name']}: side={result['side']} "
            f"entry={result['entry_price']} exit={result['exit_price']} "
            f"gross={result['gross_pnl']} net={result['net_pnl']}"
        )


def validate_source_markers() -> None:
    """Lightweight source sanity checks without binding to constructor signatures."""
    engine_path = ROOT / "services" / "execution_engine.py"
    controller_path = ROOT / "services" / "mode_controller.py"

    if not engine_path.exists():
        print("[WARN] services/execution_engine.py not found; skipped source marker check")
        return

    engine_src = engine_path.read_text(encoding="utf-8", errors="ignore")
    controller_src = controller_path.read_text(encoding="utf-8", errors="ignore") if controller_path.exists() else ""

    required_engine_markers = ["enter_short", "exit_short"]
    missing = [m for m in required_engine_markers if m not in engine_src]
    if missing:
        raise AssertionError(f"Missing short accounting markers in execution_engine.py: {missing}")
    print("[OK] execution_engine.py exposes short entry/exit methods")

    if "open_fee_paid" in engine_src:
        print("[OK] execution_engine.py contains open_fee_paid marker for fee-aware net PnL")
    else:
        print("[WARN] open_fee_paid marker not found; ensure entry+exit fees are both included in realized PnL")

    if "current_side" in controller_src and '== "short"' in controller_src:
        print("[OK] mode_controller.py appears side-aware for long/short state")
    else:
        print("[WARN] Could not confirm side-aware paper state machine markers in mode_controller.py")


def main() -> int:
    print(f"TradingBot accounting validator root: {ROOT}")
    validate_math()
    validate_source_markers()
    print("[OK] v0.7.6.3 accounting validation completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
