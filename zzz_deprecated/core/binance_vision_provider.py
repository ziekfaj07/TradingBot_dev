import os
import io
import zipfile
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

from core.data_provider import DataProvider


class BinanceVisionProvider(DataProvider):
    BASE = "https://data.binance.vision/data"


    def __init__(self, root_dir: str = "database"):
        self.root_dir = root_dir
        self.hist_dir = os.path.join(root_dir, "historical")
        self.exp_dir = os.path.join(root_dir, "exports")
        os.makedirs(self.hist_dir, exist_ok=True)
        os.makedirs(self.exp_dir, exist_ok=True)

    # ---------- Paths / URL building ----------

    def _market_paths(self, market_type: str) -> list[str]:
        mt = market_type.lower()
        if mt == "spot":
            return ["spot"]
        if mt == "futures":
            # USDT-M first, then COIN-M as fallback
            return ["futures/um", "futures/cm"]
        raise ValueError("market_type must be 'spot' or 'futures'")


    def _kline_folders(self, market_type: str) -> list[str]:
        return ["klines"]


    def _parquet_path(self, symbol: str, interval: str, market_type: str) -> str:
        mt = market_type.lower()
        folder = os.path.join(self.hist_dir, mt)
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, f"{symbol.upper()}_{interval}.parquet")


    def _try_get(self, url: str) -> tuple[bytes | None, int]:
        r = requests.get(url, timeout=60)
        if r.status_code == 200 and r.content:
            return r.content, r.status_code
        return None, r.status_code


    def _build_month_urls(self, symbol: str, interval: str, market_type: str, year: int, month: int) -> list[str]:
        symbol = symbol.upper()
        urls = []
        for mp in self._market_paths(market_type):
            for folder in self._kline_folders(market_type):
                urls.append(
                    f"{self.BASE}/{mp}/monthly/{folder}/{symbol}/{interval}/{symbol}-{interval}-{year}-{month:02d}.zip"
                )
        return urls


    def _build_day_urls(self, symbol: str, interval: str, market_type: str, date_str: str) -> list[str]:
        symbol = symbol.upper()
        urls = []
        for mp in self._market_paths(market_type):
            for folder in self._kline_folders(market_type):
                urls.append(
                    f"{self.BASE}/{mp}/daily/{folder}/{symbol}/{interval}/{symbol}-{interval}-{date_str}.zip"
                )
        return urls


    def _download_first_available(self, urls: list[str]) -> tuple[bytes | None, str | None, int | None]:
        last_url = None
        last_status = None
        for url in urls:
            last_url = url
            content, status = self._try_get(url)
            last_status = status
            if content:
                return content, url, status
        return None, last_url, last_status


    def _clean_ohlcv(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """
        Remove broken rows that can blow up backtests:
        - non-positive prices
        - high/low outside sane bounds
        - extreme jumps (data corruption)
        """
        if df.empty:
            return df

        # Basic validity
        df = df.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
        df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)]
        df = df[(df["high"] >= df["low"]) & (df["high"] >= df["open"]) & (df["high"] >= df["close"])]
        df = df[(df["low"] <= df["open"]) & (df["low"] <= df["close"])]

        # Remove ridiculous spikes: if close changes > 10x vs previous close, drop the row
        # (10x in 1h for BTC is corruption, not real market movement)
        df = df.sort_values("timestamp").reset_index(drop=True)
        prev_close = df["close"].shift(1)
        ratio = df["close"] / prev_close
        df = df[(ratio.isna()) | ((ratio > 0.1) & (ratio < 10.0))]

        return df.reset_index(drop=True)

    # ---------- Parsing ----------

    def _parse_binance_kline_zip(self, zip_bytes: bytes) -> pd.DataFrame:
        """
        Handles both:
        - headerless kline CSV (numeric first column)
        - headered kline CSV (first row contains 'open_time', etc.)
        """
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            name = z.namelist()[0]
            with z.open(name) as f:
                # Read without forcing dtype first (headered files will break int casting)
                raw = pd.read_csv(
                    f,
                    header=None,
                    usecols=[0, 1, 2, 3, 4, 5],
                    low_memory=False,
                )

        # If first cell is a string header like "open_time", drop that row
        first = str(raw.iloc[0, 0]).strip().lower()
        if first in ("open_time", "open time", "timestamp", "time"):
            raw = raw.iloc[1:].reset_index(drop=True)

        raw.columns = ["timestamp_ms", "open", "high", "low", "close", "volume"]

        # Coerce numeric safely
        raw["timestamp_ms"] = pd.to_numeric(raw["timestamp_ms"], errors="coerce")
        for c in ["open", "high", "low", "close", "volume"]:
            raw[c] = pd.to_numeric(raw[c], errors="coerce")

        # Convert timestamp ms -> datetime (coerce invalid)
        raw["timestamp"] = pd.to_datetime(raw["timestamp_ms"], unit="ms", utc=True, errors="coerce")
        raw = raw.drop(columns=["timestamp_ms"])

        # Drop bad rows
        raw = raw.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])

        # Hard range filter (prevents corrupted timestamps)
        min_ts = pd.Timestamp("2010-01-01", tz="UTC")
        max_ts = pd.Timestamp("2100-01-01", tz="UTC")
        raw = raw[(raw["timestamp"] >= min_ts) & (raw["timestamp"] <= max_ts)]

        # Ensure float dtype
        for c in ["open", "high", "low", "close", "volume"]:
            raw[c] = raw[c].astype(float)

        raw = raw.reset_index(drop=True)
        raw = self._clean_ohlcv(raw, symbol="")  # symbol optional
        return raw

    # ---------- Parquet ensure ----------

    def _ensure_parquet(self, symbol: str, interval: str, market_type: str, years_back: int = 3) -> str:
        pq = self._parquet_path(symbol, interval, market_type)
        if os.path.exists(pq):
            return pq

        chunks: list[pd.DataFrame] = []
        last_url = None
        last_status = None

        now = datetime.now(timezone.utc)
        start_year = now.year - years_back

        # 1) Monthly download attempt
        for y in range(start_year, now.year + 1):
            for m in range(1, 13):
                if y == now.year and m > now.month:
                    continue

                urls = self._build_month_urls(symbol, interval, market_type, y, m)
                zb, last_url, last_status = self._download_first_available(urls)
                if not zb:
                    continue

                try:
                    df = self._parse_binance_kline_zip(zb)
                    if not df.empty:
                        chunks.append(df)
                except Exception as e:
                    raise RuntimeError(f"Downloaded but failed to parse: {last_url}. Error: {e}")

        # 2) Daily fallback (last 90 days) if monthly had nothing
        if not chunks:
            for d in range(1, 91):
                day = (now - timedelta(days=d)).strftime("%Y-%m-%d")
                urls = self._build_day_urls(symbol, interval, market_type, day)
                zb, last_url, last_status = self._download_first_available(urls)
                if not zb:
                    continue
                try:
                    df = self._parse_binance_kline_zip(zb)
                    if not df.empty:
                        chunks.append(df)
                except Exception:
                    continue

        if not chunks:
            raise RuntimeError(
                f"No Binance Vision data downloaded/parsed for {symbol.upper()} {interval} {market_type}. "
                f"Last tried URL: {last_url} (status={last_status})"
            )

        merged = pd.concat(chunks, ignore_index=True)
        merged = merged.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

        merged.to_parquet(pq, index=False)
        return pq

    # ---------- Public API ----------

    def _slice(self, df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
        if start:
            s = pd.to_datetime(start, utc=True, errors="coerce")
            if pd.notna(s):
                df = df[df["timestamp"] >= s]
        if end:
            e = pd.to_datetime(end, utc=True, errors="coerce")
            if pd.notna(e):
                df = df[df["timestamp"] <= e]
        return df.reset_index(drop=True)

    def load_ohlcv(self, symbol: str, interval: str, market_type: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        pq = self._ensure_parquet(symbol, interval, market_type)
        df = pd.read_parquet(pq)
        return self._slice(df, start, end)

    def export_to_csv(self, symbol: str, interval: str, market_type: str, out_path: str | None = None,
                      start: str | None = None, end: str | None = None) -> str:
        df = self.load_ohlcv(symbol, interval, market_type, start=start, end=end)
        if out_path is None:
            out_path = os.path.join(self.exp_dir, f"{symbol.upper()}_{interval}_{market_type.lower()}.csv")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        df.to_csv(out_path, index=False)
        return out_path