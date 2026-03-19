import requests

class MarketDataService:
    def get_price(self, symbol: str):
        raise NotImplementedError


class CoinGeckoService(MarketDataService):

    BASE_URL = "https://api.coingecko.com/api/v3"

    SYMBOL_MAP = {
        "btc": "bitcoin",
        "eth": "ethereum",
        "sol": "solana"
    }

    def get_price(self, symbol: str):

        symbol = symbol.lower()

        if symbol not in self.SYMBOL_MAP:
            return {"error": "Unsupported symbol"}

        coin_id = self.SYMBOL_MAP[symbol]

        response = requests.get(
            f"{self.BASE_URL}/simple/price",
            params={
                "ids": coin_id,
                "vs_currencies": "usd"
            }
        )

        data = response.json()

        if coin_id not in data:
            return {"error": "CoinGecko did not return data"}

        return {
            "symbol": symbol.upper(),
            "price_usd": data[coin_id]["usd"],
            "source": "coingecko"
        }


class GateIOService(MarketDataService):

    BASE_URL = "https://api.gateio.ws/api/v4"

    SYMBOL_MAP = {
        "btcusdt": "BTC_USDT",
        "ethusdt": "ETH_USDT",
        "solusdt": "SOL_USDT",
        "dogeusdt": "DOGE_USDT",
        "pepeusdt": "PEPE_USDT",
        "wifusdt": "WIF_USDT",
        "bonkusdt": "BONK_USDT"
    }

    INTERVAL_MAP = {
       "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "1h": "1h",
        "4h": "4h",
        "1d": "1d",
    }

    def get_price(self, symbol: str):

        symbol = symbol.lower()

        if symbol not in self.SYMBOL_MAP:
            return {"error": "Unsupported symbol"}

        pair = self.SYMBOL_MAP[symbol]

        response = requests.get(
            f"{self.BASE_URL}/spot/tickers",
            params={"currency_pair": pair}
        )

        data = response.json()

        if not data or "last" not in data[0]:
            return {"error": "Gate.io did not return valid data"}

        return {
            "symbol": symbol.upper(),
            "price_usd": float(data[0]["last"]),
            "source": "gateio"
        }

    # def get_candles(self, symbol: str, interval="1m", limit=200):
    #     symbol = symbol.lower()

    #     if symbol not in self.SYMBOL_MAP:
    #         return []

    #     pair = self.SYMBOL_MAP[symbol]

    #     response = requests.get(
    #         f"{self.BASE_URL}/spot/candlesticks",
    #         params={
    #             "currency_pair": pair,
    #             "interval": interval,
    #             "limit": limit,
    #         },
    #         timeout=10,
    #     )
    #     response.raise_for_status()

    #     data = response.json()

    #     # Gate returns: [timestamp, volume, close, high, low, open]
    #     bars = []
    #     for row in data:
    #         try:
    #             bars.append(
    #                 {
    #                     "timestamp": int(row[0]),
    #                     "open": float(row[5]),
    #                     "high": float(row[3]),
    #                     "low": float(row[4]),
    #                     "close": float(row[2]),
    #                     "volume": float(row[1]),
    #                 }
    #             )
    #         except (IndexError, TypeError, ValueError):
    #             continue

    #     # oldest -> newest
    #     bars.sort(key=lambda x: x["timestamp"])
    #     return bars

    # this block is temporary debugging script    
    def get_candles(self, symbol: str, interval="1m", limit=200):
        symbol = symbol.lower()
        print(f"[get_candles] incoming symbol={symbol}, interval={interval}, limit={limit}")

        if symbol not in self.SYMBOL_MAP:
            print(f"[get_candles] symbol not found in map: {symbol}")
            return []

        pair = self.SYMBOL_MAP[symbol]
        print(f"[get_candles] mapped pair={pair}")

        response = requests.get(
            f"{self.BASE_URL}/spot/candlesticks",
            params={
                "currency_pair": pair,
                "interval": interval,
                "limit": limit,
            },
            timeout=10,
        )

        print(f"[get_candles] status_code={response.status_code}")
        print(f"[get_candles] raw_text={response.text[:500]}")

        response.raise_for_status()
        data = response.json()

        bars = []
        for row in data:
            try:
                bars.append(
                    {
                        "timestamp": int(row[0]),
                        "open": float(row[5]),
                        "high": float(row[3]),
                        "low": float(row[4]),
                        "close": float(row[2]),
                        "volume": float(row[1]),
                    }
                )
            except (IndexError, TypeError, ValueError) as e:
                print(f"[get_candles] skipped row due to error: {e}, row={row}")
                continue

        bars.sort(key=lambda x: x["timestamp"])
        print(f"[get_candles] parsed bars={len(bars)}")

        if bars:
            print(f"[get_candles] latest parsed bar={bars[-1]}")

        return bars    