import asyncio
import csv
import io
import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

DB_PATH = os.getenv("TRADINGBOT_DB_PATH", "database/tradingbot.db")


# ============================================================
# Core connection helpers
# ============================================================

def get_connection() -> sqlite3.Connection:
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -20000")

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


def _coerce_limit(
    value: Any,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    parsed = _safe_int(value, default)
    return max(minimum, min(parsed, maximum))


def _coerce_offset(value: Any, default: int = 0) -> int:
    parsed = _safe_int(value, default)
    return max(0, parsed)


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    return dict(row)


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


# ============================================================
# v0.5.4 Buffered runtime writes
# ============================================================

@dataclass
class _RuntimeWriteBuffer:
    fills: list[dict] = field(default_factory=list)
    equity_points: list[dict] = field(default_factory=list)
    last_flush_monotonic: float = field(default_factory=time.monotonic)


_RUNTIME_WRITE_LOCK = threading.Lock()
_RUNTIME_WRITE_BUFFERS: dict[str, _RuntimeWriteBuffer] = {}

_FILL_BATCH_SIZE = _coerce_limit(
    os.getenv("TRADINGBOT_DB_FILL_BATCH_SIZE"),
    default=25,
    minimum=1,
    maximum=10_000,
)

_EQUITY_BATCH_SIZE = _coerce_limit(
    os.getenv("TRADINGBOT_DB_EQUITY_BATCH_SIZE"),
    default=100,
    minimum=1,
    maximum=50_000,
)

_DB_FLUSH_INTERVAL_SECONDS = max(
    0.1,
    _safe_float(os.getenv("TRADINGBOT_DB_FLUSH_INTERVAL_SECONDS"), 1.0),
)


def _get_runtime_buffer(run_id: str) -> _RuntimeWriteBuffer:
    bucket = _RUNTIME_WRITE_BUFFERS.get(run_id)
    if bucket is None:
        bucket = _RuntimeWriteBuffer()
        _RUNTIME_WRITE_BUFFERS[run_id] = bucket
    return bucket


def _normalize_fill_payload(run_id: str, fill: dict, now: Optional[float] = None) -> dict:
    payload = dict(fill or {})
    created_at = now if now is not None else time.time()
    return {
        "run_id": run_id,
        "timestamp": str(payload.get("timestamp", "")),
        "type": str(payload.get("type", "")),
        "side": str(payload.get("side", "")),
        "price": _safe_float(payload.get("price")),
        "qty": _safe_float(payload.get("qty")),
        "fee": _safe_float(payload.get("fee")),
        "equity_after": _safe_float(payload.get("equity_after")),
        "entry_price": payload.get("entry_price"),
        "exit_price": payload.get("exit_price"),
        "trade_id": payload.get("trade_id"),
        "pnl": payload.get("pnl"),
        "created_at": created_at,
        "meta_json": _json_dumps(payload),
    }


def _normalize_equity_payload(run_id: str, point: dict, now: Optional[float] = None) -> dict:
    payload = dict(point or {})
    created_at = now if now is not None else time.time()
    return {
        "run_id": run_id,
        "timestamp": str(payload.get("timestamp", "")),
        "equity": _safe_float(payload.get("equity")),
        "price": payload.get("price"),
        "cash": payload.get("cash"),
        "position_qty": payload.get("position_qty"),
        "drawdown": payload.get("drawdown"),
        "created_at": created_at,
        "meta_json": _json_dumps(payload),
    }


def _insert_runtime_fills_batch_conn(
    conn: sqlite3.Connection,
    rows: list[dict],
) -> int:
    if not rows:
        return 0

    cur = conn.cursor()

    cur.executemany(
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
        [
            (
                row["run_id"],
                row["timestamp"],
                row["type"],
                row["side"],
                row["price"],
                row["qty"],
                row["fee"],
                row["equity_after"],
                row["entry_price"],
                row["exit_price"],
                row["trade_id"],
                row["pnl"],
                row["created_at"],
            )
            for row in rows
        ],
    )

    cur.executemany(
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
        [
            (
                row["run_id"],
                row["timestamp"],
                row["type"],
                row["side"],
                row["price"],
                row["qty"],
                row["fee"],
                row["equity_after"],
                row["entry_price"],
                row["exit_price"],
                row["trade_id"],
                row["pnl"],
                row["meta_json"],
                row["created_at"],
            )
            for row in rows
        ],
    )

    return len(rows)


def _insert_runtime_equity_batch_conn(
    conn: sqlite3.Connection,
    rows: list[dict],
) -> int:
    if not rows:
        return 0

    cur = conn.cursor()

    cur.executemany(
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
        [
            (
                row["run_id"],
                row["timestamp"],
                row["equity"],
                row["price"],
                row["cash"],
                row["position_qty"],
                row["created_at"],
            )
            for row in rows
        ],
    )

    cur.executemany(
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
        [
            (
                row["run_id"],
                row["timestamp"],
                row["equity"],
                row["price"],
                row["cash"],
                row["position_qty"],
                row["drawdown"],
                row["meta_json"],
                row["created_at"],
            )
            for row in rows
        ],
    )

    return len(rows)


def flush_runtime_write_buffers(run_id: Optional[str] = None) -> dict[str, int]:
    with _RUNTIME_WRITE_LOCK:
        if run_id is None:
            targets = list(_RUNTIME_WRITE_BUFFERS.keys())
        else:
            targets = [run_id] if run_id in _RUNTIME_WRITE_BUFFERS else []

        if not targets:
            return {"fills": 0, "equity_points": 0}

        drained: dict[str, tuple[list[dict], list[dict]]] = {}
        for rid in targets:
            bucket = _RUNTIME_WRITE_BUFFERS.get(rid)
            if bucket is None:
                continue

            fills = bucket.fills[:]
            equity_points = bucket.equity_points[:]

            if fills or equity_points:
                drained[rid] = (fills, equity_points)

            bucket.fills.clear()
            bucket.equity_points.clear()
            bucket.last_flush_monotonic = time.monotonic()

            if not bucket.fills and not bucket.equity_points:
                _RUNTIME_WRITE_BUFFERS.pop(rid, None)

    if not drained:
        return {"fills": 0, "equity_points": 0}

    conn = get_connection()
    inserted_fills = 0
    inserted_equity = 0

    try:
        for fills, equity_points in drained.values():
            inserted_fills += _insert_runtime_fills_batch_conn(conn, fills)
            inserted_equity += _insert_runtime_equity_batch_conn(conn, equity_points)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "fills": inserted_fills,
        "equity_points": inserted_equity,
    }


async def flush_runtime_write_buffers_async(run_id: Optional[str] = None) -> dict[str, int]:
    return await asyncio.to_thread(flush_runtime_write_buffers, run_id)


def _buffer_runtime_fill(run_id: str, fill: dict) -> None:
    normalized = _normalize_fill_payload(run_id=run_id, fill=fill)
    with _RUNTIME_WRITE_LOCK:
        bucket = _get_runtime_buffer(run_id)
        bucket.fills.append(normalized)
        should_flush = (
            len(bucket.fills) >= _FILL_BATCH_SIZE
            or (time.monotonic() - bucket.last_flush_monotonic) >= _DB_FLUSH_INTERVAL_SECONDS
        )

    if should_flush:
        flush_runtime_write_buffers(run_id)


def _buffer_runtime_equity_point(run_id: str, point: dict) -> None:
    normalized = _normalize_equity_payload(run_id=run_id, point=point)
    with _RUNTIME_WRITE_LOCK:
        bucket = _get_runtime_buffer(run_id)
        bucket.equity_points.append(normalized)
        should_flush = (
            len(bucket.equity_points) >= _EQUITY_BATCH_SIZE
            or (time.monotonic() - bucket.last_flush_monotonic) >= _DB_FLUSH_INTERVAL_SECONDS
        )

    if should_flush:
        flush_runtime_write_buffers(run_id)


async def insert_runtime_fill_async(run_id: str, fill: dict) -> int:
    return await asyncio.to_thread(insert_runtime_fill, run_id, fill)


async def insert_runtime_equity_snapshot_async(
    run_id: str,
    point: Optional[dict] = None,
    **kwargs: Any,
) -> int:
    return await asyncio.to_thread(
        insert_runtime_equity_snapshot,
        run_id,
        point,
        **kwargs,
    )

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

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runtime_runs_mode_updated_at
        ON runtime_runs(mode, updated_at DESC)
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runtime_runs_mode_created_at
        ON runtime_runs(mode, created_at DESC)
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runtime_fills_run_id_created_at
        ON runtime_fills(run_id, created_at)
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runtime_equity_run_id_created_at
        ON runtime_equity_snapshots(run_id, created_at)
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runs_mode_updated_at
        ON runs(mode, updated_at DESC)
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runs_symbol_updated_at
        ON runs(symbol, updated_at DESC)
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fills_run_id_created_at
        ON fills(run_id, created_at)
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_equity_snapshots_run_id_created_at
        ON equity_snapshots(run_id, created_at)
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
    flush_runtime_write_buffers(run_id)

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
    flush_runtime_write_buffers(run_id)

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
    _buffer_runtime_fill(run_id, fill)
    return 0


def load_runtime_fills(
    run_id: str,
    limit: int = 500,
    offset: int = 0,
    ascending: bool = False,
) -> list[dict]:
    flush_runtime_write_buffers(run_id)    

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
        (
            run_id,
            _coerce_limit(limit, default=500, minimum=1, maximum=1_000_000),
            _coerce_offset(offset, default=0),
        ),
    )
    rows = cur.fetchall()
    conn.close()
    return _rows_to_dicts(rows)


def load_runtime_fills_ascending(run_id: str, limit: int = 500, offset: int = 0) -> list[dict]:
    return load_runtime_fills(run_id=run_id, limit=limit, offset=offset, ascending=True)


def count_runtime_fills(run_id: str) -> int:
    flush_runtime_write_buffers(run_id)

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
    if point is None:
        point = {}

    if kwargs:
        point = dict(point)
        point.update(kwargs)

    _buffer_runtime_equity_point(run_id, dict(point))
    return 0


def load_runtime_equity_snapshots(
    run_id: str,
    limit: int = 5000,
    offset: int = 0,
    ascending: bool = True,
) -> list[dict]:
    flush_runtime_write_buffers(run_id)    

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
        (
            run_id,
            _coerce_limit(limit, default=5000, minimum=1, maximum=1_000_000),
            _coerce_offset(offset, default=0),
        ),        
    )
    rows = cur.fetchall()
    conn.close()
    return _rows_to_dicts(rows)


def count_runtime_equity_snapshots(run_id: str) -> int:
    flush_runtime_write_buffers(run_id)

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
    flush_runtime_write_buffers()

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


def load_persisted_fills(
    run_id: str,
    limit: int = 500,
    offset: int = 0,
    ascending: bool = False,
) -> list[dict]:
    flush_runtime_write_buffers(run_id)    

    order = "ASC" if ascending else "DESC"

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT
            id,
            run_id,
            timestamp,
            fill_type AS type,
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
        FROM fills
        WHERE run_id = ?
        ORDER BY id {order}
        LIMIT ? OFFSET ?
        """,
        (
            run_id,
            _coerce_limit(limit, default=500, minimum=1, maximum=1_000_000),
            _coerce_offset(offset, default=0),
        ),        
    )
    rows = cur.fetchall()
    conn.close()
    return _rows_to_dicts(rows)


def load_persisted_equity_snapshots(
    run_id: str,
    limit: int = 5000,
    offset: int = 0,
    ascending: bool = True,
) -> list[dict]:
    flush_runtime_write_buffers(run_id)    

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
            drawdown,
            meta_json,
            created_at
        FROM equity_snapshots
        WHERE run_id = ?
        ORDER BY id {order}
        LIMIT ? OFFSET ?
        """,
        (
            run_id,
            _coerce_limit(limit, default=5000, minimum=1, maximum=1_000_000),
            _coerce_offset(offset, default=0),
        ),        
    )
    rows = cur.fetchall()
    conn.close()

    normalized: list[dict] = []

    for row in rows:
        raw = dict(row)
        meta = _json_loads(raw.get("meta_json"), default={}) or {}
        if not isinstance(meta, dict):
            meta = {}

        ts_raw = meta.get("ts", meta.get("timestamp", raw.get("timestamp")))
        ts_value: Any = ts_raw
        if ts_raw is not None:
            try:
                ts_value = int(ts_raw)
            except (TypeError, ValueError):
                ts_value = ts_raw        
        try:
            ts_value = int(ts_value)
        except Exception:
            pass

        point = dict(meta)
        point.update(
            {
                "id": raw.get("id"),
                "run_id": raw.get("run_id"),
                "timestamp": raw.get("timestamp"),
                "ts": ts_value,
                "balance": _safe_float(point.get("balance", raw.get("cash"))),
                "equity": _safe_float(point.get("equity", raw.get("equity"))),
                "market_price": (
                    _safe_float(raw.get("price"))
                    if raw.get("price") is not None
                    else point.get("market_price")
                ),
                "position_qty": _safe_float(
                    point.get("position_qty", raw.get("position_qty"))
                ),
                "drawdown_pct": _safe_float(
                    point.get(
                        "drawdown_pct",
                        point.get("drawdown", raw.get("drawdown")),
                    )
                ),
                "side": point.get("side"),
                "unrealized_pnl": _safe_float(point.get("unrealized_pnl")),
                "realized_pnl": _safe_float(point.get("realized_pnl")),
                "created_at": raw.get("created_at"),
            }
        )
        normalized.append(point)

    return normalized


def _hydrate_persisted_run_row(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None

    item = dict(row)
    item["config"] = _json_loads(item.get("config_json"), default={}) or {}
    item["strategy_params"] = (
        _json_loads(item.get("strategy_params_json"), default={}) or {}
    )
    item["fill_count"] = _safe_int(item.get("fill_count"))
    item["equity_point_count"] = _safe_int(item.get("equity_point_count"))

    latest_equity = item.get("latest_equity")
    if latest_equity is not None:
        item["latest_equity"] = _safe_float(latest_equity)

    return item


def list_persisted_runs(
    limit: int = 100,
    offset: int = 0,
    mode: Optional[str] = None,
    symbol: Optional[str] = None,
    state: Optional[str] = None,
    strategy_name: Optional[str] = None,
) -> dict:
    flush_runtime_write_buffers()

    safe_limit = _coerce_limit(limit, default=100, minimum=1, maximum=1000)
    safe_offset = _coerce_offset(offset, default=0)

    where = []
    params: list = []

    if mode:
        where.append("r.mode = ?")
        params.append(mode)

    if symbol:
        where.append("r.symbol = ?")
        params.append(symbol)

    if state:
        where.append("r.state = ?")
        params.append(state)

    if strategy_name:
        where.append("r.strategy_name = ?")
        params.append(strategy_name)

    where_sql = ""
    if where:
        where_sql = "WHERE " + " AND ".join(where)

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        f"""
        SELECT COUNT(*) AS cnt
        FROM runs r
        {where_sql}
        """,
        params,
    )
    total_row = cur.fetchone()
    total = _safe_int(total_row["cnt"]) if total_row else 0

    cur.execute(
        f"""
        SELECT
            r.run_id,
            r.run_label,
            r.mode,
            r.state,
            r.symbol,
            r.interval,
            r.market_type,
            r.strategy_name,
            r.strategy_params_json,
            r.config_json,
            r.started_at,
            r.stopped_at,
            r.last_error,
            r.created_at,
            r.updated_at,
            (
                SELECT COUNT(*)
                FROM fills f
                WHERE f.run_id = r.run_id
            ) AS fill_count,
            (
                SELECT COUNT(*)
                FROM equity_snapshots e
                WHERE e.run_id = r.run_id
            ) AS equity_point_count,
            (
                SELECT e.equity
                FROM equity_snapshots e
                WHERE e.run_id = r.run_id
                ORDER BY e.id DESC
                LIMIT 1
            ) AS latest_equity,
            (
                SELECT e.timestamp
                FROM equity_snapshots e
                WHERE e.run_id = r.run_id
                ORDER BY e.id DESC
                LIMIT 1
            ) AS latest_equity_timestamp
        FROM runs r
        {where_sql}
        ORDER BY COALESCE(r.started_at, r.created_at) DESC, r.run_id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, safe_limit, safe_offset],
    )
    rows = cur.fetchall()
    conn.close()

    hydrated_runs: list[dict] = []
    for row in rows:
        hydrated = _hydrate_persisted_run_row(row)
        if hydrated is not None:
            hydrated_runs.append(hydrated)

    return {
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
        "runs": hydrated_runs,
    }


def get_persisted_run(
    run_id: str,
    fill_preview_limit: int = 50,
    equity_preview_limit: int = 200,
) -> Optional[dict]:
    flush_runtime_write_buffers(run_id)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            r.run_id,
            r.run_label,
            r.mode,
            r.state,
            r.symbol,
            r.interval,
            r.market_type,
            r.strategy_name,
            r.strategy_params_json,
            r.config_json,
            r.started_at,
            r.stopped_at,
            r.last_error,
            r.created_at,
            r.updated_at,
            (
                SELECT COUNT(*)
                FROM fills f
                WHERE f.run_id = r.run_id
            ) AS fill_count,
            (
                SELECT COUNT(*)
                FROM equity_snapshots e
                WHERE e.run_id = r.run_id
            ) AS equity_point_count,
            (
                SELECT e.equity
                FROM equity_snapshots e
                WHERE e.run_id = r.run_id
                ORDER BY e.id DESC
                LIMIT 1
            ) AS latest_equity,
            (
                SELECT e.timestamp
                FROM equity_snapshots e
                WHERE e.run_id = r.run_id
                ORDER BY e.id DESC
                LIMIT 1
            ) AS latest_equity_timestamp
        FROM runs r
        WHERE r.run_id = ?
        LIMIT 1
        """,
        (run_id,),
    )
    row = cur.fetchone()
    conn.close()

    run = _hydrate_persisted_run_row(row)
    if run is None:
        return None

    safe_fill_preview_limit = _coerce_limit(
        fill_preview_limit,
        default=50,
        minimum=1,
        maximum=500,
    )
    safe_equity_preview_limit = _coerce_limit(
        equity_preview_limit,
        default=200,
        minimum=1,
        maximum=1000,
    )

    run["fills_preview"] = load_persisted_fills(
        run_id=run_id,
        limit=safe_fill_preview_limit,
        offset=0,
        ascending=False,
    )
    run["equity_preview"] = load_persisted_equity_snapshots(
        run_id=run_id,
        limit=safe_equity_preview_limit,
        offset=0,
        ascending=False,
    )
    return run


def update_run_state(
    run_id: str,
    state: str,
    stopped_at: Optional[float] = None,
    last_error: Optional[str] = None,
) -> None:
    flush_runtime_write_buffers(run_id)

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
    flush_runtime_write_buffers(run_id)

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
    flush_runtime_write_buffers(run_id)

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
    flush_runtime_write_buffers(run_id)

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
        (
            _coerce_limit(limit, default=100, minimum=1, maximum=1000),
            _coerce_offset(offset, default=0),
        ),        
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
        (
            run_id,
            _coerce_limit(limit, default=5000, minimum=1, maximum=1_000_000),
            _coerce_offset(offset, default=0),
        ),        
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
        (
            run_id,
            _coerce_limit(limit, default=5000, minimum=1, maximum=1_000_000),
            _coerce_offset(offset, default=0),
        ),        
    )
    rows = cur.fetchall()
    conn.close()
    return _rows_to_dicts(rows)


# ============================================================
# v0.5.3 Query Layer (persisted run-scoped query surface)
# ============================================================

def get_persisted_run_row(run_id: str) -> Optional[dict]:
    flush_runtime_write_buffers(run_id)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
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
        FROM runs
        WHERE run_id = ?
        LIMIT 1
        """,
        (run_id,),
    )
    row = cur.fetchone()
    conn.close()

    if row is None:
        return None

    data = dict(row)
    data["strategy_params"] = _json_loads(data.get("strategy_params_json"), default={}) or {}
    data["config"] = _json_loads(data.get("config_json"), default={}) or {}
    return data


def count_persisted_fills(run_id: str) -> int:
    flush_runtime_write_buffers(run_id)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM fills WHERE run_id = ?",
        (run_id,),
    )
    row = cur.fetchone()
    conn.close()

    if row is None:
        return 0

    return _safe_int(row["cnt"])


def count_persisted_equity_snapshots(run_id: str) -> int:
    flush_runtime_write_buffers(run_id)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM equity_snapshots WHERE run_id = ?",
        (run_id,),
    )
    row = cur.fetchone()
    conn.close()

    if row is None:
        return 0

    return _safe_int(row["cnt"])


def _compute_max_drawdown_pct(points: list[dict]) -> float:
    peak: Optional[float] = None
    max_dd = 0.0

    for point in points:
        equity = _safe_float(point.get("equity"), default=0.0)
        if peak is None or equity > peak:
            peak = equity
        if peak and peak > 0:
            dd = ((peak - equity) / peak) * 100.0
            if dd > max_dd:
                max_dd = dd

    return float(max_dd)


def get_persisted_run_metrics(run_id: str) -> Optional[dict]:
    flush_runtime_write_buffers(run_id)

    run = get_persisted_run_row(run_id)
    if run is None:
        return None

    fills = load_persisted_fills(
        run_id=run_id,
        limit=1_000_000,
        offset=0,
        ascending=True,
    )
    equity_points = load_persisted_equity_snapshots(
        run_id=run_id,
        limit=1_000_000,
        offset=0,
        ascending=True,
    )

    closed_trade_fills = [f for f in fills if f.get("pnl") is not None]
    gross_pnl = sum(_safe_float(f.get("pnl")) for f in closed_trade_fills)
    total_fees = sum(_safe_float(f.get("fee")) for f in fills)

    winning = [f for f in closed_trade_fills if _safe_float(f.get("pnl")) > 0]
    losing = [f for f in closed_trade_fills if _safe_float(f.get("pnl")) < 0]
    breakeven = [f for f in closed_trade_fills if _safe_float(f.get("pnl")) == 0]

    gross_wins = sum(_safe_float(f.get("pnl")) for f in winning)
    gross_losses_abs = abs(sum(_safe_float(f.get("pnl")) for f in losing))

    first_equity = _safe_float(equity_points[0].get("equity")) if equity_points else 0.0
    last_equity = _safe_float(equity_points[-1].get("equity")) if equity_points else 0.0
    peak_equity = max((_safe_float(p.get("equity")) for p in equity_points), default=0.0)
    trough_equity = min((_safe_float(p.get("equity")) for p in equity_points), default=0.0)

    absolute_return = last_equity - first_equity if equity_points else 0.0
    return_pct = ((absolute_return / first_equity) * 100.0) if first_equity > 0 else 0.0

    latest_drawdown_pct = 0.0
    if equity_points:
        latest_drawdown_pct = _safe_float(
            equity_points[-1].get("drawdown_pct", equity_points[-1].get("drawdown"))
        )

    max_drawdown_pct = _compute_max_drawdown_pct(equity_points)

    closed_trade_count = len(closed_trade_fills)
    winning_trade_count = len(winning)
    losing_trade_count = len(losing)
    breakeven_trade_count = len(breakeven)

    win_rate = (
        (winning_trade_count / closed_trade_count) * 100.0
        if closed_trade_count > 0
        else 0.0
    )

    avg_closed_pnl = gross_pnl / closed_trade_count if closed_trade_count > 0 else 0.0
    avg_win_pnl = gross_wins / winning_trade_count if winning_trade_count > 0 else 0.0
    avg_loss_pnl = (
        sum(_safe_float(f.get("pnl")) for f in losing) / losing_trade_count
        if losing_trade_count > 0
        else 0.0
    )
    profit_factor = (
        gross_wins / gross_losses_abs
        if gross_losses_abs > 0
        else (float("inf") if gross_wins > 0 else 0.0)
    )

    return {
        "run_id": run_id,
        "summary": {
            "run_label": run.get("run_label"),
            "mode": run.get("mode"),
            "state": run.get("state"),
            "symbol": run.get("symbol"),
            "interval": run.get("interval"),
            "market_type": run.get("market_type"),
            "strategy_name": run.get("strategy_name"),
            "strategy_params": run.get("strategy_params", {}),
            "started_at": run.get("started_at"),
            "stopped_at": run.get("stopped_at"),
            "last_error": run.get("last_error"),
        },
        "trade_stats": {
            "fill_count": len(fills),
            "entry_fill_count": sum(
                1 for f in fills if str(f.get("type", "")).lower() in {"buy", "entry", "enter_long", "enter_short"}
            ),
            "exit_fill_count": sum(
                1 for f in fills if str(f.get("type", "")).lower() in {"sell", "exit", "exit_long", "exit_short", "liquidate"}
            ),
            "closed_trade_count": closed_trade_count,
            "winning_trade_count": winning_trade_count,
            "losing_trade_count": losing_trade_count,
            "breakeven_trade_count": breakeven_trade_count,
            "win_rate": float(win_rate),
            "gross_pnl": float(gross_pnl),
            "total_fees": float(total_fees),
            "net_closed_pnl": float(gross_pnl - total_fees),
            "avg_closed_pnl": float(avg_closed_pnl),
            "avg_win_pnl": float(avg_win_pnl),
            "avg_loss_pnl": float(avg_loss_pnl),
            "profit_factor": profit_factor,
        },
        "equity": {
            "point_count": len(equity_points),
            "first_equity": float(first_equity),
            "last_equity": float(last_equity),
            "peak_equity": float(peak_equity),
            "trough_equity": float(trough_equity),
            "absolute_return": float(absolute_return),
            "return_pct": float(return_pct),
            "latest_drawdown_pct": float(latest_drawdown_pct),
            "max_drawdown_pct": float(max_drawdown_pct),
        },
    }


def export_persisted_fills_csv(run_id: str) -> str:
    flush_runtime_write_buffers(run_id)

    rows = load_persisted_fills(
        run_id=run_id,
        limit=1_000_000,
        offset=0,
        ascending=True,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
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
    ])

    for row in rows:
        writer.writerow([
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
        ])

    return output.getvalue()


def export_persisted_equity_csv(run_id: str) -> str:
    flush_runtime_write_buffers(run_id)

    rows = load_persisted_equity_snapshots(
        run_id=run_id,
        limit=1_000_000,
        offset=0,
        ascending=True,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id",
        "run_id",
        "ts",
        "balance",
        "equity",
        "market_price",
        "position_qty",
        "side",
        "unrealized_pnl",
        "realized_pnl",
        "drawdown_pct",
        "created_at",
    ])

    for row in rows:
        writer.writerow([
            row.get("id"),
            row.get("run_id"),
            row.get("ts"),
            row.get("balance"),
            row.get("equity"),
            row.get("market_price"),
            row.get("position_qty"),
            row.get("side"),
            row.get("unrealized_pnl"),
            row.get("realized_pnl"),
            row.get("drawdown_pct"),
            row.get("created_at"),
        ])

    return output.getvalue()