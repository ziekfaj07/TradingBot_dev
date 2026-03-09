from fastapi import APIRouter, Query
from services.backtest_service import BacktestService

router = APIRouter()
backtester = BacktestService()

@router.get("/api/backtest/{symbol}")
def run_backtest(
    symbol: str,
    interval: str = "1h",
    market_type: str = "spot",
    start: str | None = None,
    end: str | None = None,
    initial_balance: float = 1000.0,
    fee_rate: float = 0.001,
    slippage_bps: float = 2.0,
    allow_short: bool = False,
    leverage: float = 1.0,
):
    return backtester.run_backtest(
        symbol=symbol,
        interval=interval,
        market_type=market_type,
        start=start,
        end=end,
        initial_balance=initial_balance,
        fee_rate=fee_rate,
        slippage_bps=slippage_bps,
        allow_short=allow_short,
        leverage=leverage,
    )


@router.get("/api/backtest_equity/{symbol}")
def backtest_equity(
    symbol: str,
    interval: str = "1h",
    market_type: str = "spot",
    leverage: float = 1.0,
    stride: int = Query(5, ge=1, le=200),
):
    # returns equity curve only (plus minimal metadata)
    res = backtester.run_backtest(
        symbol=symbol,
        interval=interval,
        market_type=market_type,
        leverage=leverage,
        include_equity=True,
        equity_stride=stride,
    )
    return {
        "symbol": res["symbol"],
        "interval": res["interval"],
        "market_type": res["market_type"],
        "equity_curve": res.get("equity_curve", []),
    }