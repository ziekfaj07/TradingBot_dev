from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CRITICAL_FILES = [
    "app.py",
    "core/market_types.py",
    "core/execution_models.py",
    "services/mode_controller.py",
    "services/execution_engine.py",
    "services/live_execution_service.py",
    "services/strategies/bollinger_mean_reversion.py",
    "market/gateio_adapter.py",
    "market/exceptions.py",
]

ALLOWED_FUTURES_STRING_FILES = {
    "core/binance_vision_provider.py",  # Binance Vision URL naming uses futures/spot path folders.
    "routers/run_router.py",            # API accepts futures as backward-compatible alias to swap.
    "core/market_types.py",             # Canonical alias table.
}


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def ok(msg: str) -> None:
    print(f"[OK] {msg}")


def warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}")


def iter_py_files() -> list[Path]:
    ignored = {"TradingBot311_venv", ".git", "__pycache__"}
    return [p for p in ROOT.rglob("*.py") if not any(part in ignored for part in p.parts)]


def check_critical_files() -> bool:
    missing = [f for f in CRITICAL_FILES if not (ROOT / f).exists()]
    if missing:
        fail("Critical files missing: " + ", ".join(missing))
        return False
    ok("Critical source files exist")
    return True


def check_ast_parse() -> bool:
    bad = []
    for p in iter_py_files():
        try:
            ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        except SyntaxError as exc:
            bad.append(f"{rel(p)}:{exc.lineno}: {exc.msg}")
    if bad:
        fail("Python AST parse failed")
        for item in bad:
            print("  -", item)
        return False
    ok("Python AST parse clean")
    return True


def check_compileall() -> bool:
    proc = subprocess.run(
        [sys.executable, "-m", "compileall", "core", "market", "services", "routers"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if proc.returncode != 0:
        fail("compileall failed")
        print(proc.stdout)
        return False
    ok("compileall clean")
    return True


def check_legacy_futures_checks() -> bool:
    offenders: list[str] = []
    patterns = [
        re.compile(r"==\s*['\"]futures['\"]"),
        re.compile(r"!=\s*['\"]futures['\"]"),
        re.compile(r"in\s*\([^\)]*['\"]spot['\"]\s*,\s*['\"]futures['\"]"),
    ]
    for p in iter_py_files():
        rp = rel(p)
        if rp in ALLOWED_FUTURES_STRING_FILES:
            continue
        text = p.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), start=1):
            if any(pat.search(line) for pat in patterns):
                offenders.append(f"{rp}:{i}: {line.strip()}")
    if offenders:
        fail("Legacy hard-coded futures equality checks remain; use core.market_types")
        for item in offenders:
            print("  -", item)
        return False
    ok("No legacy futures equality checks outside allowlisted alias/path files")
    return True


def check_paper_loop_side_awareness() -> bool:
    path = ROOT / "services/mode_controller.py"
    text = path.read_text(encoding="utf-8")
    required = [
        "position_side = str(getattr(self._state, \"side\", None) or \"\").lower() or None",
        "signal > 0 and position_side == \"short\"",
        "signal < 0 and position_side == \"long\"",
        "signal < 0 and is_flat and bool(cfg.allow_short) and is_derivatives_market(cfg.market_type)",
    ]
    missing = [r for r in required if r not in text]
    bad = [
        "signal > 0 and position_qty < 0.0",
        "signal < 0 and position_qty > 0.0",
    ]
    stale = [b for b in bad if b in text]
    if missing or stale:
        fail("Paper loop is not side-aware; shorts must use state.side, not signed qty")
        for item in missing:
            print("  - missing marker:", item)
        for item in stale:
            print("  - stale marker:", item)
        return False
    ok("Paper loop side-aware for long/short parity")
    return True


def check_live_post_submit_verification() -> bool:
    path = ROOT / "services/live_execution_service.py"
    text = path.read_text(encoding="utf-8")
    required = [
        "post_submit_verified",
        "post_submit_verification_error",
        "Live order is not fill-confirmed yet; refusing to create fill",
    ]
    missing = [r for r in required if r not in text]
    if missing:
        fail("Live order post-submit verification markers missing")
        for item in missing:
            print("  - missing marker:", item)
        return False
    ok("Live order post-submit verification markers present")
    return True


def check_bollinger_contract() -> bool:
    path = ROOT / "services/strategies/bollinger_mean_reversion.py"
    text = path.read_text(encoding="utf-8")
    if "def apply(" not in text or "signal" not in text:
        fail("Bollinger strategy contract missing apply() / signal")
        return False
    ok("Bollinger strategy contract present")
    return True


def check_status_metrics_warning() -> bool:
    path = ROOT / "services/mode_controller.py"
    text = path.read_text(encoding="utf-8")
    if "load_runtime_fills_ascending" in text and "limit=1_000_000" in text:
        warn("Status endpoint still rebuilds full metrics history")
        print("  - For UI phase, split /status from /metrics or cache metrics on fill/equity append.")
    else:
        ok("No obvious full-history metrics rebuild in status")
    return True


def check_secrets() -> bool:
    secret_hits = []
    pattern = re.compile(r"(?i)(api[_-]?secret|api[_-]?key|password)\s*=\s*['\"][^'\"]{12,}['\"]")
    for p in iter_py_files():
        rp = rel(p)
        text = p.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                secret_hits.append(f"{rp}:{i}: {line.strip()}")
    if secret_hits:
        fail("Possible hard-coded secrets found")
        for item in secret_hits:
            print("  -", item)
        return False
    ok("No obvious hard-coded secrets")
    return True


def main() -> int:
    print(f"TradingBot repo doctor root: {ROOT}")
    checks = [
        check_critical_files,
        check_ast_parse,
        check_compileall,
        check_legacy_futures_checks,
        check_paper_loop_side_awareness,
        check_live_post_submit_verification,
        check_bollinger_contract,
        check_status_metrics_warning,
        check_secrets,
    ]
    hard_fail = False
    for check in checks:
        result = check()
        hard_fail = hard_fail or (result is False)
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
