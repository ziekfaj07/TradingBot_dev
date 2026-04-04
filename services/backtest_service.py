import math
import numpy as np

from core.binance_vision_provider import BinanceVisionProvider
from core.execution_models import PortfolioState
from services.execution_engine import ExecutionEngine
from services.risk_engine import RiskEngine
from services.strategy_engine import StrategyEngine
from services.runner import run_signal_backed_loop


class BacktestService:
    def __init__(self):
        self.provider = BinanceVisionProvider()
        self.risk_engine = RiskEngine()

    def run_backtest(
        self,
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
        maintenance_margin: float = 0.005,
        include_equity: bool = False,
        equity_stride: int = 1,
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
        market_type = (market_type or "spot").lower()
        if market_type not in ("spot", "futures"):
            return {"error": "market_type must be 'spot' or 'futures'"}

        if market_type == "spot":
            leverage = 1.0
            allow_short = False

        df = self.provider.load_ohlcv(symbol, interval, market_type, start=start, end=end)
        if df is None or df.empty:
            return {"error": "No OHLCV data loaded"}

        df = StrategyEngine.ema_crossover(df)

        # remove lookahead bias
        df["signal"] = df["signal"].shift(1).fillna(0).astype(int)

        engine = ExecutionEngine(
            fee_rate=fee_rate,
            slippage_bps=slippage_bps,
            max_leverage=50.0,
            max_qty=10.0,
            maintenance_margin=maintenance_margin,
        )

        state = PortfolioState(cash=float(initial_balance))

        out = run_signal_backed_loop(
            df,
            engine=engine,
            state=state,
            market_type=market_type,
            leverage=leverage,
            include_equity=include_equity,
            equity_stride=equity_stride,
            risk_engine=self.risk_engine,
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

        trades = out.trades
        equity_curve = out.equity_curve
        liquidated = out.liquidated
        final_equity = out.final_equity
        risk_events = out.risk_events

        metrics = self._metrics(
            initial_balance,
            final_equity,
            equity_curve,
            trades,
            include_equity=include_equity,
        )

        resp = {
            "symbol": symbol.upper(),
            "interval": interval,
            "market_type": market_type,
            "start": start,
            "end": end,
            "config": {
                "initial_balance": initial_balance,
                "fee_rate": fee_rate,
                "slippage_bps": slippage_bps,
                "allow_short": allow_short,
                "leverage": leverage,
                "maintenance_margin": maintenance_margin,
                "include_equity": include_equity,
                "equity_stride": equity_stride,
                "position_sizing_mode": position_sizing_mode,
                "position_size_value": position_size_value,
                "max_drawdown_pct": max_drawdown_pct,
                "max_trades_per_day": max_trades_per_day,
                "cooldown_seconds": cooldown_seconds,
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct,
                "exit_mode": exit_mode,
                "atr_period": atr_period,
                "atr_stop_mult": atr_stop_mult,
                "atr_take_mult": atr_take_mult,
            },
            "results": metrics,
            "liquidated": liquidated,
            "risk_events": risk_events,
            "trades": trades[-200:],
        }

        if include_equity:
            resp["equity_curve"] = equity_curve

        return resp

    def _metrics(self, initial_balance, final_equity, equity_curve, trades, include_equity: bool):
        total_return = (final_equity / initial_balance - 1.0) * 100.0

        if include_equity and equity_curve:
            eq = np.array([x["equity"] for x in equity_curve], dtype=float)
        else:
            series = [float(initial_balance)]
            for t in trades:
                if t.get("type") in ("EXIT", "LIQUIDATION"):
                    series.append(float(t.get("equity_after", series[-1])))
            eq = np.array(series, dtype=float)

        if len(eq) < 2:
            return {
                "initial_balance": round(initial_balance, 2),
                "final_equity": round(final_equity, 2),
                "total_return_percent": round(total_return, 2),
                "max_drawdown_percent": 0.0,
                "total_trades": 0,
                "win_rate_percent": 0.0,
                "profit_factor": 0.0,
                "sharpe": 0.0,
            }

        peaks = np.maximum.accumulate(eq)
        drawdowns = (peaks - eq) / np.where(peaks == 0, 1, peaks)
        max_dd = float(np.max(drawdowns)) * 100.0

        pnls = []
        for t in trades:
            if t.get("type") in ("EXIT", "LIQUIDATION") and t.get("pnl") is not None:
                pnls.append(float(t["pnl"]))

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        total_trades = len(pnls)
        win_rate = (len(wins) / total_trades * 100.0) if total_trades else 0.0
        gross_profit = float(np.sum(wins)) if wins else 0.0
        gross_loss = float(np.sum(np.abs(losses))) if losses else 0.0

        if gross_loss == 0 and gross_profit > 0:
            profit_factor = float("inf")
        elif gross_loss == 0:
            profit_factor = 0.0
        else:
            profit_factor = gross_profit / gross_loss

        rets = np.diff(eq) / np.where(eq[:-1] == 0, 1, eq[:-1])
        if np.std(rets) > 0:
            sharpe = float(np.mean(rets) / np.std(rets)) * math.sqrt(365)
        else:
            sharpe = 0.0

        return {
            "initial_balance": round(initial_balance, 2),
            "final_equity": round(final_equity, 2),
            "total_return_percent": round(total_return, 2),
            "max_drawdown_percent": round(max_dd, 2),
            "total_trades": total_trades,
            "win_rate_percent": round(win_rate, 2),
            "profit_factor": ("inf" if profit_factor == float("inf") else round(profit_factor, 2)),
            "sharpe": round(sharpe, 2),
        }