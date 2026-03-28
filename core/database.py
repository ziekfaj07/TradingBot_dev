import csv
import io
import json
import os
import re
import sqlite3
import time
from typing import Any, Optional

DB_PATH = os.getenv("TRADINGBOT_DB_PATH", "database/tradingbot.db")


# ============================================================
# Core connection helpers
# ============================================================

def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _safe_table_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", str(name or ""))
    if not cleaned:
        raise ValueError("Invalid table name")
    return cleaned


def _json_dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: Optional[str], default: Any = None) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    return dict(row)


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


# ============================================================
# Legacy market-data storage
# ============================================================

def create_table(symbol: str) -> None:
    table_name = _safe_table_name(symbol)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            timestamp INTEGER PRIMARY KEY,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL
        )
        """
    )
    conn.commit()
    conn.close()


# ============================================================
# v0.5.1 Persistent DB schema
# ============================================================

def init_runtime_db() -> None:
    """
    Backward-compatible bootstrap.

    Keeps the existing runtime_* tables your current paper-trading flow expects,
    while also creating the new v0.5 persistence tables:
      - runs
      - fills
      - equity_snapshots
    """
    conn = get_connection()
    cur = conn.cursor()

    # ----------------------------
    # Existing runtime tables
    # ----------------------------
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_runs (
            run_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            state TEXT NOT NULL,
            started_at REAL,
            stopped_at REAL,
            last_error TEXT,
            config_json TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_snapshots (
            run_id TEXT PRIMARY KEY,
            state_json TEXT,
            latest_bar_json TEXT,
            bars_json TEXT,
            last_processed_bar_ts INTEGER,
            last_signal INTEGER,
            trade_id INTEGER,
            updated_at REAL NOT NULL,
            FOREIGN KEY(run_id) REFERENCES runtime_runs(run_id) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            type TEXT NOT NULL,
            side TEXT NOT NULL,
            price REAL NOT NULL,
            qty REAL NOT NULL,
            fee REAL NOT NULL,
            equity_after REAL NOT NULL,
            entry_price REAL,
            exit_price REAL,
            trade_id INTEGER,
            pnl REAL,
            created_at REAL NOT NULL,
            FOREIGN KEY(run_id) REFERENCES runtime_runs(run_id) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runtime_fills_run_id_id
        ON runtime_fills(run_id, id)
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_equity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            equity REAL NOT NULL,
            price REAL,
            cash REAL,
            position_qty REAL,
            created_at REAL NOT NULL,
            FOREIGN KEY(run_id) REFERENCES runtime_runs(run_id) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runtime_equity_run_id_id
        ON runtime_equity_snapshots(run_id, id)
        """
    )

    # ----------------------------
    # New v0.5 canonical tables
    # ----------------------------
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            run_label TEXT,
            mode TEXT NOT NULL,
            state TEXT NOT NULL,
            symbol TEXT NOT NULL,
            interval TEXT,
            market_type TEXT,
            strategy_name TEXT,
            strategy_params_json TEXT,
            config_json TEXT,
            started_at REAL,
            stopped_at REAL,
            last_error TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runs_mode_state
        ON runs(mode, state)
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runs_symbol_started_at
        ON runs(symbol, started_at)
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            fill_type TEXT NOT NULL,
            side TEXT NOT NULL,
            price REAL NOT NULL,
            qty REAL NOT NULL,
            fee REAL NOT NULL DEFAULT 0,
            equity_after REAL,
            entry_price REAL,
            exit_price REAL,
            trade_id INTEGER,
            pnl REAL,
            meta_json TEXT,
            created_at REAL NOT NULL,
            FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fills_run_id_id
        ON fills(run_id, id)
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fills_run_id_trade_id
        ON fills(run_id, trade_id)
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS equity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            equity REAL NOT NULL,
            price REAL,
            cash REAL,
            position_qty REAL,
            drawdown REAL,
            meta_json TEXT,
            created_at REAL NOT NULL,
            FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_equity_snapshots_run_id_id
        ON equity_snapshots(run_id, id)
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_equity_snapshots_run_id_timestamp
        ON equity_snapshots(run_id, timestamp)
        """
    )

    conn.commit()
    conn.close()


# ============================================================
# Runtime helpers used by current paper-trading flow
# ============================================================

def upsert_runtime_run(
    run_id: str,
    mode: str,
    state: str,
    started_at: Optional[float],
    stopped_at: Optional[float],
    last_error: Optional[str],
    config: dict,
) -> None:
    now = time.time()
    conn = get_connection()
    cur = conn.cursor()

    config = dict(config or {})

    cur.execute(
        """
        INSERT INTO runtime_runs (
            run_id, mode, state, started_at, stopped_at, last_error, config_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            mode = excluded.mode,
            state = excluded.state,
            started_at = excluded.started_at,
            stopped_at = excluded.stopped_at,
            last_error = excluded.last_error,
            config_json = excluded.config_json,
            updated_at = excluded.updated_at
        """,
        (
            run_id,
            mode,
            state,
            started_at,
            stopped_at,
            last_error,
            _json_dumps(config),
            now,
            now,
        ),
    )

    cur.execute(
        """
        INSERT INTO runs (
            run_id,
            run_label,
            mode,
            state,
            symbol,
            interval,
            market_type,
            strategy_name,
            strategy_params_json,
            config_json,
            started_at,
            stopped_at,
            last_error,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            run_label = excluded.run_label,
            mode = excluded.mode,
            state = excluded.state,
            symbol = excluded.symbol,
            interval = excluded.interval,
            market_type = excluded.market_type,
            strategy_name = excluded.strategy_name,
            strategy_params_json = excluded.strategy_params_json,
            config_json = excluded.config_json,
            started_at = excluded.started_at,
            stopped_at = excluded.stopped_at,
            last_error = excluded.last_error,
            updated_at = excluded.updated_at
        """,
        (
            run_id,
            run_id,
            mode,
            state,
            str(config.get("symbol", "")),
            config.get("interval"),
            config.get("market_type"),
            config.get("strategy_name"),
            _json_dumps(config.get("strategy_params", {})),
            _json_dumps(config),
            started_at,
            stopped_at,
            last_error,
            now,
            now,
        ),
    )

    conn.commit()
    conn.close()


def save_runtime_snapshot(
    run_id: str,
    state_dict: Optional[dict],
    latest_bar: Optional[dict],
    bars: Optional[list[dict]],
    last_processed_bar_ts: Optional[int],
    last_signal: Optional[int],
    trade_id: Optional[int],
) -> None:
    now = time.time()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO runtime_snapshots (
            run_id,
            state_json,
            latest_bar_json,
            bars_json,
            last_processed_bar_ts,
            last_signal,
            trade_id,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            state_json = excluded.state_json,
            latest_bar_json = excluded.latest_bar_json,
            bars_json = excluded.bars_json,
            last_processed_bar_ts = excluded.last_processed_bar_ts,
            last_signal = excluded.last_signal,
            trade_id = excluded.trade_id,
            updated_at = excluded.updated_at
        """,
        (
            run_id,
            _json_dumps(state_dict),
            _json_dumps(latest_bar),
            _json_dumps(bars or []),
            last_processed_bar_ts,
            _safe_int(last_signal),
            _safe_int(trade_id),
            now,
        ),
    )

    conn.commit()
    conn.close()


def insert_runtime_fill(run_id: str, fill: dict) -> int:
    now = time.time()
    fill = dict(fill or {})

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO runtime_fills (
            run_id,
            timestamp,
            type,
            side,
            price,
            qty,
            fee,
            equity_after,
            entry_price,
            exit_price,
            trade_id,
            pnl,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            str(fill.get("timestamp", "")),
            str(fill.get("type", "")),
            str(fill.get("side", "")),
            _safe_float(fill.get("price")),
            _safe_float(fill.get("qty")),
            _safe_float(fill.get("fee")),
            _safe_float(fill.get("equity_after")),
            fill.get("entry_price"),
            fill.get("exit_price"),
            fill.get("trade_id"),
            fill.get("pnl"),
            now,
        ),
    )
    runtime_fill_id = _safe_int(cur.lastrowid)

    cur.execute(
        """
        INSERT INTO fills (
            run_id,
            timestamp,
            fill_type,
            side,
            price,
            qty,
            fee,
            equity_after,
            entry_price,
            exit_price,
            trade_id,
            pnl,
            meta_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            str(fill.get("timestamp", "")),
            str(fill.get("type", "")),
            str(fill.get("side", "")),
            _safe_float(fill.get("price")),
            _safe_float(fill.get("qty")),
            _safe_float(fill.get("fee")),
            fill.get("equity_after"),
            fill.get("entry_price"),
            fill.get("exit_price"),
            fill.get("trade_id"),
            fill.get("pnl"),
            _json_dumps(fill),
            now,
        ),
    )

    conn.commit()
    conn.close()
    return runtime_fill_id


def load_runtime_fills(
    run_id: str,
    limit: int = 500,
    offset: int = 0,
    ascending: bool = False,
) -> list[dict]:
    order = "ASC" if ascending else "DESC"

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT
            id,
            run_id,
            timestamp,
            type,
            side,
            price,
            qty,
            fee,
            equity_after,
            entry_price,
            exit_price,
            trade_id,
            pnl,
            created_at
        FROM runtime_fills
        WHERE run_id = ?
        ORDER BY id {order}
        LIMIT ? OFFSET ?
        """,
        (run_id, int(limit), int(offset)),
    )
    rows = cur.fetchall()
    conn.close()
    return _rows_to_dicts(rows)


def load_runtime_fills_ascending(run_id: str, limit: int = 500, offset: int = 0) -> list[dict]:
    return load_runtime_fills(run_id=run_id, limit=limit, offset=offset, ascending=True)


def count_runtime_fills(run_id: str) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM runtime_fills WHERE run_id = ?",
        (run_id,),
    )
    row = cur.fetchone()
    conn.close()
    if row is None:
        return 0
    return _safe_int(row["cnt"])


def insert_runtime_equity_snapshot(
    run_id: str,
    point: Optional[dict] = None,
    **kwargs: Any,
) -> int:
    """
    Backward-compatible helper.

    Supports both:
        insert_runtime_equity_snapshot(run_id, point={...})

    and the older call style:
        insert_runtime_equity_snapshot(
            run_id,
            ts=...,
            balance=...,
            equity=...,
            market_price=...,
            position_qty=...,
            side=...,
            unrealized_pnl=...,
            realized_pnl=...,
            drawdown_pct=...,
        )
    """
    now = time.time()

    if point is None:
        timestamp_value = kwargs.get("ts", kwargs.get("timestamp", ""))
        equity_value = kwargs.get("equity", kwargs.get("balance", 0.0))
        price_value = kwargs.get("market_price", kwargs.get("price"))
        cash_value = kwargs.get("balance", kwargs.get("cash"))
        position_qty_value = kwargs.get("position_qty", 0.0)
        drawdown_value = kwargs.get("drawdown_pct", kwargs.get("drawdown"))

        point = {
            "timestamp": timestamp_value,
            "equity": equity_value,
            "price": price_value,
            "cash": cash_value,
            "position_qty": position_qty_value,
            "drawdown": drawdown_value,
            "side": kwargs.get("side"),
            "unrealized_pnl": kwargs.get("unrealized_pnl"),
            "realized_pnl": kwargs.get("realized_pnl"),
        }
    else:
        point = dict(point)

    timestamp = str(point.get("timestamp", ""))
    equity = _safe_float(point.get("equity"))
    price = point.get("price")
    cash = point.get("cash")
    position_qty = point.get("position_qty")

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO runtime_equity_snapshots (
            run_id,
            timestamp,
            equity,
            price,
            cash,
            position_qty,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            timestamp,
            equity,
            price,
            cash,
            position_qty,
            now,
        ),
    )
    runtime_id = _safe_int(cur.lastrowid)

    cur.execute(
        """
        INSERT INTO equity_snapshots (
            run_id,
            timestamp,
            equity,
            price,
            cash,
            position_qty,
            drawdown,
            meta_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            timestamp,
            equity,
            price,
            cash,
            position_qty,
            point.get("drawdown"),
            _json_dumps(point),
            now,
        ),
    )

    conn.commit()
    conn.close()
    return runtime_id


def load_runtime_equity_snapshots(
    run_id: str,
    limit: int = 5000,
    offset: int = 0,
    ascending: bool = True,
) -> list[dict]:
    order = "ASC" if ascending else "DESC"

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT
            id,
            run_id,
            timestamp,
            equity,
            price,
            cash,
            position_qty,
            created_at
        FROM runtime_equity_snapshots
        WHERE run_id = ?
        ORDER BY id {order}
        LIMIT ? OFFSET ?
        """,
        (run_id, int(limit), int(offset)),
    )
    rows = cur.fetchall()
    conn.close()
    return _rows_to_dicts(rows)


def count_runtime_equity_snapshots(run_id: str) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM runtime_equity_snapshots WHERE run_id = ?",
        (run_id,),
    )
    row = cur.fetchone()
    conn.close()
    if row is None:
        return 0
    return _safe_int(row["cnt"])


def get_latest_paper_run() -> Optional[dict]:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM runtime_runs
        WHERE mode = 'paper'
        ORDER BY COALESCE(updated_at, created_at) DESC
        LIMIT 1
        """
    )
    run_row = cur.fetchone()
    if not run_row:
        conn.close()
        return None

    run_id = run_row["run_id"]

    cur.execute(
        "SELECT * FROM runtime_snapshots WHERE run_id = ? LIMIT 1",
        (run_id,),
    )
    snapshot_row = cur.fetchone()

    cur.execute(
        """
        SELECT
            id,
            run_id,
            timestamp,
            type,
            side,
            price,
            qty,
            fee,
            equity_after,
            entry_price,
            exit_price,
            trade_id,
            pnl,
            created_at
        FROM runtime_fills
        WHERE run_id = ?
        ORDER BY id ASC
        LIMIT 5000
        """,
        (run_id,),
    )
    fill_rows = cur.fetchall()

    cur.execute(
        """
        SELECT
            id,
            run_id,
            timestamp,
            equity,
            price,
            cash,
            position_qty,
            created_at
        FROM runtime_equity_snapshots
        WHERE run_id = ?
        ORDER BY id ASC
        LIMIT 5000
        """,
        (run_id,),
    )
    equity_rows = cur.fetchall()

    conn.close()

    run = dict(run_row)
    run["config"] = _json_loads(run.get("config_json"), default={}) or {}

    if snapshot_row:
        snapshot = dict(snapshot_row)
        run["snapshot"] = {
            "state": _json_loads(snapshot.get("state_json"), default=None),
            "latest_bar": _json_loads(snapshot.get("latest_bar_json"), default=None),
            "bars": _json_loads(snapshot.get("bars_json"), default=[]),
            "last_processed_bar_ts": snapshot.get("last_processed_bar_ts"),
            "last_signal": _safe_int(snapshot.get("last_signal")),
            "trade_id": _safe_int(snapshot.get("trade_id")),
            "updated_at": snapshot.get("updated_at"),
        }
    else:
        run["snapshot"] = None

    run["fills"] = [dict(r) for r in fill_rows]
    run["equity_points"] = [dict(r) for r in equity_rows]

    return run


def update_run_state(
    run_id: str,
    state: str,
    stopped_at: Optional[float] = None,
    last_error: Optional[str] = None,
) -> None:
    now = time.time()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE runtime_runs
        SET state = ?, stopped_at = ?, last_error = ?, updated_at = ?
        WHERE run_id = ?
        """,
        (state, stopped_at, last_error, now, run_id),
    )

    cur.execute(
        """
        UPDATE runs
        SET state = ?, stopped_at = ?, last_error = ?, updated_at = ?
        WHERE run_id = ?
        """,
        (state, stopped_at, last_error, now, run_id),
    )

    conn.commit()
    conn.close()


def delete_runtime_run(run_id: str) -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM runtime_equity_snapshots WHERE run_id = ?", (run_id,))
    cur.execute("DELETE FROM runtime_fills WHERE run_id = ?", (run_id,))
    cur.execute("DELETE FROM runtime_snapshots WHERE run_id = ?", (run_id,))
    cur.execute("DELETE FROM runtime_runs WHERE run_id = ?", (run_id,))

    cur.execute("DELETE FROM equity_snapshots WHERE run_id = ?", (run_id,))
    cur.execute("DELETE FROM fills WHERE run_id = ?", (run_id,))
    cur.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))

    conn.commit()
    conn.close()


def export_runtime_fills_csv(run_id: str) -> str:
    rows = load_runtime_fills(run_id=run_id, limit=1_000_000, offset=0, ascending=True)
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(
        [
            "id",
            "run_id",
            "timestamp",
            "type",
            "side",
            "price",
            "qty",
            "fee",
            "equity_after",
            "entry_price",
            "exit_price",
            "trade_id",
            "pnl",
            "created_at",
        ]
    )

    for row in rows:
        writer.writerow(
            [
                row.get("id"),
                row.get("run_id"),
                row.get("timestamp"),
                row.get("type"),
                row.get("side"),
                row.get("price"),
                row.get("qty"),
                row.get("fee"),
                row.get("equity_after"),
                row.get("entry_price"),
                row.get("exit_price"),
                row.get("trade_id"),
                row.get("pnl"),
                row.get("created_at"),
            ]
        )

    return output.getvalue()


def export_runtime_equity_csv(run_id: str) -> str:
    rows = load_runtime_equity_snapshots(run_id=run_id, limit=1_000_000, offset=0, ascending=True)
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(
        [
            "id",
            "run_id",
            "timestamp",
            "equity",
            "price",
            "cash",
            "position_qty",
            "created_at",
        ]
    )

    for row in rows:
        writer.writerow(
            [
                row.get("id"),
                row.get("run_id"),
                row.get("timestamp"),
                row.get("equity"),
                row.get("price"),
                row.get("cash"),
                row.get("position_qty"),
                row.get("created_at"),
            ]
        )

    return output.getvalue()


# ============================================================
# v0.5 canonical query helpers
# ============================================================

def get_run(run_id: str) -> Optional[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM runs WHERE run_id = ? LIMIT 1", (run_id,))
    row = cur.fetchone()
    conn.close()

    data = _row_to_dict(row)
    if not data:
        return None

    data["strategy_params"] = _json_loads(data.get("strategy_params_json"), default={}) or {}
    data["config"] = _json_loads(data.get("config_json"), default={}) or {}
    return data


def list_runs(limit: int = 100, offset: int = 0) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM runs
        ORDER BY COALESCE(started_at, created_at) DESC
        LIMIT ? OFFSET ?
        """,
        (int(limit), int(offset)),
    )
    rows = cur.fetchall()
    conn.close()

    result = []
    for row in rows:
        item = dict(row)
        item["strategy_params"] = _json_loads(item.get("strategy_params_json"), default={}) or {}
        item["config"] = _json_loads(item.get("config_json"), default={}) or {}
        result.append(item)
    return result


def get_fills_by_run_id(run_id: str, limit: int = 5000, offset: int = 0) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM fills
        WHERE run_id = ?
        ORDER BY id ASC
        LIMIT ? OFFSET ?
        """,
        (run_id, int(limit), int(offset)),
    )
    rows = cur.fetchall()
    conn.close()
    return _rows_to_dicts(rows)


def get_equity_by_run_id(run_id: str, limit: int = 5000, offset: int = 0) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM equity_snapshots
        WHERE run_id = ?
        ORDER BY id ASC
        LIMIT ? OFFSET ?
        """,
        (run_id, int(limit), int(offset)),
    )
    rows = cur.fetchall()
    conn.close()
    return _rows_to_dicts(rows)