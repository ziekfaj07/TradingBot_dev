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
    position_sizing_mode: str = "all_in",
    position_size_value: float | None = None,
    max_drawdown_pct: float | None = None,
    max_trades_per_day: int | None = None,
    cooldown_seconds: int = 0,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    exit_mode: str = "static",
    atr_period: int = 14,
    atr_stop_mult: float | None = None,
    atr_take_mult: float | None = None,
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
        position_sizing_mode=position_sizing_mode,
        position_size_value=position_size_value,
        max_drawdown_pct=max_drawdown_pct,
        max_trades_per_day=max_trades_per_day,
        cooldown_seconds=cooldown_seconds,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        exit_mode=exit_mode,
        atr_period=atr_period,
        atr_stop_mult=atr_stop_mult,
        atr_take_mult=atr_take_mult,
    )


@router.get("/api/backtest_equity/{symbol}")
def backtest_equity(
    symbol: str,
    interval: str = "1h",
    market_type: str = "spot",
    leverage: float = 1.0,
    stride: int = Query(5, ge=1, le=200),
    exit_mode: str = "static",
    atr_period: int = 14,
    atr_stop_mult: float | None = None,
    atr_take_mult: float | None = None,
):
    res = backtester.run_backtest(
        symbol=symbol,
        interval=interval,
        market_type=market_type,
        leverage=leverage,
        include_equity=True,
        equity_stride=stride,
        exit_mode=exit_mode,
        atr_period=atr_period,
        atr_stop_mult=atr_stop_mult,
        atr_take_mult=atr_take_mult,
    )
    return {
        "symbol": res["symbol"],
        "interval": res["interval"],
        "market_type": res["market_type"],
        "equity_curve": res.get("equity_curve", []),
        "risk_events": res.get("risk_events", []),
    }