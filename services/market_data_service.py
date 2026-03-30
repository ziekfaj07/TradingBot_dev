import requests


class MarketDataService:
    def get_price(self, symbol: str):
        raise NotImplementedError


class CoinGeckoService(MarketDataService):
    BASE_URL = "https://api.coingecko.com/api/v3"

    SYMBOL_MAP = {
        "btc": "bitcoin",
        "eth": "ethereum",
        "sol": "solana",
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
                "vs_currencies": "usd",
            },
            timeout=10,
        )

        data = response.json()

        if coin_id not in data:
            return {"error": "CoinGecko did not return data"}

        return {
            "symbol": symbol.upper(),
            "price_usd": data[coin_id]["usd"],
            "source": "coingecko",
        }


class GateIOService(MarketDataService):
    BASE_URL = "https://api.gateio.ws/api/v4"

    INTERVAL_MAP = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "1h": "1h",
        "4h": "4h",
        "1d": "1d",
    }

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        """
        Convert flexible user input into GateIO spot pair format.

        Examples:
            BTCUSDT   -> BTC_USDT
            btcusdt   -> BTC_USDT
            BTC/USDT  -> BTC_USDT
            BTC_USDT  -> BTC_USDT
            ethusdt   -> ETH_USDT
        """
        raw = (symbol or "").strip().upper()

        if not raw:
            raise ValueError("Symbol is required")

        raw = raw.replace("/", "_").replace("-", "_").replace(" ", "")

        if "_" in raw:
            base, quote = raw.split("_", 1)
            base = base.strip()
            quote = quote.strip()
            if not base or not quote:
                raise ValueError(f"Invalid symbol format: {symbol}")
            return f"{base}_{quote}"

        common_quotes = [
            "USDT",
            "USDC",
            "BTC",
            "ETH",
            "TRY",
            "EUR",
            "USD",
        ]

        for quote in common_quotes:
            if raw.endswith(quote) and len(raw) > len(quote):
                base = raw[:-len(quote)]
                return f"{base}_{quote}"

        raise ValueError(
            f"Unsupported symbol format: {symbol}. Use formats like BTCUSDT, BTC/USDT, or BTC_USDT."
        )

    def get_price(self, symbol: str):
        try:
            pair = self.normalize_symbol(symbol)
        except ValueError as e:
            return {"error": str(e)}

        response = requests.get(
            f"{self.BASE_URL}/spot/tickers",
            params={"currency_pair": pair},
            timeout=10,
        )

        response.raise_for_status()
        data = response.json()

        if not isinstance(data, list) or not data or "last" not in data[0]:
            return {"error": "Gate.io did not return valid data"}

        return {
            "symbol": pair,
            "price_usd": float(data[0]["last"]),
            "source": "gateio",
        }

    # this block is temporary debugging script
    def get_candles(self, symbol: str, interval="1m", limit=200):
        try:
            pair = self.normalize_symbol(symbol)
        except ValueError as e:
            print(f"[get_candles] invalid symbol: {e}")
            return []

        interval = self.INTERVAL_MAP.get(interval, interval)
        print(f"[get_candles] incoming symbol={symbol}, normalized_pair={pair}, interval={interval}, limit={limit}")

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