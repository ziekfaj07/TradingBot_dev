import certifi
import requests
import time
from core.database import create_table, get_connection

BASE_URL = "https://api.bybit.com"

def fetch_candles(symbol="BTCUSDT", interval="3", limit=1000):
    endpoint = "/v5/market/kline"

    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }

    response = requests.get(BASE_URL + endpoint, params=params, verify=False)
    data = response.json()

    print("BYBIT RESPONSE:", data)

    if "result" not in data:
        raise Exception(f"Bybit API error: {data}")

    return data["result"]["list"]


def save_candles(symbol, candles):
    create_table(symbol)
    conn = get_connection()
    cursor = conn.cursor()

    for candle in candles:
        timestamp = int(candle[0])
        open_price = float(candle[1])
        high = float(candle[2])
        low = float(candle[3])
        close = float(candle[4])
        volume = float(candle[5])

        cursor.execute(f"""
            INSERT OR IGNORE INTO {symbol}
            VALUES (?, ?, ?, ?, ?, ?)
        """, (timestamp, open_price, high, low, close, volume))

    conn.commit()
    conn.close()


def download_recent(symbol="BTCUSDT"):
    candles = fetch_candles(symbol)
    save_candles(symbol, candles)