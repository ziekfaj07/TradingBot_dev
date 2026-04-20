from __future__ import annotations

import io
import os
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, TypeAlias

import pandas as pd
import requests

from core.market_types import is_derivatives_market, normalize_market_type

from core.data_provider import DataProvider

UtcTimestamp: TypeAlias = pd.Timestamp
OptionalUtcTimestamp: TypeAlias = pd.Timestamp | None

@dataclass(frozen=True)
class _CoverageRequest:
    symbol: str
    interval: str
    market_type: str
    start: pd.Timestamp
    end: pd.Timestamp


class BinanceVisionProvider(DataProvider):
    BASE = "https://data.binance.vision/data"

    # Try requested interval first. Only fall back to 1m if exact interval cannot be loaded.
    PREFERRED_BASE_INTERVAL: dict[str, str] = {
        "1m": "1m",
        "3m": "3m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "1h",
        "2h": "2h",
        "4h": "4h",
        "6h": "6h",
        "8h": "8h",
        "12h": "12h",
        "1d": "1d",
        "3d": "3d",
        "1w": "1w",
    }

    RESAMPLE_RULES: dict[str, str] = {
        "1m": "1min",
        "3m": "3min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1h",
        "2h": "2h",
        "4h": "4h",
        "6h": "6h",
        "8h": "8h",
        "12h": "12h",
        "1d": "1D",
        "3d": "3D",
        "1w": "1W-MON",
    }

    REST_SPOT_BASES = [
        "https://api.binance.com",
        "https://api-gcp.binance.com",
        "https://api1.binance.com",
        "https://api2.binance.com",
        "https://api3.binance.com",
        "https://api4.binance.com",
        "https://data-api.binance.vision",
    ]

    REST_FUTURES_BASES = [
        "https://fapi.binance.com",
        "https://dapi.binance.com",
    ]

    _REST_ENDPOINTS: dict[str, list[str]] = {
        "spot": ["/api/v3/klines"],
        "futures": ["/fapi/v1/klines", "/dapi/v1/klines"],
    }

    def __init__(self, root_dir: str | None = None):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        if root_dir is None:
            root_dir = os.path.join(base_dir, "database")
        elif not os.path.isabs(root_dir):
            root_dir = os.path.join(base_dir, root_dir)

        self.root_dir = root_dir
        self.hist_dir = os.path.join(self.root_dir, "historical")
        self.exp_dir = os.path.join(self.root_dir, "exports")
        os.makedirs(self.hist_dir, exist_ok=True)
        os.makedirs(self.exp_dir, exist_ok=True)

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "TradingBot/0.1 (+historical-loader)",
                "Accept": "application/json, text/plain, */*",
            }
        )

    # ---------- Paths / URL building ----------
    def _normalize_symbol(self, symbol: str) -> str:
        return str(symbol or "").strip().upper()

    def _normalize_market_type(self, market_type: str) -> str:
        return normalize_market_type(market_type)

    def _normalize_interval(self, interval: str) -> str:
        iv = str(interval or "1m").strip().lower()
        if iv not in self.RESAMPLE_RULES:
            raise ValueError(f"Unsupported interval: {interval}")
        return iv

    def _preferred_base_interval(self, requested_interval: str) -> str:
        return self.PREFERRED_BASE_INTERVAL.get(requested_interval, requested_interval)

    def _normalize_bound_text(self, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if text.lower() in {"string", "none", "null", "nan"}:
            return None
        return text

    def _to_utc_timestamp(self, value) -> OptionalUtcTimestamp:
        if value is None:
            return None

        if isinstance(value, pd.Timestamp):
            ts = value
        else:
            ts = pd.to_datetime(value, errors="coerce")

        if pd.isna(ts):
            return None

        if getattr(ts, "tzinfo", None) is None:
            return ts.tz_localize("UTC")

        return ts.tz_convert("UTC")

    def _parse_bound(self, value: str | None, *, is_end: bool) -> OptionalUtcTimestamp:
        text = self._normalize_bound_text(value)
        if text is None:
            return None

        ts = self._to_utc_timestamp(text)
        if ts is None:
            return None

        # Treat YYYY-MM-DD as a whole UTC day for end bounds.
        if is_end and len(text) == 10 and text[4] == "-" and text[7] == "-":
            ts = ts + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)

        return ts

    def _default_lookback_for_interval(self, interval: str) -> pd.DateOffset:
        interval = self._normalize_interval(interval)

        if interval in {"1m", "3m", "5m"}:
            return pd.DateOffset(days=90)
        if interval in {"15m", "30m", "1h"}:
            return pd.DateOffset(days=180)
        if interval in {"2h", "4h", "6h", "8h", "12h"}:
            return pd.DateOffset(years=1)
        return pd.DateOffset(years=3)

    def _resolve_bounds(
        self,
        interval: str,
        start: str | None,
        end: str | None,
    ) -> tuple[pd.Timestamp, pd.Timestamp]:
        now = pd.Timestamp.now(tz="UTC").floor("min")

        start_ts = self._parse_bound(start, is_end=False)
        end_ts = self._parse_bound(end, is_end=True)

        if end_ts is None:
            end_ts = now
        if start_ts is None:
            start_ts = end_ts - self._default_lookback_for_interval(interval)

        if start_ts > end_ts:
            start_ts, end_ts = end_ts, start_ts

        return start_ts, end_ts

    def _interval_to_timedelta(self, interval: str) -> pd.Timedelta:
        interval = self._normalize_interval(interval)
        mapping = {
            "1m": pd.Timedelta(minutes=1),
            "3m": pd.Timedelta(minutes=3),
            "5m": pd.Timedelta(minutes=5),
            "15m": pd.Timedelta(minutes=15),
            "30m": pd.Timedelta(minutes=30),
            "1h": pd.Timedelta(hours=1),
            "2h": pd.Timedelta(hours=2),
            "4h": pd.Timedelta(hours=4),
            "6h": pd.Timedelta(hours=6),
            "8h": pd.Timedelta(hours=8),
            "12h": pd.Timedelta(hours=12),
            "1d": pd.Timedelta(days=1),
            "3d": pd.Timedelta(days=3),
            "1w": pd.Timedelta(days=7),
        }
        return mapping[interval]

    def _scalar_to_int(self, value) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        try:
            return int(float(value))
        except Exception as e:
            raise ValueError(f"Cannot coerce value to int: {value!r}") from e

    def _iter_years(self, start: pd.Timestamp, end: pd.Timestamp) -> Iterable[int]:
        for year in range(start.year, end.year + 1):
            yield year

    def _iter_month_starts(self, start: pd.Timestamp, end: pd.Timestamp) -> Iterable[pd.Timestamp]:
        current = start.floor("D").replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while current <= end:
            yield current
            current = current + pd.offsets.MonthBegin(1)

    def _market_paths(self, market_type: str) -> list[str]:
        mt = normalize_market_type(market_type)
        if mt == "spot":
            return ["spot"]
        if is_derivatives_market(mt):
            return ["futures/um", "futures/cm"]
        raise ValueError("market_type must be 'spot' or 'swap'")

    def _kline_folders(self, market_type: str) -> list[str]:
        return ["klines"]

    def _interval_dir(self, symbol: str, interval: str, market_type: str) -> str:
        return os.path.join(
            self.hist_dir,
            market_type.lower(),
            symbol.upper(),
            interval,
        )

    def _year_parquet_path(self, symbol: str, interval: str, market_type: str, year: int) -> str:
        folder = self._interval_dir(symbol, interval, market_type)
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, f"{year}.parquet")

    def _parquet_path(self, symbol: str, interval: str, market_type: str) -> str:
        mt = market_type.lower()
        folder = os.path.join(self.hist_dir, mt)
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, f"{symbol.upper()}_{interval}.parquet")

    def _legacy_flat_parquet_path(self, symbol: str, interval: str, market_type: str) -> str:
        return self._parquet_path(symbol, interval, market_type)

    def _build_month_urls(self, symbol: str, interval: str, market_type: str, year: int, month: int) -> list[str]:
        symbol = symbol.upper()
        urls: list[str] = []
        for mp in self._market_paths(market_type):
            for folder in self._kline_folders(market_type):
                urls.append(
                    f"{self.BASE}/{mp}/monthly/{folder}/{symbol}/{interval}/"
                    f"{symbol}-{interval}-{year}-{month:02d}.zip"
                )
        return urls

    def _build_day_urls(self, symbol: str, interval: str, market_type: str, date_str: str) -> list[str]:
        symbol = symbol.upper()
        urls: list[str] = []
        for mp in self._market_paths(market_type):
            for folder in self._kline_folders(market_type):
                urls.append(
                    f"{self.BASE}/{mp}/daily/{folder}/{symbol}/{interval}/"
                    f"{symbol}-{interval}-{date_str}.zip"
                )
        return urls

    def _safe_read_parquet(self, path: str) -> pd.DataFrame:
        try:
            return pd.read_parquet(path)
        except Exception as e:
            raise RuntimeError(
                f"Failed to read parquet file: {path}. Original error: {e}"
            ) from e

    def _normalize_timestamp_series(self, series: pd.Series) -> pd.Series:
        if series.empty:
            return pd.to_datetime(series, utc=True, errors="coerce")

        if pd.api.types.is_numeric_dtype(series):
            numeric = pd.to_numeric(series, errors="coerce")
            sample = numeric.dropna()
            if sample.empty:
                return pd.to_datetime(series, utc=True, errors="coerce")

            med = float(sample.abs().median())
            if med >= 1e17:
                unit = "ns"
            elif med >= 1e14:
                unit = "us"
            elif med >= 1e11:
                unit = "ms"
            else:
                unit = "s"
            return pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce")

        if pd.api.types.is_datetime64_any_dtype(series):
            try:
                raw = pd.Series(series.astype("int64"), index=series.index)
                sample = raw.dropna()
                if not sample.empty:
                    med = float(sample.abs().median())
                    if med < 1e17:
                        if med >= 1e14:
                            unit = "us"
                        elif med >= 1e11:
                            unit = "ms"
                        else:
                            unit = "s"
                        return pd.to_datetime(raw, unit=unit, utc=True, errors="coerce")
            except Exception:
                pass
            return pd.to_datetime(series, utc=True, errors="coerce")

        as_dt = pd.to_datetime(series, utc=True, errors="coerce")
        if as_dt.notna().any():
            return as_dt

        numeric = pd.to_numeric(series, errors="coerce")
        sample = numeric.dropna()
        if sample.empty:
            return as_dt

        med = float(sample.abs().median())
        if med >= 1e17:
            unit = "ns"
        elif med >= 1e14:
            unit = "us"
        elif med >= 1e11:
            unit = "ms"
        else:
            unit = "s"
        return pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce")

    # ---------- HTTP helpers ----------
    def _request(self, url: str, params: dict | None = None, *, expect_json: bool) -> tuple[object | None, int, str | None]:
        last_err: str | None = None
        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params, timeout=60)
                status = resp.status_code
                if status == 200:
                    if expect_json:
                        return resp.json(), status, None
                    return resp.content, status, None
                last_err = f"HTTP {status}"
                if status in {429, 418, 500, 502, 503, 504} and attempt < 2:
                    time.sleep(1.0 + attempt)
                    continue
                return None, status, last_err
            except Exception as e:
                last_err = str(e)
                if attempt < 2:
                    time.sleep(1.0 + attempt)
                    continue
                return None, -1, last_err
        return None, -1, last_err

    def _download_first_available(self, urls: list[str]) -> tuple[bytes | None, str | None, int | None, str | None]:
        last_url = None
        last_status = None
        last_error = None
        for url in urls:
            last_url = url
            content, status, err = self._request(url, expect_json=False)
            last_status = status
            last_error = err
            if isinstance(content, (bytes, bytearray)) and content:
                return bytes(content), url, status, None
        return None, last_url, last_status, last_error

    # ---------- Cleaning / parsing ----------
    def _empty_ohlcv_df(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    def _finalize_ohlcv(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return self._empty_ohlcv_df()

        out = df.copy()
        out = out.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
        out["timestamp"] = self._normalize_timestamp_series(out["timestamp"])
        out = out.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")

        min_valid = pd.Timestamp("2000-01-01", tz="UTC")
        max_valid = pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=30)

        out = out[
            out["timestamp"].notna()
            & (out["timestamp"] >= min_valid)
            & (out["timestamp"] <= max_valid)
        ].copy()

        return out.reset_index(drop=True)

    def _normalize_loaded_df(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return self._empty_ohlcv_df()

        out = df.copy()
        required = ["timestamp", "open", "high", "low", "close", "volume"]
        for col in required:
            if col not in out.columns:
                raise RuntimeError(f"Historical file missing required column: {col}")

        out["timestamp"] = self._normalize_timestamp_series(out["timestamp"])
        for col in ["open", "high", "low", "close", "volume"]:
            out[col] = pd.to_numeric(out[col], errors="coerce")

        return self._finalize_ohlcv(out)

    def _clean_ohlcv(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return self._empty_ohlcv_df()

        out = df.copy()
        out["timestamp"] = self._normalize_timestamp_series(out["timestamp"])
        for col in ["open", "high", "low", "close", "volume"]:
            out[col] = pd.to_numeric(out[col], errors="coerce")

        out = out.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
        out = out[(out["open"] > 0) & (out["high"] > 0) & (out["low"] > 0) & (out["close"] > 0)]
        out = out[out["high"] >= out["low"]]
        out = out.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")

        prev_close = out["close"].shift(1)
        ratio = out["close"] / prev_close
        out = out[(ratio.isna()) | ((ratio > 0.05) & (ratio < 20.0))]

        return self._finalize_ohlcv(out)

    def _parse_binance_kline_zip(self, zip_bytes: bytes) -> pd.DataFrame:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            if not names:
                return self._empty_ohlcv_df()
            with zf.open(names[0]) as fh:
                raw = pd.read_csv(
                    fh,
                    header=None,
                    usecols=[0, 1, 2, 3, 4, 5],
                    low_memory=False,
                )

        if raw.empty:
            return self._empty_ohlcv_df()

        first = str(raw.iloc[0, 0]).strip().lower()
        if first in ("open_time", "open time", "timestamp", "time"):
            raw = raw.iloc[1:].reset_index(drop=True)

        raw.columns = ["timestamp_ms", "open", "high", "low", "close", "volume"]
        raw["timestamp"] = pd.to_datetime(pd.to_numeric(raw["timestamp_ms"], errors="coerce"), unit="ms", utc=True, errors="coerce")
        raw = raw.drop(columns=["timestamp_ms"])
        return self._clean_ohlcv(raw)

    def _parse_rest_klines(self, payload: object) -> pd.DataFrame:
        if not isinstance(payload, list) or not payload:
            return self._empty_ohlcv_df()

        rows = []
        for item in payload:
            if not isinstance(item, list) or len(item) < 6:
                continue
            rows.append(
                {
                    "timestamp": pd.to_datetime(pd.to_numeric(item[0], errors="coerce"), unit="ms", utc=True, errors="coerce"),
                    "open": item[1],
                    "high": item[2],
                    "low": item[3],
                    "close": item[4],
                    "volume": item[5],
                }
            )

        if not rows:
            return self._empty_ohlcv_df()

        return self._clean_ohlcv(pd.DataFrame(rows))

    # ---------- Local partitioned store ----------
    def _load_partitioned_range(
        self,
        symbol: str,
        interval: str,
        market_type: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> pd.DataFrame:
        chunks: list[pd.DataFrame] = []
        folder = self._interval_dir(symbol, interval, market_type)
        if not os.path.exists(folder):
            return self._empty_ohlcv_df()

        expected_years = set(self._iter_years(start, end))
        for filename in os.listdir(folder):
            if not filename.endswith(".parquet"):
                continue
            stem = filename[:-8]
            if not stem.isdigit():
                continue
            year = int(stem)
            if year not in expected_years:
                continue
            path = os.path.join(folder, filename)
            df = self._safe_read_parquet(path)
            if df.empty:
                continue
            chunks.append(self._normalize_loaded_df(df))

        if not chunks:
            return self._empty_ohlcv_df()

        merged = pd.concat(chunks, ignore_index=True)
        merged = self._finalize_ohlcv(merged)
        return self._slice(merged, start, end)

    def _write_partitioned_df(self, symbol: str, interval: str, market_type: str, df: pd.DataFrame) -> None:
        if df.empty:
            return

        work = self._normalize_loaded_df(df)
        if work.empty:
            return

        work["year"] = work["timestamp"].dt.year
        for year, year_df in work.groupby("year", sort=True):
            year_path = self._year_parquet_path(
                symbol,
                interval,
                market_type,
                self._scalar_to_int(year),
            )
            incoming = year_df.drop(columns=["year"]).copy()

            if os.path.exists(year_path):
                current = self._safe_read_parquet(year_path)
                current = self._normalize_loaded_df(current)
                merged = pd.concat([current, incoming], ignore_index=True)
            else:
                merged = incoming

            merged = self._finalize_ohlcv(merged)
            merged.to_parquet(year_path, index=False)

    def _migrate_legacy_flat_parquet(self, symbol: str, interval: str, market_type: str) -> None:
        legacy_path = self._legacy_flat_parquet_path(symbol, interval, market_type)
        if not os.path.exists(legacy_path):
            return

        legacy_df = self._safe_read_parquet(legacy_path)
        legacy_df = self._normalize_loaded_df(legacy_df)
        if legacy_df.empty:
            return

        self._write_partitioned_df(symbol, interval, market_type, legacy_df)

    def _load_legacy_flat_range(
        self,
        symbol: str,
        interval: str,
        market_type: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> pd.DataFrame:
        path = self._legacy_flat_parquet_path(symbol, interval, market_type)
        if not os.path.exists(path):
            return self._empty_ohlcv_df()

        df = self._safe_read_parquet(path)
        df = self._normalize_loaded_df(df)
        return self._slice(df, start, end)

    def _coverage_ok(self, df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, interval: str) -> bool:
        if df.empty:
            return False
        tol = self._interval_to_timedelta(interval)
        min_ts = df["timestamp"].min()
        max_ts = df["timestamp"].max()
        if pd.isna(min_ts) or pd.isna(max_ts):
            return False
        return bool(min_ts <= (start + tol) and max_ts >= (end - tol))

    # ---------- Archive downloads ----------
    def _download_monthly_archives(
        self,
        symbol: str,
        interval: str,
        market_type: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> tuple[pd.DataFrame, list[str]]:
        chunks: list[pd.DataFrame] = []
        notes: list[str] = []

        for month_start in self._iter_month_starts(start, end):
            urls = self._build_month_urls(symbol, interval, market_type, month_start.year, month_start.month)
            zb, url, status, err = self._download_first_available(urls)
            if not zb:
                if url:
                    notes.append(f"archive monthly miss {month_start.strftime('%Y-%m')}: {status or 'no-status'} {err or ''}".strip())
                continue
            try:
                df = self._parse_binance_kline_zip(zb)
                if not df.empty:
                    chunks.append(df)
            except Exception as e:
                notes.append(f"archive monthly parse fail {month_start.strftime('%Y-%m')}: {e}")

        if not chunks:
            return self._empty_ohlcv_df(), notes

        merged = pd.concat(chunks, ignore_index=True)
        merged = self._finalize_ohlcv(merged)
        return self._slice(merged, start, end), notes

    def _download_daily_archives(
        self,
        symbol: str,
        interval: str,
        market_type: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> tuple[pd.DataFrame, list[str]]:
        start_day = start.floor("D")
        end_day = end.floor("D")
        current = start_day
        chunks: list[pd.DataFrame] = []
        notes: list[str] = []

        while current <= end_day:
            date_str = current.strftime("%Y-%m-%d")
            urls = self._build_day_urls(symbol, interval, market_type, date_str)
            zb, url, status, err = self._download_first_available(urls)
            if zb:
                try:
                    df = self._parse_binance_kline_zip(zb)
                    if not df.empty:
                        chunks.append(df)
                except Exception as e:
                    notes.append(f"archive daily parse fail {date_str}: {e}")
            else:
                if url:
                    notes.append(f"archive daily miss {date_str}: {status or 'no-status'} {err or ''}".strip())
            current += pd.Timedelta(days=1)

        if not chunks:
            return self._empty_ohlcv_df(), notes

        merged = pd.concat(chunks, ignore_index=True)
        merged = self._finalize_ohlcv(merged)
        return self._slice(merged, start, end), notes

    # ---------- REST fallback ----------
    def _rest_bases_for_market(self, market_type: str) -> list[str]:
        if market_type == "spot":
            return list(self.REST_SPOT_BASES)
        return list(self.REST_FUTURES_BASES)

    def _fetch_rest_klines_once(
        self,
        base_url: str,
        endpoint: str,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
        limit: int = 1000,
    ) -> tuple[pd.DataFrame, str | None]:
        payload, status, err = self._request(
            f"{base_url}{endpoint}",
            params={
                "symbol": symbol.upper(),
                "interval": interval,
                "startTime": int(start_ms),
                "endTime": int(end_ms),
                "limit": int(limit),
            },
            expect_json=True,
        )
        if status != 200:
            return self._empty_ohlcv_df(), f"{base_url}{endpoint} -> {status} {err or ''}".strip()

        df = self._parse_rest_klines(payload)
        return df, None

    def _fetch_rest_klines(
        self,
        symbol: str,
        interval: str,
        market_type: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> tuple[pd.DataFrame, list[str]]:
        delta = self._interval_to_timedelta(interval)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        max_span = int(delta.total_seconds() * 1000 * 1000)

        notes: list[str] = []
        all_chunks: list[pd.DataFrame] = []

        bases = self._rest_bases_for_market(market_type)
        endpoints = self._REST_ENDPOINTS[market_type]

        for endpoint in endpoints:
            endpoint_success = False
            endpoint_chunks: list[pd.DataFrame] = []
            endpoint_notes: list[str] = []

            for base_url in bases:
                cursor = start_ms
                base_chunks: list[pd.DataFrame] = []
                base_notes: list[str] = []
                consecutive_empty = 0

                while cursor <= end_ms:
                    batch_end_ms = min(end_ms, cursor + max_span - 1)
                    df, err = self._fetch_rest_klines_once(
                        base_url=base_url,
                        endpoint=endpoint,
                        symbol=symbol,
                        interval=interval,
                        start_ms=cursor,
                        end_ms=batch_end_ms,
                        limit=1000,
                    )
                    if err is not None:
                        base_notes.append(err)
                        break

                    if df.empty:
                        consecutive_empty += 1
                        if consecutive_empty >= 2:
                            break
                        cursor = batch_end_ms + 1
                        continue

                    consecutive_empty = 0
                    base_chunks.append(df)
                    last_open_ms = int(df["timestamp"].max().timestamp() * 1000)
                    next_cursor = last_open_ms + int(delta.total_seconds() * 1000)
                    if next_cursor <= cursor:
                        next_cursor = batch_end_ms + 1
                    cursor = next_cursor

                    if cursor > end_ms:
                        break
                    time.sleep(0.05)

                if base_chunks:
                    merged = pd.concat(base_chunks, ignore_index=True)
                    merged = self._finalize_ohlcv(merged)
                    endpoint_chunks.append(merged)
                    endpoint_success = True
                    break

                endpoint_notes.extend(base_notes)

            if endpoint_chunks:
                all_chunks.extend(endpoint_chunks)
                break

            notes.extend(endpoint_notes)
            if endpoint_success:
                break

        if not all_chunks:
            return self._empty_ohlcv_df(), notes

        merged = pd.concat(all_chunks, ignore_index=True)
        merged = self._finalize_ohlcv(merged)
        return self._slice(merged, start, end), notes

    # ---------- Coverage orchestration ----------
    def _ensure_local_coverage(
        self,
        symbol: str,
        interval: str,
        market_type: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> list[str]:
        notes: list[str] = []

        local = self._load_partitioned_range(symbol, interval, market_type, start, end)
        if self._coverage_ok(local, start, end, interval):
            return notes

        legacy = self._load_legacy_flat_range(symbol, interval, market_type, start, end)
        if not legacy.empty:
            self._write_partitioned_df(symbol, interval, market_type, legacy)
            local = self._load_partitioned_range(symbol, interval, market_type, start, end)
            if self._coverage_ok(local, start, end, interval):
                notes.append("filled from legacy flat parquet")
                return notes

        monthly_df, monthly_notes = self._download_monthly_archives(symbol, interval, market_type, start, end)
        notes.extend(monthly_notes)
        if not monthly_df.empty:
            self._write_partitioned_df(symbol, interval, market_type, monthly_df)
            local = self._load_partitioned_range(symbol, interval, market_type, start, end)
            if self._coverage_ok(local, start, end, interval):
                notes.append("filled from monthly archives")
                return notes

        daily_df, daily_notes = self._download_daily_archives(symbol, interval, market_type, start, end)
        notes.extend(daily_notes)
        if not daily_df.empty:
            self._write_partitioned_df(symbol, interval, market_type, daily_df)
            local = self._load_partitioned_range(symbol, interval, market_type, start, end)
            if self._coverage_ok(local, start, end, interval):
                notes.append("filled from daily archives")
                return notes

        rest_df, rest_notes = self._fetch_rest_klines(symbol, interval, market_type, start, end)
        notes.extend(rest_notes)
        if not rest_df.empty:
            self._write_partitioned_df(symbol, interval, market_type, rest_df)
            notes.append("filled from REST klines")

        return notes

    # ---------- Resampling ----------
    def _resample_ohlcv(self, df: pd.DataFrame, target_interval: str) -> pd.DataFrame:
        target_interval = self._normalize_interval(target_interval)
        if target_interval == "1m":
            return self._finalize_ohlcv(df)

        rule = self.RESAMPLE_RULES[target_interval]
        work = self._finalize_ohlcv(df)
        if work.empty:
            return work

        work = work.set_index("timestamp")
        out = work.resample(rule, label="left", closed="left").agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        out = out.dropna(subset=["open", "high", "low", "close"]).reset_index()
        return self._finalize_ohlcv(out)

    # ---------- Public API ----------
    def _slice(
        self,
        df: pd.DataFrame,
        start: str | pd.Timestamp | None,
        end: str | pd.Timestamp | None,
    ) -> pd.DataFrame:
        if df.empty:
            return self._empty_ohlcv_df()

        s = self._to_utc_timestamp(start)
        e = self._to_utc_timestamp(end)

        out = df.copy()
        out["timestamp"] = self._normalize_timestamp_series(out["timestamp"])

        if s is not None:
            out = out[out["timestamp"] >= s]
        if e is not None:
            out = out[out["timestamp"] <= e]

        return out.reset_index(drop=True)

    def _candidate_base_intervals(self, requested_interval: str) -> list[str]:
        requested = self._preferred_base_interval(requested_interval)
        candidates = [requested]
        if requested_interval != "1m" and "1m" not in candidates:
            candidates.append("1m")
        return candidates

    def load_ohlcv(
        self,
        symbol: str,
        interval: str,
        market_type: str,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        symbol = self._normalize_symbol(symbol)
        interval = self._normalize_interval(interval)
        market_type = self._normalize_market_type(market_type)
        start_ts, end_ts = self._resolve_bounds(interval, start, end)

        request = _CoverageRequest(
            symbol=symbol,
            interval=interval,
            market_type=market_type,
            start=start_ts,
            end=end_ts,
        )

        diagnostics: list[str] = []

        for base_interval in self._candidate_base_intervals(interval):
            try:
                self._migrate_legacy_flat_parquet(symbol, base_interval, market_type)

                base_df = self._load_partitioned_range(
                    symbol=symbol,
                    interval=base_interval,
                    market_type=market_type,
                    start=request.start,
                    end=request.end,
                )

                if base_df.empty:
                    legacy_df = self._load_legacy_flat_range(symbol, base_interval, market_type, request.start, request.end)
                    if not legacy_df.empty:
                        self._write_partitioned_df(symbol, base_interval, market_type, legacy_df)
                        base_df = self._load_partitioned_range(
                            symbol=symbol,
                            interval=base_interval,
                            market_type=market_type,
                            start=request.start,
                            end=request.end,
                        )

                if not self._coverage_ok(base_df, request.start, request.end, base_interval):
                    notes = self._ensure_local_coverage(
                        symbol=symbol,
                        interval=base_interval,
                        market_type=market_type,
                        start=request.start,
                        end=request.end,
                    )
                    if notes:
                        diagnostics.extend([f"{base_interval}: {n}" for n in notes])

                    base_df = self._load_partitioned_range(
                        symbol=symbol,
                        interval=base_interval,
                        market_type=market_type,
                        start=request.start,
                        end=request.end,
                    )

                if base_df.empty:
                    diagnostics.append(
                        f"{base_interval}: no local/archive/rest coverage for "
                        f"{request.start} -> {request.end}"
                    )
                    continue

                if base_interval != interval:
                    out = self._resample_ohlcv(base_df, interval)
                else:
                    out = base_df.copy()

                out = self._slice(out, request.start, request.end)
                if not out.empty:
                    return out

                diagnostics.append(
                    f"{base_interval}: loaded source but final slice/resample was empty for "
                    f"{request.start} -> {request.end}"
                )
            except Exception as e:
                diagnostics.append(f"{base_interval}: {e}")

        raise RuntimeError(
            f"Failed to load historical OHLCV. "
            f"symbol={symbol} interval={interval} market_type={market_type} "
            f"start={request.start} end={request.end} details=" + " | ".join(diagnostics)
        )

    def export_to_csv(
        self,
        symbol: str,
        interval: str,
        market_type: str,
        out_path: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> str:
        df = self.load_ohlcv(symbol, interval, market_type, start=start, end=end)
        if out_path is None:
            out_path = os.path.join(
                self.exp_dir,
                f"{symbol.upper()}_{interval}_{market_type.lower()}.csv",
            )
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        df.to_csv(out_path, index=False)
        return out_path
