import requests
import pandas as pd
import numpy as np
from core.binance_vision_provider import BinanceVisionProvider


class BacktestService:

    def __init__(self):
        self.provider = BinanceVisionProvider()


    def fetch_historical_data(self, symbol: str, days: int=180):
        """
        Fetch historical daily prices from CoinGecko
        """
        url = f"https://api.coingecko.com/api/v3/coins/{symbol}/market_chart"
        params = {
            "vs_currency": "usd",
            "days": days
        }

        response = requests.get(url, params=params)
        
        if response.status_code != 200:
            return None

        data = response.json()

        if "prices" not in data:
            return None

        df = pd.DataFrame(data["prices"], columns=["timestamp", "price"])
        df["price"] = df["price"].astype(float)

        return df


    def run_ema_strategy(self, df, short=9, long=21, initial_balance=1000):

        df["ema_short"] = df["price"].ewm(span=short, adjust=False).mean()
        df["ema_long"] = df["price"].ewm(span=long, adjust=False).mean()

        balance = initial_balance
        position = 0
        trades = 0

        for i in range(1, len(df)):

            if df["ema_short"].iloc[i] > df["ema_long"].iloc[i] and position == 0:
                # BUY
                position = balance / df["price"].iloc[i]
                balance = 0
                trades += 1

            elif df["ema_short"].iloc[i] < df["ema_long"].iloc[i] and position > 0:
                # SELL
                balance = position * df["price"].iloc[i]
                position = 0
                trades += 1

        final_equity = balance + (position * df["price"].iloc[-1])

        return {
            "initial_balance": initial_balance,
            "final_equity": final_equity,
            "profit": final_equity - initial_balance,
            "return_percent": ((final_equity / initial_balance) - 1) * 100,
            "total_trades": trades
        }