import requests
from core.http_client import request_get


class MarketDataService:
    def get_price(self, symbol: str):
        raise NotImplementedError


class CoinGeckoService(MarketDataService):
    BASE_URL = "https://api.coingecko.com/api/v3"
    SYMBOL_MAP = {
        "btc": "bitcoin",
        "eth": "ethereum",
        "sol": "solana",
        "doge": "dogecoin",
        "pepe": "pepe",
    }

    def get_price(self, symbol: str):
        symbol = str(symbol or "").lower().replace("usdt", "")
        if symbol not in self.SYMBOL_MAP:
            return {"error": "Unsupported symbol"}

        coin_id = self.SYMBOL_MAP[symbol]
        response = request_get(
            f"{self.BASE_URL}/simple/price",
            params={"ids": coin_id, "vs_currencies": "usd"},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()

        if coin_id not in data:
            return {"error": "CoinGecko did not return data"}

        return {
            "symbol": f"{symbol.upper()}USDT",
            "price_usd": float(data[coin_id]["usd"]),
            "source": "coingecko",
        }


class GateIOService(MarketDataService):
    BASE_URL = "https://api.gateio.ws/api/v4"
    INTERVAL_MAP = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "1h",
        "4h": "4h",
        "1d": "1d",
    }

    def _normalize_symbol(self, symbol: str) -> str:
        raw = str(symbol or "").strip().upper().replace("/", "").replace("-", "")
        if not raw:
            raise ValueError("Symbol is required.")
        return raw

    def _to_gate_spot_pair(self, symbol: str) -> str:
        raw = self._normalize_symbol(symbol)
        if raw.endswith("USDT"):
            base = raw[:-4]
            quote = "USDT"
        elif raw.endswith("USD"):
            base = raw[:-3]
            quote = "USD"
        elif "_" in raw:
            return raw
        else:
            raise ValueError(f"Unsupported symbol format: {symbol}")

        if not base:
            raise ValueError(f"Invalid symbol: {symbol}")

        return f"{base}_{quote}"

    def _normalize_interval(self, interval: str) -> str:
        raw = str(interval or "1m").strip().lower()
        return self.INTERVAL_MAP.get(raw, raw)

    def get_price(self, symbol: str):
        try:
            pair = self._to_gate_spot_pair(symbol)
        except ValueError as e:
            return {"error": str(e)}

        response = request_get(
            f"{self.BASE_URL}/spot/tickers",
            params={"currency_pair": pair},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()

        if not data or "last" not in data[0]:
            return {"error": "Gate.io did not return valid data"}

        return {
            "symbol": self._normalize_symbol(symbol),
            "price_usd": float(data[0]["last"]),
            "source": "gateio",
        }

    def get_candles(self, symbol: str, interval: str = "1m", limit: int = 200):
        pair = self._to_gate_spot_pair(symbol)
        gate_interval = self._normalize_interval(interval)
        safe_limit = max(10, min(int(limit), 1000))

        response = request_get(
            f"{self.BASE_URL}/spot/candlesticks",
            params={
                "currency_pair": pair,
                "interval": gate_interval,
                "limit": safe_limit,
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

        bars: list[dict] = []
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
            except (IndexError, TypeError, ValueError):
                continue

        bars.sort(key=lambda x: x["timestamp"])
        return bars