import sqlite3
import os

DB_PATH = "database/market_data.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    return conn

def create_table(symbol: str):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {symbol} (
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