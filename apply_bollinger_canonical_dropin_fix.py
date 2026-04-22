"""
apply_bollinger_canonical_dropin_fix.py

Drop-in remediation for the Bollinger Mean Reversion naming mismatch.
Run this from the TradingBot repo root:

    python apply_bollinger_canonical_dropin_fix.py

Canonical public names after this patch:
    length
    std_dev
    min_band_width_pct
    exit_on_mid
    allow_short

Backward-compatible aliases still accepted:
    window -> length
    stddev -> std_dev
    std_mult -> std_dev
    min_bandwidth_pct -> min_band_width_pct
    min_bandwidth -> min_band_width_pct
    min_band_width -> min_band_width_pct

This script patches:
  1. The BollingerMeanReversion strategy class.
  2. Bollinger Pydantic strategy-param schemas/registries.
  3. StrategyEngine.apply() so strategies can normalize params before execution.
  4. /api/run/backtest-style request models so top-level allow_short is accepted.

Backups are written as *.bak_bollinger_canonical before each changed file.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path.cwd()
SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "env", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
}
BACKUP_SUFFIX = ".bak_bollinger_canonical"

BOLLINGER_CLASS = '''class BollingerMeanReversion(BaseStrategy):
    name = "bollinger_mean_reversion"
    display_name = "Bollinger Mean Reversion"
    description = (
        "Enter long when price re-enters from below the lower Bollinger band; "
        "optionally emit short signals when price re-enters from above the upper band."
    )

    # Canonical names used by UI, API schemas, saved configs, and strategy code.
    # Legacy spellings are accepted by normalize_params(), but all new configs
    # should emit only these canonical names.
    default_params = {
        "length": 20,
        "std_dev": 2.0,
        "min_band_width_pct": 0.01,
        "exit_on_mid": True,
        "allow_short": False,
    }

    min_bars = 22

    @classmethod
    def normalize_params(cls, params: dict | None = None) -> dict:
        """Return canonical Bollinger params while accepting legacy aliases."""
        raw = dict(params or {})
        aliases = {
            "window": "length",
            "stddev": "std_dev",
            "std_mult": "std_dev",
            "min_bandwidth_pct": "min_band_width_pct",
            "min_bandwidth": "min_band_width_pct",
            "min_band_width": "min_band_width_pct",
        }

        normalized = dict(cls.default_params)
        for key, value in raw.items():
            normalized[aliases.get(str(key), str(key))] = value

        normalized["length"] = max(2, int(normalized.get("length", cls.default_params["length"])))
        normalized["std_dev"] = float(normalized.get("std_dev", cls.default_params["std_dev"]))
        normalized["min_band_width_pct"] = float(
            normalized.get("min_band_width_pct", cls.default_params["min_band_width_pct"])
        )
        normalized["exit_on_mid"] = bool(normalized.get("exit_on_mid", cls.default_params["exit_on_mid"]))
        normalized["allow_short"] = bool(normalized.get("allow_short", cls.default_params["allow_short"]))
        return normalized

    @classmethod
    def generate(cls, df, params: dict | None = None):
        """Generate Bollinger mean-reversion signals.

        signal:
            1  = long entry / bullish signal
           -1  = short entry / bearish signal when allow_short=True
            0  = no signal
        """
        p = cls.normalize_params(params)
        length = p["length"]
        std_dev = p["std_dev"]
        min_band_width_pct = p["min_band_width_pct"]
        allow_short = p["allow_short"]

        out = df.copy()
        close = out["close"].astype(float)

        mid = close.rolling(length, min_periods=length).mean()
        std = close.rolling(length, min_periods=length).std()
        upper = mid + (std_dev * std)
        lower = mid - (std_dev * std)

        band_width_pct = ((upper - lower) / mid).replace(
            [float("inf"), float("-inf")], 0.0
        ).fillna(0.0)

        prev_close = close.shift(1)
        prev_upper = upper.shift(1)
        prev_lower = lower.shift(1)

        # Long when price was below the lower band and then re-enters above it.
        long_signal = (
            (prev_close < prev_lower)
            & (close >= lower)
            & (band_width_pct >= min_band_width_pct)
        )

        # Optional short when price was above the upper band and then re-enters below it.
        short_signal = (
            (prev_close > prev_upper)
            & (close <= upper)
            & (band_width_pct >= min_band_width_pct)
        )

        out["bb_mid"] = mid
        out["bb_upper"] = upper
        out["bb_lower"] = lower
        out["bb_band_width_pct"] = band_width_pct
        out["signal"] = 0
        out.loc[long_signal, "signal"] = 1
        if allow_short:
            out.loc[short_signal, "signal"] = -1

        return out

    @classmethod
    def apply(cls, df, params: dict | None = None):
        return cls.generate(df, params)
'''


def iter_py_files() -> list[Path]:
    out: list[Path] = []
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name == Path(__file__).name:
            continue
        out.append(path)
    return out


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def write_if_changed(path: Path, old: str, new: str, touched: list[str]) -> None:
    if old == new:
        return
    backup = path.with_suffix(path.suffix + BACKUP_SUFFIX)
    if not backup.exists():
        backup.write_text(old, encoding="utf-8")
    path.write_text(new, encoding="utf-8")
    touched.append(str(path.relative_to(ROOT)))


def replace_top_level_class(text: str, class_name: str, replacement: str) -> tuple[str, bool]:
    pattern = re.compile(rf"^class\s+{re.escape(class_name)}\b[^\n]*:\n", re.M)
    m = pattern.search(text)
    if not m:
        return text, False

    start = m.start()
    next_class = re.search(r"^class\s+\w+\b[^\n]*:\n", text[m.end():], re.M)
    if next_class:
        end = m.end() + next_class.start()
    else:
        end = len(text)

    new = text[:start] + replacement.rstrip() + "\n" + text[end:]
    return new, True


def patch_bollinger_strategy(touched: list[str]) -> None:
    candidates = [p for p in iter_py_files() if "class BollingerMeanReversion" in read(p)]
    if not candidates:
        raise FileNotFoundError("Could not find class BollingerMeanReversion in the repo.")

    for path in candidates:
        old = read(path)
        new, ok = replace_top_level_class(old, "BollingerMeanReversion", BOLLINGER_CLASS)
        if ok:
            write_if_changed(path, old, new, touched)
            return

    raise RuntimeError("Found BollingerMeanReversion text, but could not replace the class safely.")


def ensure_pydantic_import(text: str, name: str) -> str:
    # Handles: from pydantic import BaseModel, Field
    m = re.search(r"^from\s+pydantic\s+import\s+([^\n]+)$", text, re.M)
    if not m:
        return text
    imports = [x.strip() for x in m.group(1).split(",")]
    if name not in imports:
        imports.append(name)
    replacement = "from pydantic import " + ", ".join(imports)
    return text[:m.start()] + replacement + text[m.end():]


def canonicalize_names_in_text(text: str) -> str:
    # String/key-level canonicalization. Do not touch already-canonical names.
    replacements = {
        "stddev": "std_dev",
        "std_mult": "std_dev",
        "min_bandwidth_pct": "min_band_width_pct",
        "min_bandwidth": "min_band_width_pct",
        # min_band_width is accepted legacy, but canonical public percent name is explicit.
        "min_band_width": "min_band_width_pct",
    }
    for old, new in replacements.items():
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])", new, text)
    # Undo accidental double replacement: min_band_width_pct_pct -> min_band_width_pct
    text = text.replace("min_band_width_pct_pct", "min_band_width_pct")
    return text


def class_blocks(text: str):
    matches = list(re.finditer(r"^class\s+(\w+)\b[^\n]*:\n", text, re.M))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        yield m.group(1), m.start(), end, text[m.start():end]


def inject_bollinger_alias_validator(text: str) -> str:
    if "normalize_bollinger_param_aliases" in text:
        return text
    if "BaseModel" not in text:
        return text

    for name, start, end, block in list(class_blocks(text)):
        lower = name.lower() + block.lower()
        is_bollinger_model = "bollinger" in lower and "basemodel" in block.lower()
        has_fields = "std_dev" in block and "min_band_width_pct" in block
        if not (is_bollinger_model and has_fields):
            continue

        text = ensure_pydantic_import(text, "model_validator")
        # Recompute block after import length may have changed.
        for name2, start2, end2, block2 in class_blocks(text):
            if name2 != name:
                continue
            header_end = text.find("\n", start2) + 1
            validator = '''    @model_validator(mode="before")
    @classmethod
    def normalize_bollinger_param_aliases(cls, values):
        if not isinstance(values, dict):
            return values
        values = dict(values)
        aliases = {
            "window": "length",
            "stddev": "std_dev",
            "std_mult": "std_dev",
            "min_bandwidth_pct": "min_band_width_pct",
            "min_bandwidth": "min_band_width_pct",
            "min_band_width": "min_band_width_pct",
        }
        for old_key, new_key in aliases.items():
            if old_key in values and new_key not in values:
                values[new_key] = values.pop(old_key)
        return values

'''
            text = text[:header_end] + validator + text[header_end:]
            break
        break
    return text


def ensure_bollinger_allow_short_field(text: str) -> str:
    # Adds allow_short to Bollinger params Pydantic model when missing.
    for name, start, end, block in list(class_blocks(text)):
        lower = name.lower() + block.lower()
        if "bollinger" not in lower or "basemodel" not in block.lower():
            continue
        if "std_dev" not in block or "min_band_width_pct" not in block:
            continue
        if re.search(r"^\s*allow_short\s*:", block, re.M):
            continue
        insert_pos = None
        for field in ("exit_on_mid", "min_band_width_pct", "std_dev", "length"):
            m = re.search(rf"^\s*{field}\s*:[^\n]*\n", block, re.M)
            if m:
                insert_pos = start + m.end()
                break
        if insert_pos is not None:
            text = text[:insert_pos] + "    allow_short: bool = False\n" + text[insert_pos:]
            break
    return text


def ensure_top_level_allow_short_in_request_models(text: str) -> str:
    # Adds top-level allow_short to API/backtest request models that already expose
    # leverage/slippage/strategy fields. This fixes the observed Swagger error:
    # loc=["body", "allow_short"], extra_forbidden.
    changed = False
    offset = 0
    for name, start, end, block in list(class_blocks(text)):
        block_start = start + offset
        block_end = end + offset
        block = text[block_start:block_end]
        if "BaseModel" not in block:
            continue
        markers = [
            "strategy_name" in block,
            "strategy_params" in block,
            "slippage_bps" in block,
            "position_sizing_mode" in block,
            "initial_balance" in block,
            "leverage" in block,
        ]
        # Require enough markers to avoid touching unrelated models.
        if sum(markers) < 4:
            continue
        if re.search(r"^\s*allow_short\s*:", block, re.M):
            continue

        m = re.search(r"^(\s*)leverage\s*:[^\n]*\n", block, re.M)
        if not m:
            m = re.search(r"^(\s*)market_type\s*:[^\n]*\n", block, re.M)
        if not m:
            continue
        indent = m.group(1)
        insert_at = block_start + m.end()
        line = f"{indent}allow_short: bool = False\n"
        text = text[:insert_at] + line + text[insert_at:]
        offset += len(line)
        changed = True
    return text


def patch_schema_and_request_files(touched: list[str]) -> None:
    for path in iter_py_files():
        old = read(path)
        if not any(s in old for s in (
            "bollinger", "Bollinger", "stddev", "std_mult", "min_bandwidth", "min_band_width",
            "strategy_name", "strategy_params", "slippage_bps", "position_sizing_mode",
        )):
            continue
        new = old
        new = canonicalize_names_in_text(new)
        new = inject_bollinger_alias_validator(new)
        new = ensure_bollinger_allow_short_field(new)
        new = ensure_top_level_allow_short_in_request_models(new)
        write_if_changed(path, old, new, touched)


def patch_strategy_engine_apply(touched: list[str]) -> None:
    for path in iter_py_files():
        old = read(path)
        if "class StrategyEngine" not in old or "def apply" not in old:
            continue
        if "strategy.normalize_params" in old:
            return

        lines = old.splitlines(True)
        new_lines = []
        inserted = False
        inside_apply = False
        apply_indent = ""
        for line in lines:
            new_lines.append(line)
            if re.match(r"^(\s*)def\s+apply\s*\(", line):
                inside_apply = True
                apply_indent = re.match(r"^(\s*)", line).group(1)
                continue
            if inside_apply and not inserted:
                # Insert after strategy resolution line.
                if re.search(r"\bstrategy\s*=", line):
                    indent = re.match(r"^(\s*)", line).group(1)
                    new_lines.append(f"{indent}strategy_params = dict(strategy_params or {{}})\n")
                    new_lines.append(f"{indent}if hasattr(strategy, \"normalize_params\"):\n")
                    new_lines.append(f"{indent}    strategy_params = strategy.normalize_params(strategy_params)\n")
                    inserted = True
                    continue
            if inside_apply and re.match(rf"^{apply_indent}def\s+", line):
                inside_apply = False
        if inserted:
            write_if_changed(path, old, "".join(new_lines), touched)
            return


def main() -> None:
    touched: list[str] = []
    patch_bollinger_strategy(touched)
    patch_schema_and_request_files(touched)
    patch_strategy_engine_apply(touched)

    print("Bollinger canonical naming patch complete.")
    print("Canonical params: length, std_dev, min_band_width_pct, exit_on_mid, allow_short")
    print("Top-level API allow_short is also restored for backtest-style request models.")
    if touched:
        print("Modified files:")
        for path in touched:
            print(f"  - {path}")
    else:
        print("No files changed.")
    print("\nNext commands:")
    print("  python -m compileall -q core services routers app.py")
    print("  restart uvicorn")


if __name__ == "__main__":
    main()
