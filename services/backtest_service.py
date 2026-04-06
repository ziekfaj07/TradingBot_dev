import math

import numpy as np

from core.binance_vision_provider import BinanceVisionProvider
from core.execution_models import PortfolioState
from services.execution_engine import ExecutionEngine
from services.margin_engine import normalize_maintenance_margin_override
from services.risk_engine import RiskEngine
from services.strategy_engine import StrategyEngine
from services.runner import run_signal_backed_loop


class BacktestService:
    def __init__(self):
        self.provider = BinanceVisionProvider()
        self.risk_engine = RiskEngine()

    def _validate_backtest_config(
        self,
        *,
        market_type: str,
        allow_short: bool,
        leverage: float,
        margin_mode: str,
        enable_liquidation: bool,
        use_mark_price_for_liquidation: bool,
        mark_price_source: str,
        liquidation_fee_rate: float,
        maintenance_margin_override: float | None,
        position_sizing_mode: str,
        position_size_value: float | None,
        stop_loss_pct: float | None,
        take_profit_pct: float | None,
        exit_on_signal: bool | None = None,
        exit_mode: str,
        atr_period: int,
        atr_stop_mult: float | None,
        atr_take_mult: float | None,
    ) -> str | None:
        mt = str(market_type or "spot").strip().lower()
        if mt not in ("spot", "futures"):
            return "market_type must be 'spot' or 'futures'"

        mmode = str(margin_mode or "isolated").strip().lower()
        if mmode not in ("isolated", "cross"):
            return "margin_mode must be 'isolated' or 'cross'"

        mps = str(mark_price_source or "close").strip().lower()
        if mps not in ("close", "hlc3", "ohlc4"):
            return "mark_price_source must be one of: close, hlc3, ohlc4"

        if leverage < 1.0:
            return "leverage must be >= 1.0"

        if liquidation_fee_rate < 0.0:
            return "liquidation_fee_rate must be >= 0"

        if maintenance_margin_override is not None:
            try:
                normalize_maintenance_margin_override(maintenance_margin_override)
            except ValueError as exc:
                return str(exc)

        sizing_mode = str(position_sizing_mode or "all_in").strip().lower()
        if sizing_mode in {"fixed_usdt", "fixed_notional", "fixed_qty"}:
            if position_size_value is None or float(position_size_value) <= 0.0:
                return f"position_size_value must be > 0 for sizing mode '{sizing_mode}'"

        if stop_loss_pct is not None and float(stop_loss_pct) <= 0.0:
            return "stop_loss_pct must be > 0"
        if take_profit_pct is not None and float(take_profit_pct) <= 0.0:
            return "take_profit_pct must be > 0"
        if not isinstance(exit_on_signal, bool):
            return "exit_on_signal must be true or false"        

        normalized_exit_mode = str(exit_mode or "static").strip().lower()
        if normalized_exit_mode not in {"static", "atr"}:
            return "exit_mode must be 'static' or 'atr'"

        if normalized_exit_mode == "atr":
            if int(atr_period) <= 0:
                return "atr_period must be > 0 for ATR exit mode"
            if atr_stop_mult is not None and float(atr_stop_mult) <= 0.0:
                return "atr_stop_mult must be > 0 when provided"
            if atr_take_mult is not None and float(atr_take_mult) <= 0.0:
                return "atr_take_mult must be > 0 when provided"

        if mt == "spot":
            if allow_short:
                return "allow_short is only supported for futures"
            if leverage != 1.0:
                return "spot backtests must use leverage=1"
            if mmode != "isolated":
                return "margin_mode is only meaningful for futures"
            if enable_liquidation:
                return "enable_liquidation must be false for spot"
            if use_mark_price_for_liquidation:
                return "use_mark_price_for_liquidation must be false for spot"
            if maintenance_margin_override is not None:
                return "maintenance_margin_override is only valid for futures"

        if mt == "futures":
            if leverage > 50.0:
                return "leverage exceeds current engine max of 50"

        return None

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
        exit_on_signal: bool = True,
        exit_mode: str = "static",
        atr_period: int = 14,
        atr_stop_mult: float | None = None,
        atr_take_mult: float | None = None,
        margin_mode: str = "isolated",
        enable_liquidation: bool = True,
        use_mark_price_for_liquidation: bool = True,
        mark_price_source: str = "close",
        liquidation_fee_rate: float = 0.005,
        maintenance_margin_override: float | None = None,
    ):
        market_type = (market_type or "spot").lower()
        margin_mode = str(margin_mode or "isolated").strip().lower()
        mark_price_source = str(mark_price_source or "close").strip().lower()

        if market_type == "spot":
            leverage = 1.0
            allow_short = False
            margin_mode = "isolated"
            enable_liquidation = False
            use_mark_price_for_liquidation = False
            mark_price_source = "close"
        if market_type == "spot":
            leverage = 1.0
            allow_short = False
            margin_mode = "isolated"
            enable_liquidation = False
            use_mark_price_for_liquidation = False
            mark_price_source = "close"
            exit_on_signal = True
            
        validation_error = self._validate_backtest_config(
            market_type=market_type,
            allow_short=allow_short,
            leverage=leverage,
            margin_mode=margin_mode,
            enable_liquidation=enable_liquidation,
            use_mark_price_for_liquidation=use_mark_price_for_liquidation,
            mark_price_source=mark_price_source,
            liquidation_fee_rate=liquidation_fee_rate,
            maintenance_margin_override=maintenance_margin_override,
            position_sizing_mode=position_sizing_mode,
            position_size_value=position_size_value,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            exit_on_signal=exit_on_signal,            
            exit_mode=exit_mode,
            atr_period=atr_period,
            atr_stop_mult=atr_stop_mult,
            atr_take_mult=atr_take_mult,
        )
        if validation_error:
            return {"error": validation_error}

        effective_mm_override = normalize_maintenance_margin_override(maintenance_margin_override)

        df = self.provider.load_ohlcv(symbol, interval, market_type, start=start, end=end)
        if df is None or df.empty:
            return {"error": "No OHLCV data loaded"}

        df = StrategyEngine.ema_crossover(df)
        df["signal"] = df["signal"].shift(1).fillna(0).astype(int)

        if market_type != "futures":
            df.loc[df["signal"] < 0, "signal"] = 0
        elif not allow_short:
            # keep -1 only as long exit; do not allow flat-to-short entries
            pass

        engine = ExecutionEngine(
            fee_rate=fee_rate,
            slippage_bps=slippage_bps,
            max_leverage=50.0,
            max_qty=10.0,
            maintenance_margin=maintenance_margin,
            liquidation_fee_rate=liquidation_fee_rate,
        )

        state = PortfolioState(
            cash=float(initial_balance),
            margin_mode=margin_mode,
        )

        out = run_signal_backed_loop(
            df,
            engine=engine,
            state=state,
            market_type=market_type,
            leverage=leverage,
            allow_short=allow_short,            
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
            exit_on_signal=exit_on_signal,            
            exit_mode=exit_mode,
            atr_period=atr_period,
            atr_stop_mult=atr_stop_mult,
            atr_take_mult=atr_take_mult,
            margin_mode=margin_mode,
            enable_liquidation=enable_liquidation,
            use_mark_price_for_liquidation=use_mark_price_for_liquidation,
            mark_price_source=mark_price_source,
            maintenance_margin_override=effective_mm_override,
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
            liquidation_count=out.liquidation_count,
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
                "maintenance_margin_override": effective_mm_override,
                "include_equity": include_equity,
                "equity_stride": equity_stride,
                "position_sizing_mode": position_sizing_mode,
                "position_size_value": position_size_value,
                "max_drawdown_pct": max_drawdown_pct,
                "max_trades_per_day": max_trades_per_day,
                "cooldown_seconds": cooldown_seconds,
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct,
                "exit_on_signal": exit_on_signal,                
                "exit_mode": exit_mode,
                "atr_period": atr_period,
                "atr_stop_mult": atr_stop_mult,
                "atr_take_mult": atr_take_mult,
                "margin_mode": margin_mode,
                "enable_liquidation": enable_liquidation,
                "use_mark_price_for_liquidation": use_mark_price_for_liquidation,
                "mark_price_source": mark_price_source,
                "liquidation_fee_rate": liquidation_fee_rate,
            },
            "results": metrics,
            "liquidated": liquidated,
            "liquidation_count": out.liquidation_count,
            "max_margin_ratio": out.max_margin_ratio,
            "closest_liquidation_distance_pct": out.closest_liquidation_distance_pct,
            "last_margin_snapshot": out.last_margin_snapshot,
            "risk_events": risk_events,
            "trades": trades[-200:],
            "final_state": out.state.to_dict(),
        }

        if include_equity:
            resp["equity_curve"] = equity_curve

        return resp

    def _metrics(
        self,
        initial_balance,
        final_equity,
        equity_curve,
        trades,
        *,
        include_equity: bool,
        liquidation_count: int = 0,
    ):
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
                "liquidation_count": int(liquidation_count),
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
            "liquidation_count": int(liquidation_count),
        }