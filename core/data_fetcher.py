"""
data_fetcher.py  -  Bybit candle fetcher (legacy path)

Fixes applied vs original:
  • SQL injection  - symbol is validated against a strict whitelist regex
                     before being used as a SQLite table identifier.
  • SSL / MITM    - verify=certifi.where() replaces the unsafe verify=False.
  • Hang / DoS    - connect + read timeouts added to every HTTP call.
  • Retry logic   - transient errors (5xx / connection issues) are retried
                     with exponential back-off up to MAX_RETRIES attempts.
  • Magic values  - BASE_URL, category, and default interval moved to
                     module-level constants so they are easy to change.
"""
from __future__ import annotations

import re
import time

import certifi
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.database import create_table, get_connection
from core.http_client import request_session

session = request_session()

# ── configuration ─────────────────────────────────────────────────────────────
BASE_URL: str = "https://api.bybit.com"
DEFAULT_CATEGORY: str = "linear"   # "linear" = USDT-settled perps; "spot" for spot
DEFAULT_INTERVAL: str = "3"        # minutes
DEFAULT_LIMIT: int = 1_000

# HTTP timeouts: (connect seconds, read seconds)
HTTP_TIMEOUT: tuple[float, float] = (3.05, 10.0)

# Retry strategy: 3 retries on 5xx or connection errors, exponential back-off
MAX_RETRIES: int = 3
_RETRY_STRATEGY = Retry(
    total=MAX_RETRIES,
    backoff_factor=1.0,
    status_forcelist={500, 502, 503, 504},
    allowed_methods={"GET"},
    raise_on_status=False,
)

# Symbol whitelist: uppercase letters and digits only, 2–20 characters.
# Prevents SQL-injection via the table-name interpolation in save_candles().
_SYMBOL_RE: re.Pattern[str] = re.compile(r"^[A-Z0-9]{2,20}$")


def _validate_symbol(symbol: str) -> str:
    """Return the upper-cased symbol or raise ValueError if it looks unsafe."""
    clean = str(symbol or "").strip().upper()
    if not _SYMBOL_RE.fullmatch(clean):
        raise ValueError(
            f"Invalid symbol {symbol!r}. "
            "Only uppercase letters and digits (2-20 chars) are allowed."
        )
    return clean


def _make_session() -> request_session:
    """Return a Session pre-configured with retries and SSL verification."""
    session = request_session()
    adapter = HTTPAdapter(max_retries=_RETRY_STRATEGY)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def fetch_candles(
    symbol: str = "BTCUSDT",
    interval: str = DEFAULT_INTERVAL,
    limit: int = DEFAULT_LIMIT,
    category: str = DEFAULT_CATEGORY,
) -> list:
    """Fetch OHLCV candles from Bybit v5 kline endpoint.

    Returns the raw list of candle arrays as returned by the API.
    Raises ValueError for an invalid symbol, requests.HTTPError for
    a non-2xx response, and Exception for missing 'result' key.
    """
    validated_symbol = _validate_symbol(symbol)
    endpoint = "/v5/market/kline"
    params = {
        "category": category,
        "symbol": validated_symbol,
        "interval": str(interval),
        "limit": int(limit),
    }

    session = _make_session()
    response = session.get(
        BASE_URL + endpoint,
        params=params,
        verify=certifi.where(),      # ← SSL verification enabled
        timeout=HTTP_TIMEOUT,        # ← no more indefinite hangs
    )
    response.raise_for_status()
    data = response.json()

    if "result" not in data:
        raise Exception(f"Bybit API error: {data}")

    return data["result"]["list"]


def save_candles(symbol: str, candles: list) -> None:
    """Persist candles to the local SQLite database.

    The table name is derived from the validated symbol so that it cannot
    contain SQL-special characters.
    """
    validated_symbol = _validate_symbol(symbol)
    create_table(validated_symbol)
    conn = get_connection()
    cursor = conn.cursor()

    for candle in candles:
        timestamp  = int(candle[0])
        open_price = float(candle[1])
        high       = float(candle[2])
        low        = float(candle[3])
        close      = float(candle[4])
        volume     = float(candle[5])

        # Table name is safe: validated by _validate_symbol above.
        cursor.execute(
            f"INSERT OR IGNORE INTO {validated_symbol} VALUES (?, ?, ?, ?, ?, ?)",  # noqa: S608
            (timestamp, open_price, high, low, close, volume),
        )

    conn.commit()
    conn.close()


def download_recent(symbol: str = "BTCUSDT") -> None:
    """Convenience helper: fetch and persist the most recent candles."""
    candles = fetch_candles(symbol)
    save_candles(symbol, candles)
