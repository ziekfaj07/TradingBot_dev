import random
import re
import string
from datetime import datetime

from core.market_types import is_derivatives_market

"""
This whole section creates naming covention for Run ID and csv filename

AAAAAAA-BCD-yymmdd-hhmm-EEEEE

  AAAAAAA - trading pair (ticker)
  B - for market type (S - spot, F - futures)
  C - for mode (L - live, P - paper, B - Backtest, D - demo)
  D - if SHORT whether true or false (F - short=false, T - short=true)
  yyyymmdd - year month day
  hhmm - hour minute
  EEEEE - 5 digit random alpha-numeric tag

  example: SOLUSDC-SLST-20260324-1430-d8kjd (this means: the run was of SOLUSDC pair, it was live trading on spot market with SHORT ON, done today at 2:30pm)
"""
def sanitize_ticker(symbol: str) -> str:
    """
    Convert trading pair into uppercase alphanumeric-only token.

    Examples:
    - BTC/USDT -> BTCUSDT
    - sol_usdc -> SOLUSDC
    - ETH-USDT -> ETHUSDT
    """
    symbol = (symbol or "").upper()
    symbol = re.sub(r"[^A-Z0-9]", "", symbol)
    return symbol or "UNKNOWN"


def market_code(market_type: str) -> str:
    return "F" if is_derivatives_market(market_type) else "S"


def mode_code(mode: str) -> str:
    value = str(mode).lower()
    if value == "live":
        return "L"
    if value == "paper":
        return "P"
    if value == "backtest":
        return "B"
    return "D"


def short_code(allow_short: bool) -> str:
    return "T" if bool(allow_short) else "F"


def make_short_tag(length: int = 5) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choices(alphabet, k=length))


def make_run_id(
    symbol: str,
    market_type: str,
    mode: str,
    allow_short: bool,
    started_at: datetime | None = None,
    tag: str | None = None,
) -> str:
    dt = started_at or datetime.now()
    ticker = sanitize_ticker(symbol)
    m = market_code(market_type)
    r = mode_code(mode)
    s = short_code(allow_short)
    ts = dt.strftime("%Y%m%d-%H%M")
    suffix = tag or make_short_tag(5)
    return f"{ticker}-{m}{r}{s}-{ts}-{suffix}"


def csv_filename_from_run_id(run_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(run_id))
    return f"{safe}-fills.csv"