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
        "btc": "BTC_USDT",
        "eth": "ETH_USDT",
        "sol": "SOL_USDT"
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

    def get_candles(self, symbol: str, interval="1m", limit=1000):

        symbol = symbol.lower()

        if symbol not in self.SYMBOL_MAP:
            return []

        pair = self.SYMBOL_MAP[symbol]

        response = requests.get(
            f"{self.BASE_URL}/spot/candlesticks",
            params={
                "currency_pair": pair,
                "interval": interval,
                "limit": limit,
            }
        )

        data = response.json()

        #Gate returns: [timestamp, volume, close, high, low, open]
        closes = [float(candle[2]) for candle in data]

        return closes