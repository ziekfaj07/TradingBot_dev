import csv
import io
import json
import os
import re
import sqlite3
import time
from typing import Optional

DB_PATH = "database/market_data.db"


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_table_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if not cleaned:
        raise ValueError("Invalid table name")
    return cleaned


def create_table(symbol: str):
    table_name = _safe_table_name(symbol)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            timestamp INTEGER PRIMARY KEY,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# Persistent paper trading core
# ============================================================

def init_runtime_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
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
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS runtime_snapshots (
            run_id TEXT PRIMARY KEY,
            state_json TEXT,
            latest_bar_json TEXT,
            bars_json TEXT,
            last_processed_bar_ts INTEGER,
            last_signal INTEGER,
            trade_id INTEGER,
            updated_at REAL NOT NULL,
            FOREIGN KEY(run_id) REFERENCES runtime_runs(run_id)
        )
    """)

    cur.execute("""
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
            FOREIGN KEY(run_id) REFERENCES runtime_runs(run_id)
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_runtime_fills_run_id_id
        ON runtime_fills(run_id, id)
    """)

    conn.commit()
    conn.close()


def upsert_runtime_run(
    run_id: str,
    mode: str,
    state: str,
    started_at: Optional[float],
    stopped_at: Optional[float],
    last_error: Optional[str],
    config: dict,
):
    now = time.time()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO runtime_runs (
            run_id, mode, state, started_at, stopped_at, last_error,
            config_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            mode=excluded.mode,
            state=excluded.state,
            started_at=excluded.started_at,
            stopped_at=excluded.stopped_at,
            last_error=excluded.last_error,
            config_json=excluded.config_json,
            updated_at=excluded.updated_at
    """, (
        run_id,
        mode,
        state,
        started_at,
        stopped_at,
        last_error,
        json.dumps(config),
        now,
        now,
    ))

    conn.commit()
    conn.close()


def save_runtime_snapshot(
    run_id: str,
    state_dict: Optional[dict],
    latest_bar: Optional[dict],
    bars: Optional[list[dict]],
    last_processed_bar_ts: Optional[int],
    last_signal: int,
    trade_id: int,
):
    now = time.time()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO runtime_snapshots (
            run_id, state_json, latest_bar_json, bars_json,
            last_processed_bar_ts, last_signal, trade_id, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            state_json=excluded.state_json,
            latest_bar_json=excluded.latest_bar_json,
            bars_json=excluded.bars_json,
            last_processed_bar_ts=excluded.last_processed_bar_ts,
            last_signal=excluded.last_signal,
            trade_id=excluded.trade_id,
            updated_at=excluded.updated_at
    """, (
        run_id,
        json.dumps(state_dict) if state_dict is not None else None,
        json.dumps(latest_bar) if latest_bar is not None else None,
        json.dumps(bars) if bars is not None else None,
        last_processed_bar_ts,
        last_signal,
        trade_id,
        now,
    ))

    conn.commit()
    conn.close()


def insert_runtime_fill(run_id: str, fill: dict):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO runtime_fills (
            run_id, timestamp, type, side, price, qty, fee, equity_after,
            entry_price, exit_price, trade_id, pnl, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        run_id,
        str(fill.get("timestamp", "")),
        str(fill.get("type", "")),
        str(fill.get("side", "")),
        float(fill.get("price", 0.0)),
        float(fill.get("qty", 0.0)),
        float(fill.get("fee", 0.0)),
        float(fill.get("equity_after", 0.0)),
        fill.get("entry_price"),
        fill.get("exit_price"),
        fill.get("trade_id"),
        fill.get("pnl"),
        time.time(),
    ))

    conn.commit()
    conn.close()


def load_runtime_fills(run_id: str, limit: int = 500, offset: int = 0) -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            timestamp, type, side, price, qty, fee, equity_after,
            entry_price, exit_price, trade_id, pnl, created_at
        FROM runtime_fills
        WHERE run_id = ?
        ORDER BY id ASC
        LIMIT ? OFFSET ?
    """, (run_id, limit, offset))

    rows = cur.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def count_runtime_fills(run_id: str) -> int:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*) AS cnt
        FROM runtime_fills
        WHERE run_id = ?
    """, (run_id,))
    row = cur.fetchone()
    conn.close()
    return int(row["cnt"] if row else 0)


def get_latest_paper_run() -> Optional[dict]:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM runtime_runs
        WHERE mode = 'paper'
        ORDER BY COALESCE(updated_at, created_at) DESC
        LIMIT 1
    """)
    run_row = cur.fetchone()

    if not run_row:
        conn.close()
        return None

    cur.execute("""
        SELECT *
        FROM runtime_snapshots
        WHERE run_id = ?
        LIMIT 1
    """, (run_row["run_id"],))
    snapshot_row = cur.fetchone()

    conn.close()

    run = dict(run_row)
    snapshot = dict(snapshot_row) if snapshot_row else None

    run["config"] = json.loads(run["config_json"]) if run.get("config_json") else {}

    if snapshot:
        run["snapshot"] = {
            "state": json.loads(snapshot["state_json"]) if snapshot.get("state_json") else None,
            "latest_bar": json.loads(snapshot["latest_bar_json"]) if snapshot.get("latest_bar_json") else None,
            "bars": json.loads(snapshot["bars_json"]) if snapshot.get("bars_json") else [],
            "last_processed_bar_ts": snapshot.get("last_processed_bar_ts"),
            "last_signal": snapshot.get("last_signal") or 0,
            "trade_id": snapshot.get("trade_id") or 0,
            "updated_at": snapshot.get("updated_at"),
        }
    else:
        run["snapshot"] = None

    run["fills"] = load_runtime_fills(run["run_id"], limit=500, offset=0)
    return run


def update_run_state(
    run_id: str,
    state: str,
    stopped_at: Optional[float] = None,
    last_error: Optional[str] = None,
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE runtime_runs
        SET state = ?,
            stopped_at = ?,
            last_error = ?,
            updated_at = ?
        WHERE run_id = ?
    """, (
        state,
        stopped_at,
        last_error,
        time.time(),
        run_id,
    ))

    conn.commit()
    conn.close()


def delete_runtime_run(run_id: str):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM runtime_fills WHERE run_id = ?", (run_id,))
    cur.execute("DELETE FROM runtime_snapshots WHERE run_id = ?", (run_id,))
    cur.execute("DELETE FROM runtime_runs WHERE run_id = ?", (run_id,))

    conn.commit()
    conn.close()


def export_runtime_fills_csv(run_id: str) -> str:
    rows = load_runtime_fills(run_id=run_id, limit=1_000_000, offset=0)

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "id",
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