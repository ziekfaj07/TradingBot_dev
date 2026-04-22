from core.binance_vision_provider import BinanceVisionProvider
from core.execution_models import PortfolioState
from core.market_types import is_derivatives_market, is_spot_market, market_type_error_label, normalize_market_type
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
        enable_volatility_scaling: bool,
        volatility_target_pct: float | None,
        min_volatility_scale: float | None,
        max_volatility_scale: float | None,
        debug_risk_telemetry: bool,
        stop_loss_pct: float | None,
        take_profit_pct: float | None,
        exit_on_signal: bool | None = None,
        exit_mode: str,
        atr_period: int,
        atr_stop_mult: float | None,
        atr_take_mult: float | None,
        atr_reference_mode: str = "entry",
    ) -> str | None:
        mt = normalize_market_type(market_type)
        if not (is_spot_market(mt) or is_derivatives_market(mt)):
            return market_type_error_label()

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

        valid_sizing_modes = {
            "all_in",
            "fixed_usdt",
            "fixed_pct",
            "equity_pct",
            "risk_pct",
        }
        if sizing_mode not in valid_sizing_modes:
            return (
                "position_sizing_mode must be one of: "
                "all_in, fixed_usdt, fixed_pct, equity_pct, risk_pct"
            )

        if sizing_mode in {"fixed_usdt", "fixed_pct", "equity_pct", "risk_pct"}:
            if position_size_value is None or float(position_size_value) <= 0.0:
                return f"position_size_value must be > 0 for sizing mode '{sizing_mode}'"

        if sizing_mode == "risk_pct":
            normalized_exit_mode = str(exit_mode or "static").strip().lower()
            if normalized_exit_mode == "atr":
                if atr_stop_mult is None or float(atr_stop_mult) <= 0.0:
                    return "risk_pct requires atr_stop_mult > 0 when exit_mode='atr'"
            else:
                if stop_loss_pct is None or float(stop_loss_pct) <= 0.0:
                    return "risk_pct requires stop_loss_pct > 0 when exit_mode='static'"

        if enable_volatility_scaling:
            if volatility_target_pct is None or float(volatility_target_pct) <= 0.0:
                return "volatility_target_pct must be > 0 when enable_volatility_scaling=true"

            if min_volatility_scale is not None and float(min_volatility_scale) <= 0.0:
                return "min_volatility_scale must be > 0 when provided"

            if max_volatility_scale is not None and float(max_volatility_scale) <= 0.0:
                return "max_volatility_scale must be > 0 when provided"

            if (
                min_volatility_scale is not None
                and max_volatility_scale is not None
                and float(min_volatility_scale) > float(max_volatility_scale)
            ):
                return "min_volatility_scale cannot be greater than max_volatility_scale"

        if stop_loss_pct is not None and float(stop_loss_pct) <= 0.0:
            return "stop_loss_pct must be > 0"
        if take_profit_pct is not None and float(take_profit_pct) <= 0.0:
            return "take_profit_pct must be > 0"
        if not isinstance(exit_on_signal, bool):
            return "exit_on_signal must be true or false"        

        normalized_exit_mode = str(exit_mode or "static").strip().lower()
        if normalized_exit_mode not in {"static", "atr"}:
            return "exit_mode must be 'static' or 'atr'"

        normalized_atr_reference_mode = str(atr_reference_mode or "entry").strip().lower()
        if normalized_atr_reference_mode not in {"entry", "floating"}:
            return "atr_reference_mode must be 'entry' or 'floating'"

        needs_atr = normalized_exit_mode == "atr" or bool(enable_volatility_scaling)

        if needs_atr:
            if int(atr_period) <= 0:
                return "atr_period must be > 0 when ATR is required"
            if atr_stop_mult is not None and float(atr_stop_mult) <= 0.0:
                return "atr_stop_mult must be > 0 when provided"
            if atr_take_mult is not None and float(atr_take_mult) <= 0.0:
                return "atr_take_mult must be > 0 when provided"

        if is_spot_market(mt):
            if allow_short:
                return "allow_short is only supported for derivatives markets"
            if leverage != 1.0:
                return "spot backtests must use leverage=1"
            if mmode != "isolated":
                return "margin_mode is only meaningful for derivatives markets"
            if enable_liquidation:
                return "enable_liquidation must be false for spot"
            if use_mark_price_for_liquidation:
                return "use_mark_price_for_liquidation must be false for spot"
            if maintenance_margin_override is not None:
                return "maintenance_margin_override is only valid for derivatives markets"

        if is_derivatives_market(mt):
            if leverage > 50.0:
                return "leverage exceeds current engine max of 50"
            
        if not isinstance(debug_risk_telemetry, bool):
            return "debug_risk_telemetry must be true or false"            

        return None

    def run_backtest(
        self,
        symbol: str,
        strategy_name: str = "ema_crossover",
        strategy_params: dict | None = None,
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
        enable_volatility_scaling: bool = False,
        volatility_target_pct: float | None = None,
        min_volatility_scale: float | None = 0.50,
        max_volatility_scale: float | None = 1.50,
        debug_risk_telemetry: bool = False,
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
        atr_reference_mode: str = "entry",
        margin_mode: str = "cross",
        enable_liquidation: bool = True,
        use_mark_price_for_liquidation: bool = True,
        mark_price_source: str = "close",
        liquidation_fee_rate: float = 0.005,
        maintenance_margin_override: float | None = None,
    ):
        market_type = normalize_market_type(market_type)
        margin_mode = str(margin_mode or "isolated").strip().lower()
        mark_price_source = str(mark_price_source or "close").strip().lower()

        if is_spot_market(market_type):
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
            enable_volatility_scaling=enable_volatility_scaling,
            volatility_target_pct=volatility_target_pct,
            min_volatility_scale=min_volatility_scale,
            max_volatility_scale=max_volatility_scale,
            debug_risk_telemetry=debug_risk_telemetry,            
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            exit_on_signal=exit_on_signal,            
            exit_mode=exit_mode,
            atr_period=atr_period,
            atr_stop_mult=atr_stop_mult,
            atr_take_mult=atr_take_mult,
            atr_reference_mode=atr_reference_mode,
        )
        if validation_error:
            return {"error": validation_error}

        effective_mm_override = normalize_maintenance_margin_override(maintenance_margin_override)

        df = self.provider.load_ohlcv(symbol, interval, market_type, start=start, end=end)
        if df is None or df.empty:
            return {"error": "No OHLCV data loaded"}

        df = StrategyEngine.apply(df, strategy_name, strategy_params or {})
        df["signal"] = df["signal"].shift(1).fillna(0).astype(int)

        if not is_derivatives_market(market_type):
            df.loc[df["signal"] < 0, "signal"] = 0
        elif not allow_short:
            # keep -1 only as long exit; do not allow flat-to-short entries
            pass

        engine = ExecutionEngine(
            fee_rate=fee_rate,
            liquidation_fee_rate=liquidation_fee_rate,
            slippage_bps=slippage_bps,
            max_leverage=50.0,
            max_qty=10.0,
            maintenance_margin=maintenance_margin,
        )

        state = PortfolioState(
            cash=float(initial_balance),
            margin_mode=str(margin_mode or "cross"),
        )

        out = run_signal_backed_loop(
            df,
            engine=engine,
            state=state,
            market_type=market_type,
            leverage=leverage,
            interval=interval,
            allow_short=allow_short,            
            include_equity=include_equity,
            equity_stride=equity_stride,
            risk_engine=self.risk_engine,
            position_sizing_mode=position_sizing_mode,
            position_size_value=position_size_value,
            enable_volatility_scaling=enable_volatility_scaling,
            volatility_target_pct=volatility_target_pct,
            min_volatility_scale=min_volatility_scale,
            max_volatility_scale=max_volatility_scale,
            debug_risk_telemetry=debug_risk_telemetry,
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
            atr_reference_mode=atr_reference_mode,
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

        metrics = out.stats.to_dict()

        resp = {
            "symbol": symbol.upper(),
            "strategy_name": strategy_name,
            "strategy_params": strategy_params or {},
            "interval": interval,
            "market_type": market_type,
            "start": start,
            "end": end,
            "config": {
                "strategy_name": strategy_name,
                "strategy_params": strategy_params or {},
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
                "enable_volatility_scaling": enable_volatility_scaling,
                "volatility_target_pct": volatility_target_pct,
                "min_volatility_scale": min_volatility_scale,
                "max_volatility_scale": max_volatility_scale,
                "debug_risk_telemetry": debug_risk_telemetry,                
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
                "atr_reference_mode": atr_reference_mode, 
                "margin_mode": margin_mode,
                "enable_liquidation": enable_liquidation,
                "use_mark_price_for_liquidation": use_mark_price_for_liquidation,
                "mark_price_source": mark_price_source,
                "liquidation_fee_rate": liquidation_fee_rate,
            },
            "results": metrics,
            "liquidated": liquidated,
            "liquidation_count": out.stats.liquidation_count,
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
