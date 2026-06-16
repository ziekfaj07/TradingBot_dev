from __future__ import annotations

from datetime import datetime

import asyncio
import math
import os
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, cast

import pandas as pd

from core.binance_vision_provider import BinanceVisionProvider
from core.database import (
    count_runtime_equity_snapshots,
    count_runtime_fills,
    delete_runtime_run,
    export_runtime_equity_csv,
    export_runtime_fills_csv,
    get_latest_paper_run,
    insert_runtime_equity_snapshot,
    insert_runtime_fill,
    init_runtime_db,
    load_runtime_equity_snapshots,
    load_runtime_fills,
    load_runtime_fills_ascending,
    save_runtime_snapshot,
    update_run_state,
    upsert_runtime_run,
)
from core.indicators import latest_atr_value
from core.execution_models import Fill, PortfolioState
from core.market_types import is_derivatives_market, normalize_market_type
from core.run_naming import make_run_id
from services.execution_engine import ExecutionEngine
from services.exchange_service import resolve_exchange_env_names, validate_live_config
from services.live_execution_service import LiveExecutionService
from services.margin_engine import evaluate_position_margin
from services.market_data_service import CoinGeckoService, GateIOService
from services.risk_engine import RiskEngine
from services.runner import run_signal_backed_loop
from services.strategy_engine import StrategyEngine
from services.ws_manager import ws_manager


class Mode(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    DEMO = "demo"
    LIVE = "live"


class EngineState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class RunConfig:
    symbol: str = "BTCUSDT"
    interval: str = "1m"
    market_type: str = "spot"
    start: str | None = None
    end: str | None = None
    initial_balance: float = 1000.0
    fee_rate: float = 0.001
    slippage_bps: float = 2.0
    allow_short: bool = False
    leverage: float = 1.0
    maintenance_margin: float = 0.005
    max_leverage: float = 50.0
    max_qty: float = 10.0

    position_sizing_mode: str = "all_in"
    position_size_value: float | None = None

    enable_volatility_scaling: bool = False
    volatility_target_pct: float | None = None
    min_volatility_scale: float | None = 0.50
    max_volatility_scale: float | None = 1.50    

    max_drawdown_pct: float | None = None
    max_trades_per_day: int | None = None
    cooldown_seconds: int = 0

    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    exit_on_signal: bool = True
    exit_mode: str = "static"   # "static" | "atr"
    
    atr_period: int = 14
    atr_stop_mult: float = 1.5
    atr_take_mult: float = 2.5
    atr_reference_mode: str = "entry"  # "entry" | "floating"

    # v0.6.4 / v0.6.4.1 liquidation + margin config
    margin_mode: str = "cross"
    enable_liquidation: bool = True
    use_mark_price_for_liquidation: bool = True
    mark_price_source: str = "close"
    liquidation_fee_rate: float | None = None
    maintenance_margin_override: float | None = None    

    include_equity: bool = False
    equity_stride: int = 1
    include_trades: bool = True
    include_risk_events: bool = True
    debug_risk_telemetry: bool = False
    max_equity_points: int | None = 2000
    max_trades_returned: int | None = None
    max_risk_events_returned: int | None = None

    poll_seconds: float = 5.0
    bar_confirmations: int = 1
    max_reconnect_attempts: int = 8
    reconnect_backoff_base: float = 1.5
    dedupe_fill_window: int = 20

    ema_short: int = 9
    ema_long: int = 21

    strategy_name: str = "ema_crossover"
    strategy_params: dict = field(default_factory=dict)

    candle_limit: int = 300
    debug_stream: bool = True

    exchange_name: str = "gateio"
    exchange_api_key_env: str = "GATEIO_API_KEY"
    exchange_api_secret_env: str = "GATEIO_API_SECRET"
    exchange_api_passphrase_env: str | None = "GATEIO_API_PASSPHRASE"
    exchange_testnet: bool = False
    exchange_base_url: str | None = None
    exchange_settle_currency: str = "usdt"
    enable_live_trading: bool = False
    live_dry_run: bool = True
    sync_positions_on_start: bool = True
    cancel_open_orders_on_stop: bool = False
    client_order_id_prefix: str = "tb"
    live_poll_seconds: float = 3.0


@dataclass
class RunStatus:
    mode: Mode = Mode.BACKTEST
    state: EngineState = EngineState.IDLE
    run_id: Optional[str] = None
    run_label: Optional[str] = None
    started_at: Optional[float] = None
    stopped_at: Optional[float] = None
    last_error: Optional[str] = None
    config: RunConfig = field(default_factory=RunConfig)


class ModeController:
    def __init__(self):
        self.provider = BinanceVisionProvider()
        self.market_data = GateIOService()
        self.fallback_data = CoinGeckoService()

        self._lock = asyncio.Lock()
        self._status = RunStatus()

        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

        self._engine: Optional[ExecutionEngine] = None
        self._live_service: Optional[LiveExecutionService] = None
        self._state: Optional[PortfolioState] = None
        self._trade_id: int = 0

        self._latest_bar: dict[str, Any] | None = None
        self._bars: list[dict[str, Any]] = []
        self._last_processed_bar_ts: int | None = None
        self._last_signal: int = 0
        self._fills: list[Fill] = []
        self._equity_points: list[dict[str, Any]] = []
        self._trace: list[dict[str, Any]] = []
        self._paper_metrics_cache: dict[str, Any] | None = None
        self._paper_metrics_dirty: bool = True

        self._risk_engine = RiskEngine()
        self._peak_equity: float = 0.0
        self._trades_today: int = 0
        self._trade_day_key: str | None = None
        self._last_exit_ts: float | None = None
        self._risk_halt_reason: str | None = None
        self._position_peak_price: float | None = None        

        self._recent_fill_keys: list[str] = []
        self._recent_fill_key_set: set[str] = set()
        self._reconnect_attempts: int = 0
        self._last_fetch_error: str | None = None

        init_runtime_db()
        self._restore_paper_session()

    async def set_mode(self, mode: Mode) -> dict:
        async with self._lock:
            if self._status.state in (
                EngineState.STARTING,
                EngineState.RUNNING,
                EngineState.STOPPING,
            ):
                raise RuntimeError("Cannot change mode while engine is running.")

            self._status.mode = mode
            self._status.last_error = None
            self._persist_status()
            return self.status()

    async def configure(self, **kwargs) -> dict:
        async with self._lock:
            if self._status.state != EngineState.IDLE:
                raise RuntimeError("Can only configure while engine is idle.")

            config_dict = asdict(self._status.config)

            if kwargs.get("market_type") is not None:
                kwargs["market_type"] = self._backend_market_type(str(kwargs["market_type"]))

            kwargs = self._apply_mode_derived_execution_config(kwargs)

            if self._status.mode == Mode.PAPER:
                self._prepare_fresh_idle_config_locked()

            strategy_name = (
                kwargs.get("strategy_name")
                or self._status.config.strategy_name
                or "ema_crossover"
            )
            strategy_name = str(strategy_name).strip().lower()

            incoming = dict(kwargs.get("strategy_params", {}) or {})
            incoming.pop("additionalProp1", None)

            if "look_back" in incoming:
                incoming["lookback"] = incoming.pop("look_back")

            if "confirm_break_previous_extreme" in incoming:
                incoming["confirm_break_prev_extreme"] = incoming.pop(
                    "confirm_break_previous_extreme"
                )

            if kwargs.get("ema_short") is not None:
                incoming["short"] = int(kwargs["ema_short"])
            if kwargs.get("ema_long") is not None:
                incoming["long"] = int(kwargs["ema_long"])

            def add_volume_spike_defaults(params: dict, source: dict) -> dict:
                params["volume_spike_mult"] = source.get("volume_spike_mult", 1.5)
                params["volume_spike_lookback"] = source.get("volume_spike_lookback", 20)
                params["volume_spike_mult"] = float(params["volume_spike_mult"])
                params["volume_spike_lookback"] = int(params["volume_spike_lookback"])
                return params

            if strategy_name in {"ema_crossover", "ema_crossover_v2"}:
                clean = {
                    "short": int(incoming.get("short", 9)),
                    "long": int(incoming.get("long", 21)),
                }
                if strategy_name == "ema_crossover_v2":
                    clean = add_volume_spike_defaults(clean, incoming)
            elif strategy_name in {"donchian_breakout", "donchian_breakout_v2"}:
                clean = {"lookback": int(incoming.get("lookback", 20))}
                if strategy_name == "donchian_breakout_v2":
                    clean = add_volume_spike_defaults(clean, incoming)
            elif strategy_name in {"three_candle_reversal", "three_candle_reversal_v2"}:
                clean = {
                    "min_body_ratio": float(incoming.get("min_body_ratio", 0.55)),
                    "require_full_range_engulf": bool(
                        incoming.get("require_full_range_engulf", True)
                    ),
                    "confirm_break_prev_extreme": bool(
                        incoming.get("confirm_break_prev_extreme", True)
                    ),
                }
                if strategy_name == "three_candle_reversal_v2":
                    clean = add_volume_spike_defaults(clean, incoming)
            elif strategy_name == "bollinger_mean_reversion_v2":
                clean = add_volume_spike_defaults(incoming, incoming)
            else:
                clean = incoming

            kwargs["strategy_name"] = strategy_name
            kwargs["strategy_params"] = clean

            for key, value in kwargs.items():
                if key in config_dict and value is not None:
                    setattr(self._status.config, key, value)

            self._status.last_error = None
            self._persist_status()
            self._persist_snapshot()
            return self.status()

    def _refresh_run_identity(
        self,
        cfg: RunConfig | None = None,
        *,
        started_at: float | None = None,
        mode: Mode | None = None,
    ) -> None:
        cfg = cfg or self._status.config
        effective_mode = mode or self._status.mode
        effective_started_at = (
            started_at if started_at is not None else self._status.started_at
        )

        if effective_started_at is None:
            effective_started_at = time.time()

        run_id = make_run_id(
            symbol=cfg.symbol,
            market_type=cfg.market_type,
            mode=effective_mode.value if hasattr(effective_mode, "value") else str(effective_mode),
            allow_short=cfg.allow_short,
            started_at=datetime.fromtimestamp(effective_started_at),
        )

        self._status.run_id = run_id
        self._status.run_label = run_id

    def _resolved_strategy_name_from_config(self, cfg: RunConfig) -> str:
        return (getattr(cfg, "strategy_name", None) or "ema_crossover").strip().lower()

    def _resolved_strategy_params_from_config(self, cfg: RunConfig) -> dict:
        params = dict(getattr(cfg, "strategy_params", {}) or {})
        strategy_name = self._resolved_strategy_name_from_config(cfg)

        if strategy_name in {"ema_crossover", "ema_crossover_v2"}:
            params.setdefault("short", int(getattr(cfg, "ema_short", 9)))
            params.setdefault("long", int(getattr(cfg, "ema_long", 21)))

        if strategy_name.endswith("_v2"):
            params.setdefault("volume_spike_mult", 1.5)
            params.setdefault("volume_spike_lookback", 20)

        return params

    def _prepare_fresh_idle_config_locked(self) -> None:
        self._status.run_id = None
        self._status.run_label = None
        self._status.started_at = None
        self._status.stopped_at = None
        self._status.last_error = None
        self._reset_runtime_memory()

    def _apply_strategy_to_df(self, df: pd.DataFrame, cfg: RunConfig) -> pd.DataFrame:
        return StrategyEngine.apply(
            df=df,
            strategy_name=self._resolved_strategy_name_from_config(cfg),
            strategy_params=self._resolved_strategy_params_from_config(cfg),
        )

    def _latest_signal_from_bars(self, bars: list[dict], cfg: RunConfig) -> int:
        if not bars:
            return 0

        df = pd.DataFrame(bars).copy()
        if df.empty or "close" not in df.columns:
            return 0

        return StrategyEngine.latest_signal(
            df=df,
            strategy_name=self._resolved_strategy_name_from_config(cfg),
            strategy_params=self._resolved_strategy_params_from_config(cfg),
        )

    async def start(self) -> dict:
        async with self._lock:
            if self._status.state != EngineState.IDLE:
                raise RuntimeError("Engine must be idle before starting.")

            if self._status.mode == Mode.BACKTEST:
                raise RuntimeError("Use run_backtest() for backtest mode.")

            if self._status.mode in (Mode.DEMO, Mode.LIVE):
                exchange_testnet = bool(self._status.config.exchange_testnet or self._status.mode == Mode.DEMO)
                dry_run_live = bool(self._status.config.live_dry_run if self._status.mode == Mode.LIVE else False)

                if self._status.mode == Mode.DEMO:
                    self._status.config.exchange_testnet = True

                if self._status.mode == Mode.LIVE and self._status.config.exchange_testnet:
                    raise RuntimeError(
                        "Live mode cannot start with exchange_testnet=true. "
                        "Use demo mode for testnet/demo exchange execution."
                    )
                
                if (
                    self._status.mode == Mode.LIVE
                    and self._status.config.enable_live_trading
                    and not self._status.config.live_dry_run
                    and not self._live_submit_env_armed()
                ):
                    raise RuntimeError(
                        "Live submit is blocked by backend safety guard. "
                        "To allow real live order submission, set "
                        "TRADINGBOT_ALLOW_LIVE_SUBMIT=true in .env and restart the server. "
                        "For live monitoring without real orders, use live_dry_run=true."
                    )                

                live_validation = validate_live_config(
                    exchange_name=self._status.config.exchange_name,
                    market_type=self._status.config.market_type,
                    symbol=self._status.config.symbol,
                    enable_live_trading=self._status.config.enable_live_trading,
                    dry_run_live=dry_run_live,
                    testnet=exchange_testnet,
                    base_url=self._status.config.exchange_base_url,
                    api_key_env=self._status.config.exchange_api_key_env,
                    api_secret_env=self._status.config.exchange_api_secret_env,
                    api_passphrase_env=self._status.config.exchange_api_passphrase_env,
                )
                if not live_validation.get("ok"):
                    errors = "; ".join(live_validation.get("errors", [])) or f"{self._status.mode.value} config invalid"
                    raise RuntimeError(f"{self._status.mode.value.capitalize()} startup blocked: {errors}")

            self._status.state = EngineState.STARTING

            self._status.started_at = time.time()
            self._status.stopped_at = None
            self._status.last_error = None

            self._refresh_run_identity(
                self._status.config,
                started_at=self._status.started_at,
                mode=self._status.mode,
            )

            self._reset_runtime_memory()
            self._build_runtime_objects()

            self._stop_event = asyncio.Event()
            self._persist_status()
            self._persist_snapshot()

            self._task = asyncio.create_task(self._run_loop())

            self._status.state = EngineState.RUNNING
            self._persist_status()

            if self._status.mode in (Mode.PAPER, Mode.DEMO):
                await self._broadcast_trace_event(
                    f"{self._status.mode.value}_started",
                    data={
                        "run_id": self._status.run_id,
                        "symbol": self._status.config.symbol,
                        "interval": self._status.config.interval,
                    },
                )

            return self.status()

    async def stop(self) -> dict:
        async with self._lock:
            if self._status.state not in (EngineState.STARTING, EngineState.RUNNING):
                raise RuntimeError("Engine is not running.")

            self._status.state = EngineState.STOPPING
            self._persist_status()
            self._stop_event.set()
            task = self._task

        if task:
            try:
                await task
            except asyncio.CancelledError:
                pass

        async with self._lock:
            self._task = None
            self._status.state = EngineState.IDLE
            self._status.stopped_at = time.time()
            self._persist_status()
            self._persist_snapshot()

            if self._status.mode in (Mode.PAPER, Mode.DEMO):
                await self._broadcast_trace_event(
                    f"{self._status.mode.value}_stopped",
                    data={"run_id": self._status.run_id},
                )

            return self.status()

    async def run_backtest(self, **overrides) -> dict:
        async with self._lock:
            if self._status.state != EngineState.IDLE:
                raise RuntimeError("Engine must be idle before running backtest.")

            cfg_dict = asdict(self._status.config)
            for key, value in overrides.items():
                if value is not None and key in cfg_dict:
                    cfg_dict[key] = value
            cfg = RunConfig(**cfg_dict)

            started_at = time.time()
            self._status.state = EngineState.STARTING
            self._status.started_at = started_at
            self._status.stopped_at = None
            self._status.last_error = None

            self._refresh_run_identity(
                cfg,
                started_at=started_at,
                mode=Mode.BACKTEST,
            )

            run_id = self._status.run_id
            run_label = self._status.run_label
            self._persist_status()

        try:
            df = self.provider.load_ohlcv(
                symbol=cfg.symbol,
                interval=cfg.interval,
                market_type=cfg.market_type,
                start=cfg.start,
                end=cfg.end,
            )

            if df.empty:
                raise RuntimeError(
                    f"No historical data returned for backtest. "
                    f"symbol={cfg.symbol} interval={cfg.interval} market_type={cfg.market_type} "
                    f"start={cfg.start} end={cfg.end} "
                    f"provider_root={self.provider.root_dir} hist_dir={self.provider.hist_dir}"
                )

            df = self._apply_strategy_to_df(df, cfg)
            df["signal"] = df["signal"].shift(1).fillna(0).astype(int)

            engine = ExecutionEngine(
                fee_rate=cfg.fee_rate,
                liquidation_fee_rate=cfg.liquidation_fee_rate,
                slippage_bps=cfg.slippage_bps,
                maintenance_margin=cfg.maintenance_margin,
                max_leverage=cfg.max_leverage,
                max_qty=cfg.max_qty,
            )

            state = PortfolioState(
                cash=cfg.initial_balance,
                position_qty=0.0,
                entry_price=None,
                side=None,
                equity=cfg.initial_balance,
                liquidation_price=None,
                realized_pnl=0.0,
                active_trade_id=None,
                margin=0.0,
                borrowed=0.0,
                margin_mode=cfg.margin_mode,
            )

            output = run_signal_backed_loop(
                df,
                engine=engine,
                state=state,
                market_type=cfg.market_type,
                leverage=cfg.leverage,
                interval=cfg.interval,
                allow_short=cfg.allow_short,                
                include_equity=cfg.include_equity,
                equity_stride=cfg.equity_stride,
                risk_engine=self._risk_engine,
                position_sizing_mode=cfg.position_sizing_mode,
                position_size_value=cfg.position_size_value,
                enable_volatility_scaling=cfg.enable_volatility_scaling,
                volatility_target_pct=cfg.volatility_target_pct,
                min_volatility_scale=cfg.min_volatility_scale,
                max_volatility_scale=cfg.max_volatility_scale,
                debug_risk_telemetry=cfg.debug_risk_telemetry,
                max_drawdown_pct=cfg.max_drawdown_pct,
                max_trades_per_day=cfg.max_trades_per_day,
                cooldown_seconds=cfg.cooldown_seconds,
                stop_loss_pct=cfg.stop_loss_pct,
                take_profit_pct=cfg.take_profit_pct,
                exit_on_signal=cfg.exit_on_signal,                
                exit_mode=cfg.exit_mode,
                atr_period=cfg.atr_period,
                atr_stop_mult=cfg.atr_stop_mult,
                atr_take_mult=cfg.atr_take_mult,
                atr_reference_mode=cfg.atr_reference_mode,
                margin_mode=cfg.margin_mode,
                enable_liquidation=cfg.enable_liquidation,
                use_mark_price_for_liquidation=cfg.use_mark_price_for_liquidation,
                mark_price_source=cfg.mark_price_source,
                maintenance_margin_override=cfg.maintenance_margin_override,
            )

            stopped_at = time.time()

            backtest_status = {
                "mode": Mode.BACKTEST.value,
                "state": EngineState.IDLE.value,
                "run_id": run_id,
                "run_label": run_label,
                "started_at": started_at,
                "stopped_at": stopped_at,
                "last_error": None,
                "config": asdict(cfg),
                "runtime": {
                    "has_engine": False,
                    "has_state": False,
                    "latest_bar": None,
                    "bar_count": int(len(df)),
                    "last_processed_bar_ts": None,
                    "last_signal": 0,
                    "fill_count": len(output.trades),
                    "equity_point_count": len(output.equity_curve),
                    "fills": [],
                    "latest_equity": output.final_equity,
                    "paper_state": None,
                    "paper_metrics": None,
                    "chart_symbol": cfg.symbol,
                    "chart_interval": cfg.interval,
                    "reconnect_attempts": 0,
                    "last_fetch_error": None,
                    "risk": {
                        "position_sizing_mode": cfg.position_sizing_mode,
                        "position_size_value": cfg.position_size_value,
                        "enable_volatility_scaling": cfg.enable_volatility_scaling,
                        "volatility_target_pct": cfg.volatility_target_pct,
                        "min_volatility_scale": cfg.min_volatility_scale,
                        "max_volatility_scale": cfg.max_volatility_scale,
                        "max_drawdown_pct": cfg.max_drawdown_pct,
                        "max_trades_per_day": cfg.max_trades_per_day,
                        "cooldown_seconds": cfg.cooldown_seconds,
                        "stop_loss_pct": cfg.stop_loss_pct,
                        "take_profit_pct": cfg.take_profit_pct,
                        "exit_mode": cfg.exit_mode,
                        "atr_period": cfg.atr_period,
                        "atr_stop_mult": cfg.atr_stop_mult,
                        "atr_take_mult": cfg.atr_take_mult,
                        "atr_reference_mode": cfg.atr_reference_mode,
                    },
                },
            }

            async with self._lock:
                self._status.state = EngineState.IDLE
                self._status.stopped_at = stopped_at
                self._status.last_error = None
                self._persist_status()

            return {
                "status": backtest_status,
                "result": {
                    "final_equity": output.final_equity,
                    "liquidated": output.liquidated,
                    "trades": output.trades,
                    "equity_curve": output.equity_curve,
                    "risk_events": output.risk_events,
                    "metrics": output.stats.to_dict(),                   
                },
            }

        except Exception as e:
            async with self._lock:
                self._status.state = EngineState.ERROR
                self._status.last_error = str(e)
                self._status.stopped_at = time.time()
                self._persist_status()
            raise

    def _ui_market_type(self, market_type: str | None = None) -> str:
        raw = (market_type or self._status.config.market_type or "spot").strip().lower()
        if raw in ("swap", "future", "futures", "perp", "perpetual"):
            return "futures"
        return "spot"

    def _mode_exchange_testnet(self) -> bool:
        if self._status.mode == Mode.DEMO:
            return True
        return False

    def _mode_client_order_prefix(self) -> str:
        if self._status.mode == Mode.DEMO:
            return "tb-demo"
        if self._status.mode == Mode.LIVE:
            return "tb-live"
        if self._status.mode == Mode.PAPER:
            return "tb-paper"
        return "tb-backtest"

    def _live_submit_env_armed(self) -> bool:
        raw = os.getenv("TRADINGBOT_ALLOW_LIVE_SUBMIT", "").strip().lower()
        return raw in ("1", "true", "yes", "y", "on")

    def _apply_mode_derived_execution_config(self, config_dict: dict) -> dict:
        exchange_name = str(config_dict.get("exchange_name") or self._status.config.exchange_name or "gateio").strip().lower()
        if exchange_name in {"gate", "gateio", "gate.io"} and self._status.mode in (Mode.DEMO, Mode.LIVE):
            profile = "DEMO" if self._status.mode == Mode.DEMO else "LIVE"
            resolved_envs = {
                "api_key_env": f"GATEIO_{profile}_API_KEY",
                "api_secret_env": f"GATEIO_{profile}_API_SECRET",
                "api_passphrase_env": f"GATEIO_{profile}_API_PASSPHRASE",
            }
        else:
            resolved_envs = resolve_exchange_env_names(
                testnet=bool(self._status.mode == Mode.DEMO),
                api_key_env=str(config_dict.get("exchange_api_key_env") or self._status.config.exchange_api_key_env or "GATEIO_API_KEY"),
                api_secret_env=str(config_dict.get("exchange_api_secret_env") or self._status.config.exchange_api_secret_env or "GATEIO_API_SECRET"),
                api_passphrase_env=config_dict.get("exchange_api_passphrase_env") or self._status.config.exchange_api_passphrase_env,
            )

        config_dict["exchange_testnet"] = self._mode_exchange_testnet()
        config_dict["client_order_id_prefix"] = self._mode_client_order_prefix()
        config_dict["exchange_api_key_env"] = resolved_envs["api_key_env"]
        config_dict["exchange_api_secret_env"] = resolved_envs["api_secret_env"]
        config_dict["exchange_api_passphrase_env"] = resolved_envs["api_passphrase_env"]

        if self._status.mode in (Mode.BACKTEST, Mode.PAPER):
            config_dict["enable_live_trading"] = False
            config_dict["live_dry_run"] = True
        elif self._status.mode == Mode.DEMO:
            config_dict["live_dry_run"] = False

        return config_dict

    def _backend_market_type(self, market_type: str | None = None) -> str:
        raw = (market_type or self._status.config.market_type or "spot").strip().lower()
        if raw in ("future", "futures", "perp", "perpetual"):
            return "swap"
        if raw == "swap":
            return "swap"
        return "spot"

    def _config_payload(self) -> dict:
        cfg = asdict(self._status.config)
        backend_market_type = self._backend_market_type(cfg.get("market_type"))
        ui_market_type = self._ui_market_type(backend_market_type)

        cfg["market_type"] = ui_market_type
        cfg["market_type_ui"] = ui_market_type
        cfg["market_type_backend"] = backend_market_type
        cfg["exchange_testnet"] = self._mode_exchange_testnet()
        cfg["client_order_id_prefix"] = self._mode_client_order_prefix()

        return cfg

    def _runtime_light_payload(self) -> dict:
        cfg = self._config_payload()

        return {
            "has_engine": self._engine is not None,
            "has_state": self._state is not None,
            "latest_bar": self._latest_bar,
            "bar_count": len(self._bars),
            "last_processed_bar_ts": self._last_processed_bar_ts,
            "last_signal": self._last_signal,
            "fill_count": len(self._fills),
            "equity_point_count": len(self._equity_points),
            "latest_equity": self._equity_points[-1] if self._equity_points else None,
            "paper_state": self._serialize_state(),
            "chart_symbol": cfg.get("symbol"),
            "chart_interval": cfg.get("interval"),
            "reconnect_attempts": self._reconnect_attempts,
            "last_fetch_error": self._last_fetch_error,
            "risk_halt_reason": self._risk_halt_reason,
            "exchange": {
                "name": cfg.get("exchange_name"),
                "market_type": cfg.get("market_type_ui"),
                "backend_market_type": cfg.get("market_type_backend"),
                "testnet": cfg.get("exchange_testnet"),
                "settle_currency": cfg.get("exchange_settle_currency"),
            },
            "live": {
                "exchange_name": cfg.get("exchange_name"),
                "exchange_testnet": cfg.get("exchange_testnet"),
                "exchange_api_key_env": cfg.get("exchange_api_key_env"),
                "exchange_api_secret_env": cfg.get("exchange_api_secret_env"),
                "exchange_api_passphrase_env": cfg.get("exchange_api_passphrase_env"),
                "enable_live_trading": cfg.get("enable_live_trading"),
                "live_dry_run": cfg.get("live_dry_run"),
                "sync_positions_on_start": cfg.get("sync_positions_on_start"),
                "cancel_open_orders_on_stop": cfg.get("cancel_open_orders_on_stop"),
                "client_order_id_prefix": cfg.get("client_order_id_prefix"),
            },
        }

    def metrics(self) -> dict:
        return {
            "run_id": self._status.run_id,
            "run_label": self._status.run_label,
            "mode": self._status.mode.value,
            "state": self._status.state.value,
            "runtime": {
                "fills": [getattr(f, "__dict__", f) for f in reversed(self._fills[-10:])],
                "paper_metrics": self._get_cached_metrics_payload(),
                "risk": self._build_risk_payload(),
                "latest_equity": self._equity_points[-1] if self._equity_points else None,
                "equity_point_count": len(self._equity_points),
                "fill_count": len(self._fills),
            },
        }

    def status(self) -> dict:
        cfg = self._config_payload()

        return {
            "mode": self._status.mode.value,
            "state": self._status.state.value,
            "run_id": self._status.run_id,
            "run_label": self._status.run_label,
            "started_at": self._status.started_at,
            "stopped_at": self._status.stopped_at,
            "last_error": self._status.last_error,
            "config": cfg,
            "runtime": self._runtime_light_payload(),
        }

    def latest_bar(self, symbol: str | None = None) -> dict | None:
        cfg_symbol = self._status.config.symbol.upper()
        if symbol and symbol.upper() != cfg_symbol:
            return None
        return self._latest_bar

    def _serialize_chart_bar(self, bar: dict | None) -> dict | None:
        if not bar:
            return None

        try:
            return {
                "time": int(bar["timestamp"]),
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": float(bar.get("volume", 0.0)),
            }
        except (KeyError, TypeError, ValueError):
            return None

    def _fill_timestamp_to_unix(self, value: Any) -> int | None:
        if value is None:
            return None

        if isinstance(value, (int, float)):
            if math.isfinite(float(value)):
                return int(float(value))
            return None

        text = str(value).strip()
        if not text:
            return None

        try:
            return int(float(text))
        except (TypeError, ValueError):
            pass

        try:
            return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None

    def _load_chart_marker_rows(self) -> list[Mapping[str, Any]]:
        if self._status.run_id:
            rows = load_runtime_fills(
                run_id=self._status.run_id,
                limit=5000,
                offset=0,
            )
            if rows:
                return cast(list[Mapping[str, Any]], rows)

        return [
            cast(Mapping[str, Any], getattr(fill, "__dict__", fill))
            for fill in self._fills
        ]

    def _marker_from_fill(self, row: Mapping[str, Any]) -> dict | None:
        ts = self._fill_timestamp_to_unix(row.get("timestamp"))
        if ts is None:
            return None

        fill_type = str(row.get("type", "")).strip().upper()
        side = str(row.get("side", "")).strip().lower()
        price = self._safe_float_value(row.get("price"))
        pnl = self._safe_float_value(row.get("pnl"))
        qty = self._safe_float_value(row.get("qty"))
        fee = self._safe_float_value(row.get("fee"))
        trade_id = row.get("trade_id")
        symbol = str(row.get("symbol") or self._status.config.symbol or "").upper()

        if fill_type == "ENTRY" and side == "long":
            position = "belowBar"
            color = "#22c55e"
            shape = "arrowUp"
            label = "LONG"
        elif fill_type == "ENTRY" and side == "short":
            position = "aboveBar"
            color = "#ef4444"
            shape = "arrowDown"
            label = "SHORT"
        elif fill_type == "EXIT" and side == "long":
            position = "aboveBar"
            color = "#f59e0b"
            shape = "arrowDown"
            label = "EXIT"
        elif fill_type == "EXIT" and side == "short":
            position = "belowBar"
            color = "#f59e0b"
            shape = "arrowUp"
            label = "COVER"
        elif fill_type == "LIQUIDATION":
            position = "aboveBar"
            color = "#ff4d6d"
            shape = "circle"
            label = "LIQ"
        else:
            position = "aboveBar"
            color = "#94a3b8"
            shape = "circle"
            label = fill_type or "FILL"

        if price is not None:
            text = f"{label} @ {price:.5f}"
        else:
            text = label

        if pnl is not None and fill_type in {"EXIT", "LIQUIDATION"}:
            text += f" PnL {pnl:+.2f}"

        return {
            "time": ts,
            "position": position,
            "color": color,
            "shape": shape,
            "text": text,
            "fill_type": fill_type,
            "side": side,
            "price": price,
            "qty": qty,
            "fee": fee,
            "pnl": pnl,
            "trade_id": trade_id,
            "symbol": symbol,
            "timestamp": row.get("timestamp"),
        }

    def _chart_marker_time(self, value: Any) -> int | None:
        if value is None:
            return None

        if isinstance(value, (int, float)):
            try:
                numeric = float(value)
                if not math.isfinite(numeric):
                    return None
                return int(numeric / 1000) if numeric > 10_000_000_000 else int(numeric)
            except (TypeError, ValueError):
                return None

        raw = str(value).strip()
        if not raw:
            return None

        try:
            numeric = float(raw)
            if math.isfinite(numeric):
                return int(numeric / 1000) if numeric > 10_000_000_000 else int(numeric)
        except ValueError:
            pass

        try:
            normalized = raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            return int(dt.timestamp())
        except ValueError:
            return None

    def _serialize_chart_marker(self, fill: Fill | dict) -> dict | None:
        data = fill.to_dict() if isinstance(fill, Fill) else dict(fill or {})

        marker_time = self._chart_marker_time(data.get("timestamp"))
        if marker_time is None:
            return None

        fill_type = str(data.get("type") or data.get("fill_type") or "").upper()
        side = str(data.get("side") or "").lower()
        price = data.get("price")
        qty = data.get("qty")
        pnl = data.get("pnl")

        is_entry = fill_type == "ENTRY"
        is_exit = fill_type in {"EXIT", "LIQUIDATION"}
        if not is_entry and not is_exit:
            return None

        if fill_type == "LIQUIDATION":
            color = "#f97316"
            text = "LIQ"
            position = "aboveBar"
            shape = "circle"
        elif is_entry and side == "short":
            color = "#f43f5e"
            text = "SHORT"
            position = "aboveBar"
            shape = "arrowDown"
        elif is_entry:
            color = "#22c55e"
            text = "LONG"
            position = "belowBar"
            shape = "arrowUp"
        elif side == "short":
            color = "#38bdf8"
            text = "COVER"
            position = "belowBar"
            shape = "arrowUp"
        else:
            color = "#f59e0b"
            text = "EXIT"
            position = "aboveBar"
            shape = "arrowDown"

        label_bits = [text]

        try:
            if price is not None:
                label_bits.append(f"@ {float(price):.4g}")
        except (TypeError, ValueError):
            pass

        try:
            if pnl is not None:
                label_bits.append(f"PnL {float(pnl):+.4g}")
        except (TypeError, ValueError):
            pass

        return {
            "time": marker_time,
            "position": position,
            "color": color,
            "shape": shape,
            "text": " ".join(label_bits),
            "fill_type": fill_type,
            "side": side,
            "price": price,
            "qty": qty,
            "pnl": pnl,
        }

    def _chart_markers_from_fills(self, start_time: int | None, end_time: int | None) -> list[dict]:
        if self._status.run_id:
            raw_fills = load_runtime_fills_ascending(
                run_id=self._status.run_id,
                limit=5_000,
                offset=0,
            )
        else:
            raw_fills = [getattr(f, "__dict__", f) for f in self._fills]

        markers: list[dict] = []
        seen: set[tuple[Any, ...]] = set()

        for fill in raw_fills:
            marker = self._serialize_chart_marker(fill)
            if not marker:
                continue

            marker_time = int(marker["time"])
            if start_time is not None and marker_time < start_time:
                continue
            if end_time is not None and marker_time > end_time:
                continue

            dedupe_key = (
                marker.get("time"),
                marker.get("fill_type"),
                marker.get("side"),
                marker.get("price"),
                marker.get("qty"),
            )
            if dedupe_key in seen:
                continue

            seen.add(dedupe_key)
            markers.append(marker)

        markers.sort(key=lambda item: int(item.get("time", 0)))
        return markers

    def get_chart_snapshot(self, limit: int | None = None, interval_override: str | None = None) -> dict:
        cfg = self._status.config
        effective_limit = max(10, int(limit or cfg.candle_limit or 300))

        interval = interval_override or cfg.interval

        bars = self._fetch_recent_bars(
            symbol=cfg.symbol,
            interval=interval,
            limit=effective_limit,
        )

        if not bars:
            bars = self._fetch_recent_bars(
                symbol=cfg.symbol,
                interval=cfg.interval,
                limit=effective_limit,
            )

        candles = []
        for bar in bars[-effective_limit:]:
            normalized = self._serialize_chart_bar(bar)
            if normalized:
                candles.append(normalized)

        markers: list[dict] = []
        symbol_upper = str(cfg.symbol or "").upper()

        for row in self._load_chart_marker_rows():
            row_symbol = str(row.get("symbol") or cfg.symbol or "").upper()
            if row_symbol and symbol_upper and row_symbol != symbol_upper:
                continue

            marker = self._marker_from_fill(row)
            if marker:
                markers.append(marker)

        markers.sort(key=lambda item: int(item["time"]))

        return {
            "symbol": cfg.symbol,
            "interval": interval,
            "strategy_name": self._resolved_strategy_name_from_config(cfg),
            "strategy_params": dict(cfg.strategy_params or {}),
            "candles": candles,
            "markers": markers,
            "marker_count": len(markers),
        }

    def get_paper_fills(self, limit: int = 200, offset: int = 0) -> dict:
        safe_limit = max(1, min(limit, 5000))
        safe_offset = max(0, offset)

        if not self._status.run_id:
            mem_rows = [getattr(f, "__dict__", f) for f in reversed(self._fills)]
            sliced = mem_rows[safe_offset:safe_offset + safe_limit]
            return {
                "run_id": None,
                "total": len(mem_rows),
                "limit": limit,
                "offset": offset,
                "fills": sliced,
            }

        rows = load_runtime_fills(
            run_id=self._status.run_id,
            limit=safe_limit,
            offset=safe_offset,
        )
        total = count_runtime_fills(self._status.run_id)

        if not rows and self._fills:
            mem_rows = [getattr(f, "__dict__", f) for f in reversed(self._fills)]
            rows = mem_rows[safe_offset:safe_offset + safe_limit]
            total = len(mem_rows)

        return {
            "run_id": self._status.run_id,
            "total": total,
            "limit": limit,
            "offset": offset,
            "fills": rows,
        }

    def get_paper_equity(self, limit: int = 500, offset: int = 0) -> dict:
        safe_limit = max(1, min(limit, 10000))
        safe_offset = max(0, offset)

        if not self._status.run_id:
            rows = list(reversed(self._normalize_equity_points(cast(Sequence[Mapping[str, Any]], self._equity_points))))
            sliced = rows[safe_offset:safe_offset + safe_limit]
            return {
                "run_id": None,
                "total": len(rows),
                "limit": limit,
                "offset": offset,
                "points": list(reversed(sliced)),
            }

        rows_desc = load_runtime_equity_snapshots(
            run_id=self._status.run_id,
            limit=safe_limit,
            offset=safe_offset,
            ascending=False,
        )
        rows_desc = self._normalize_equity_points(cast(Sequence[Mapping[str, Any]], rows_desc))
        total = count_runtime_equity_snapshots(self._status.run_id)

        if not rows_desc and self._equity_points:
            mem_rows = list(reversed(self._normalize_equity_points(cast(Sequence[Mapping[str, Any]], self._equity_points))))
            rows_desc = mem_rows[safe_offset:safe_offset + safe_limit]
            total = len(mem_rows)

        return {
            "run_id": self._status.run_id,
            "total": total,
            "limit": limit,
            "offset": offset,
            "points": list(reversed(rows_desc)),
        }

    def get_paper_metrics(self) -> dict:
        return {
            "run_id": self._status.run_id,
            "metrics": self._get_cached_metrics_payload(),
        }

    def export_paper_equity_csv(self) -> str:
        if not self._status.run_id:
            return (
                "id,run_id,ts,balance,equity,market_price,position_qty,side,"
                "unrealized_pnl,realized_pnl,drawdown_pct,created_at\n"
            )
        return export_runtime_equity_csv(self._status.run_id)

    def get_trace(self, limit: int = 200, offset: int = 0) -> dict:
        safe_limit = max(1, min(limit, 1000))
        safe_offset = max(0, offset)

        rows = list(reversed(self._trace))
        sliced = rows[safe_offset:safe_offset + safe_limit]

        return {
            "run_id": self._status.run_id,
            "total": len(rows),
            "limit": limit,
            "offset": offset,
            "events": sliced,
        }

    def _safe_float_value(self, value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _safe_int_value(self, value: Any, default: int = 0) -> int:
        try:
            if value is None:
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    def _fill_to_dict(self, fill: Fill | Mapping[str, Any] | None) -> dict[str, Any]:
        if fill is None:
            return {}
        if isinstance(fill, Fill):
            return cast(dict[str, Any], fill.to_dict())
        return dict(fill)

    def _fill_dedupe_key(self, fill: Fill | Mapping[str, Any]) -> str:
        data = self._fill_to_dict(fill)
        return "|".join(
            [
                str(self._status.run_id or ""),
                str(data.get("timestamp", "")),
                str(data.get("type", "")),
                str(data.get("side", "")),
                str(data.get("trade_id", "")),
                f"{self._safe_float_value(data.get('price', 0.0)):.12f}",
                f"{self._safe_float_value(data.get('qty', 0.0)):.12f}",
            ]
        )

    def _remember_fill_key(self, key: str) -> None:
        if key in self._recent_fill_key_set:
            return

        self._recent_fill_keys.append(key)
        self._recent_fill_key_set.add(key)

        max_keys = max(5, int(self._status.config.dedupe_fill_window or 20))
        while len(self._recent_fill_keys) > max_keys:
            old = self._recent_fill_keys.pop(0)
            self._recent_fill_key_set.discard(old)

    def _is_duplicate_fill(self, fill: Fill | Mapping[str, Any]) -> bool:
        key = self._fill_dedupe_key(fill)
        return key in self._recent_fill_key_set

    async def _sleep_or_stop(self, seconds: float) -> None:
        timeout = max(0.05, float(seconds))
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return

    def _stable_bars_for_signal(
        self,
        bars: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int | None]:
        confirmations = max(1, int(self._status.config.bar_confirmations or 1))
        if len(bars) <= confirmations:
            return [], None

        stable_bars = bars[:-confirmations]
        processed_ts = None
        if stable_bars:
            processed_ts = self._safe_int_value(stable_bars[-1].get("timestamp"), default=0) or None
        return stable_bars, processed_ts

    async def force_buy(self, price: float | None = None, note: str | None = None) -> dict:
        async with self._lock:
            if self._status.mode != Mode.PAPER:
                raise RuntimeError("Force buy is only allowed in paper mode.")

            if self._status.state not in (EngineState.RUNNING, EngineState.IDLE):
                raise RuntimeError("Force buy requires paper mode to be idle or running.")

            if self._engine is None or self._state is None:
                self._build_runtime_objects()

            if self._engine is None or self._state is None:
                raise RuntimeError("Paper runtime is not initialized.")

            if float(self._state.position_qty or 0.0) > 0.0:
                raise RuntimeError("Cannot force buy: a long position is already open.")

            resolved_price: float | None = None

            if price is not None:
                resolved_price = float(price)

            if resolved_price is None and self._latest_bar:
                try:
                    close_value = self._latest_bar.get("close")
                    resolved_price = self._safe_float_value(close_value, 0.0)
                    if resolved_price <= 0.0:
                        resolved_price = None
                except (TypeError, ValueError):
                    resolved_price = None

            if resolved_price is None and self._bars:
                try:
                    close_value = self._bars[-1].get("close")
                    resolved_price = self._safe_float_value(close_value, 0.0)
                    if resolved_price <= 0.0:
                        resolved_price = None
                except (TypeError, ValueError):
                    resolved_price = None

            if resolved_price is None:
                live = self.market_data.get_price(self._status.config.symbol)
                if isinstance(live, dict) and live.get("price_usd") is not None:
                    resolved_price = float(live["price_usd"])

            if resolved_price is None or resolved_price <= 0:
                raise RuntimeError("Force buy could not resolve a valid execution price.")

            prev_cash = float(self._state.cash or 0.0)
            prev_qty = float(self._state.position_qty or 0.0)
            max_qty = float(self._status.config.max_qty or 0.0)
            leverage = float(self._status.config.leverage or 1.0)

            gate = self._check_entry_gate_locked(resolved_price)
            if not gate["allowed"]:
                self._risk_halt_reason = gate["reason"]
                raise RuntimeError(f"Risk gate blocked entry: {gate['reason']}")

            self._trade_id += 1
            self._state, fill = self._engine.enter_long(
                ts_iso=str(int(time.time())),
                state=self._state,
                close=resolved_price,
                market_type=self._status.config.market_type,
                leverage=self._status.config.leverage,
                trade_id=self._trade_id,
                qty_override=self._compute_entry_qty_locked(resolved_price),
            )

            if fill is None:
                raise RuntimeError(
                    "Force buy did not produce a fill. "
                    f"price={resolved_price}, cash={prev_cash}, "
                    f"position_qty={prev_qty}, max_qty={max_qty}, leverage={leverage}"
                )

            await self._append_fill(fill)
            await self._mark_to_market(resolved_price)
            self._persist_status()
            self._persist_snapshot()

            await self._broadcast_trace_event(
                "force_buy",
                note=note or "Manual force buy executed.",
                data={
                    "price": resolved_price,
                    "trade_id": getattr(fill, "trade_id", None),
                    "run_id": self._status.run_id,
                },
            )

            return self.status()

    async def force_sell(self, price: float | None = None, note: str | None = None) -> dict:
        async with self._lock:
            self._ensure_manual_paper_runtime_locked()

            if self._state is None or self._engine is None:
                raise RuntimeError("Paper runtime is not initialized.")

            if self._state.position_qty <= 0:
                raise RuntimeError("No long position to sell.")

            resolved_price = await self._resolve_reference_price_locked(price)

            self._state, fill = self._engine.exit_long(
                ts_iso=str(int(time.time())),
                state=self._state,
                close=resolved_price,
                market_type=self._status.config.market_type,
                trade_id=self._trade_id,
            )

            if not fill:
                raise RuntimeError("Force sell did not produce a fill.")

            await self._append_fill(fill)
            await self._mark_to_market(resolved_price)
            self._persist_status()
            self._persist_snapshot()

            await self._broadcast_trace_event(
                "force_sell",
                note=note or "Manual paper sell executed.",
                data={
                    "price": resolved_price,
                    "trade_id": self._trade_id,
                    "position_qty": self._state.position_qty,
                },
            )
            return self.status()

    async def flatten_position(self, price: float | None = None, note: str | None = None) -> dict:
        async with self._lock:
            self._ensure_manual_paper_runtime_locked()

            if self._state is None or self._engine is None:
                raise RuntimeError("Paper runtime is not initialized.")

            resolved_price = await self._resolve_reference_price_locked(price)

            if self._state.position_qty > 0:
                self._state, fill = self._engine.exit_long(
                    ts_iso=str(int(time.time())),
                    state=self._state,
                    close=resolved_price,
                    market_type=self._status.config.market_type,
                    trade_id=self._trade_id,
                )
                if fill:
                    await self._append_fill(fill)

            elif self._state.position_qty < 0:
                self._state, fill = self._engine.liquidate(
                    ts_iso=str(int(time.time())),
                    state=self._state,
                    close=resolved_price,
                    market_type=self._status.config.market_type,
                    trade_id=self._trade_id,
                )
                if fill:
                    await self._append_fill(fill)
            else:
                await self._broadcast_trace_event(
                    "flatten_noop",
                    note=note or "Flatten requested with no open position.",
                    data={"price": resolved_price},
                )
                return self.status()

            await self._mark_to_market(resolved_price)
            self._persist_status()
            self._persist_snapshot()

            await self._broadcast_trace_event(
                "flatten",
                note=note or "Manual flatten executed.",
                data={
                    "price": resolved_price,
                    "trade_id": self._trade_id,
                    "position_qty": self._state.position_qty,
                },
            )
            return self.status()

    def _append_trace_event(
        self,
        event: str,
        note: str | None = None,
        data: dict | None = None,
    ) -> dict:
        entry = {
            "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "event": event,
            "note": note,
            "run_id": self._status.run_id,
            "data": data or {},
        }
        self._trace.append(entry)

        if len(self._trace) > 2000:
            self._trace = self._trace[-2000:]

        return entry

    async def _broadcast_trace_event(
        self,
        event: str,
        note: str | None = None,
        data: dict | None = None,
    ) -> dict:
        entry = self._append_trace_event(event=event, note=note, data=data)
        await ws_manager.broadcast({"type": "trace", "data": entry})
        return entry

    def _ensure_manual_paper_runtime_locked(self) -> None:
        if self._status.mode != Mode.PAPER:
            raise RuntimeError("Paper dev controls only work in paper mode.")

        if self._status.state in (EngineState.STARTING, EngineState.STOPPING):
            raise RuntimeError("Wait for the engine transition to finish first.")

        if not self._status.run_id:
            self._status.started_at = time.time()
            self._status.stopped_at = None
            self._status.last_error = None
            self._refresh_run_identity(
                self._status.config,
                started_at=self._status.started_at,
                mode=self._status.mode,
            )
            self._reset_runtime_memory()
            self._build_runtime_objects()

        elif self._engine is None or self._state is None:
            self._build_runtime_objects_from_existing_or_new()

        self._persist_status()
        self._persist_snapshot()

    async def _resolve_reference_price_locked(self, override_price: float | None = None) -> float:
        if override_price is not None:
            return float(override_price)

        if self._latest_bar and self._latest_bar.get("close") is not None:
            return float(self._latest_bar["close"])

        bars = await asyncio.to_thread(
            self._fetch_recent_bars,
            self._status.config.symbol,
            self._status.config.interval,
            2,
        )

        if not bars:
            raise RuntimeError("No market price available for manual paper action.")

        self._latest_bar = bars[-1]
        return float(self._latest_bar["close"])

    async def reset_paper(self) -> dict:
        async with self._lock:
            if self._status.state in (
                EngineState.STARTING,
                EngineState.RUNNING,
                EngineState.STOPPING,
            ):
                raise RuntimeError("Stop paper trading before resetting it.")

            previous_run_id = self._status.run_id

            self._status = RunStatus(mode=Mode.PAPER)
            self._stop_event = asyncio.Event()
            self._task = None
            self._reset_runtime_memory()

            self._status.last_error = None
            self._status.started_at = None
            self._status.stopped_at = None
            self._status.run_id = None
            self._status.run_label = None

            await ws_manager.broadcast(
                {
                    "type": "trace",
                    "data": {
                        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                        "event": "trace_cleared",
                        "note": "Paper reset cleared active runtime state. Persisted run history was preserved.",
                        "run_id": None,
                        "data": {
                            "previous_run_id": previous_run_id,
                        },
                    },
                }
            )

            return self.status()

    def export_paper_fills_csv(self) -> str:
        if not self._status.run_id:
            return (
                "id,timestamp,type,side,price,qty,fee,equity_after,"
                "entry_price,exit_price,trade_id,pnl,created_at\n"
            )
        return export_runtime_fills_csv(self._status.run_id)

    def _serialize_state(self) -> dict | None:
        if self._state is None:
            return None

        market_price = None
        if self._latest_bar and self._latest_bar.get("close") is not None:
            try:
                market_price = float(self._latest_bar["close"])
            except Exception:
                market_price = None

        equity = float(self._state.equity or 0.0)
        unrealized = 0.0
        if (
            market_price is not None
            and self._state.entry_price is not None
            and self._state.position_qty != 0
        ):
            unrealized = float(self._state.position_qty) * (
                float(market_price) - float(self._state.entry_price)
            )

        return {
            "cash": float(self._state.cash),
            "position_qty": float(self._state.position_qty),
            "entry_price": self._state.entry_price,
            "side": self._state.side,
            "equity": equity,
            "liquidation_price": self._state.liquidation_price,
            "realized_pnl": float(self._state.realized_pnl),
            "active_trade_id": self._state.active_trade_id,
            "margin": float(self._state.margin),
            "borrowed": float(self._state.borrowed),
            "unrealized_pnl": float(unrealized),
            "market_price": market_price,
        }

    def _build_equity_point(self) -> dict | None:
        if self._state is None:
            return None

        market_price = None
        if self._latest_bar and self._latest_bar.get("close") is not None:
            try:
                market_price = float(self._latest_bar["close"])
            except Exception:
                market_price = None

        if market_price is None and self._state.entry_price is not None:
            market_price = float(self._state.entry_price)

        balance = float(self._state.cash)
        if self._engine is not None and market_price is not None:
            equity = float(self._engine.mark_equity(
                state=self._state,
                market_type=self._status.config.market_type,
                market_price=float(market_price),
            ))
        else:
            equity = float(self._state.equity or balance)
        realized_pnl = float(self._state.realized_pnl or 0.0)

        unrealized_pnl = 0.0
        if (
            market_price is not None
            and self._state.entry_price is not None
            and self._state.position_qty != 0
        ):
            unrealized_pnl = float(self._state.position_qty) * (
                float(market_price) - float(self._state.entry_price)
            )

        peak = max(float(self._peak_equity or 0.0), float(equity))
        drawdown_pct = 0.0 if peak <= 0 else ((peak - equity) / peak) * 100.0

        ts = None
        if self._latest_bar and self._latest_bar.get("timestamp") is not None:
            try:
                ts = int(self._latest_bar["timestamp"])
            except Exception:
                ts = None
        if ts is None:
            ts = int(time.time())

        return {
            "run_id": self._status.run_id,
            "ts": ts,
            "balance": balance,
            "equity": equity,
            "market_price": market_price,
            "position_qty": float(self._state.position_qty),
            "side": self._state.side,
            "unrealized_pnl": float(unrealized_pnl),
            "realized_pnl": realized_pnl,
            "drawdown_pct": float(max(drawdown_pct, 0.0)),
        }

    def _normalize_equity_point(self, point: Mapping[str, Any] | None) -> dict[str, Any]:
        if not point:
            return {
                "ts": int(time.time()),
                "balance": 0.0,
                "equity": 0.0,
                "market_price": None,
                "position_qty": 0.0,
                "side": None,
                "unrealized_pnl": 0.0,
                "realized_pnl": 0.0,
                "drawdown_pct": 0.0,
            }

        ts_raw = point.get("ts", point.get("timestamp"))
        ts_value = self._safe_int_value(ts_raw, int(time.time()))

        balance_raw = point.get("balance", point.get("cash"))
        balance_value = self._safe_float_value(balance_raw, 0.0)

        equity_raw = point.get("equity")
        equity_value = self._safe_float_value(equity_raw, balance_value)

        market_price_raw = point.get("market_price", point.get("price"))
        market_price_value = None
        if market_price_raw is not None:
            try:
                market_price_value = float(market_price_raw)
            except Exception:
                market_price_value = None

        position_qty_value = self._safe_float_value(point.get("position_qty", 0.0), 0.0)
        unrealized_pnl_value = self._safe_float_value(point.get("unrealized_pnl", 0.0), 0.0)
        realized_pnl_value = self._safe_float_value(point.get("realized_pnl", 0.0), 0.0)
        drawdown_raw = point.get("drawdown_pct", point.get("drawdown", 0.0))
        drawdown_value = self._safe_float_value(drawdown_raw, 0.0)

        return {
            "ts": ts_value,
            "balance": balance_value,
            "equity": equity_value,
            "market_price": market_price_value,
            "position_qty": position_qty_value,
            "side": point.get("side"),
            "unrealized_pnl": unrealized_pnl_value,
            "realized_pnl": realized_pnl_value,
            "drawdown_pct": drawdown_value,
        }

    def _normalize_equity_points(self, points: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [self._normalize_equity_point(p) for p in points]

    def _record_equity_point(self) -> None:
        point = self._build_equity_point()
        if not point:
            return

        last = self._equity_points[-1] if self._equity_points else None
        if last:
            same_ts = self._safe_int_value(last.get("ts"), 0) == self._safe_int_value(point.get("ts"), 0)
            same_equity = self._safe_float_value(last.get("equity"), 0.0) == self._safe_float_value(point.get("equity"), 0.0)
            same_balance = self._safe_float_value(last.get("balance"), 0.0) == self._safe_float_value(point.get("balance"), 0.0)
            same_qty = self._safe_float_value(last.get("position_qty"), 0.0) == self._safe_float_value(point.get("position_qty"), 0.0)
            if same_ts and same_equity and same_balance and same_qty:
                return

        self._equity_points.append(point)
        self._paper_metrics_dirty = True
        if len(self._equity_points) > 10000:
            self._equity_points = self._equity_points[-10000:]

        if self._status.run_id:
            insert_runtime_equity_snapshot(
                self._status.run_id,
                ts=self._safe_int_value(point.get("ts"), int(time.time())),
                balance=self._safe_float_value(point.get("balance"), 0.0),
                equity=self._safe_float_value(point.get("equity"), 0.0),
                market_price=point.get("market_price"),
                position_qty=self._safe_float_value(point.get("position_qty"), 0.0),
                side=point.get("side"),
                unrealized_pnl=self._safe_float_value(point.get("unrealized_pnl"), 0.0),
                realized_pnl=self._safe_float_value(point.get("realized_pnl"), 0.0),
                drawdown_pct=self._safe_float_value(point.get("drawdown_pct"), 0.0),
            )

    def _get_cached_metrics_payload(self) -> dict:
        # Keep /status lightweight for the v0.8 UI. Full metrics are rebuilt only
        # after a fill or equity append marks the cache dirty.
        if self._paper_metrics_cache is None or self._paper_metrics_dirty:
            self._paper_metrics_cache = self._build_metrics_payload()
            self._paper_metrics_dirty = False
        return dict(self._paper_metrics_cache)

    def _build_metrics_payload(self) -> dict:
        raw_fills = []
        if self._status.run_id:
            raw_fills = load_runtime_fills_ascending(self._status.run_id, limit=1_000_000, offset=0)
        elif self._fills:
            raw_fills = list(self._fills)

        raw_points: Sequence[Mapping[str, Any]] = []
        if self._status.run_id:
            raw_points = load_runtime_equity_snapshots(
                self._status.run_id,
                limit=1_000_000,
                offset=0,
                ascending=True,
            )
        elif self._equity_points:
            raw_points = list(self._equity_points)

        points = self._normalize_equity_points(raw_points)

        fills: list[dict] = []
        for item in raw_fills:
            if isinstance(item, dict):
                fills.append(item)
            else:
                fills.append(getattr(item, "__dict__", {}))

        initial_balance = float(self._status.config.initial_balance)

        if points:
            start_equity = float(points[0].get("equity", initial_balance))
            latest_equity = float(points[-1].get("equity", initial_balance))
            latest_balance = float(points[-1].get("balance", initial_balance))
            max_drawdown_pct = max(float(p.get("drawdown_pct", 0.0)) for p in points)
        else:
            start_equity = initial_balance
            latest_equity = float(self._state.equity) if self._state else initial_balance
            latest_balance = float(self._state.cash) if self._state else initial_balance
            max_drawdown_pct = 0.0

        exits = [
            f for f in fills
            if str(f.get("type", "")).upper() in {"EXIT", "LIQUIDATION"}
        ]

        closed_trades = len(exits)
        wins = sum(1 for f in exits if float(f.get("pnl") or 0.0) > 0.0)
        losses = sum(1 for f in exits if float(f.get("pnl") or 0.0) < 0.0)
        breakeven = closed_trades - wins - losses

        gross_profit = sum(
            float(f.get("pnl") or 0.0) for f in exits if float(f.get("pnl") or 0.0) > 0.0
        )
        gross_loss = abs(
            sum(float(f.get("pnl") or 0.0) for f in exits if float(f.get("pnl") or 0.0) < 0.0)
        )
        net_pnl = gross_profit - gross_loss

        win_rate = (wins / closed_trades * 100.0) if closed_trades else 0.0
        avg_trade_pnl = (net_pnl / closed_trades) if closed_trades else 0.0
        profit_factor = (
            gross_profit / gross_loss
            if gross_loss > 0
            else (gross_profit if gross_profit > 0 else 0.0)
        )

        return_pct = (
            ((latest_equity - initial_balance) / initial_balance) * 100.0
            if initial_balance > 0
            else 0.0
        )

        return {
            "initial_balance": initial_balance,
            "start_equity": start_equity,
            "latest_balance": latest_balance,
            "latest_equity": latest_equity,
            "net_pnl": net_pnl,
            "return_pct": return_pct,
            "max_drawdown_pct": max_drawdown_pct,
            "closed_trades": closed_trades,
            "wins": wins,
            "losses": losses,
            "breakeven": breakeven,
            "win_rate_pct": win_rate,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "profit_factor": profit_factor,
            "avg_trade_pnl": avg_trade_pnl,
            "equity_points": len(points),
        }

    def _persist_status(self) -> None:
        if not self._status.run_id:
            return

        upsert_runtime_run(
            run_id=self._status.run_id,
            mode=self._status.mode.value,
            state=self._status.state.value,
            started_at=self._status.started_at,
            stopped_at=self._status.stopped_at,
            last_error=self._status.last_error,
            config=asdict(self._status.config),
        )

    def _persist_snapshot(self) -> None:
        if not self._status.run_id:
            return

        save_runtime_snapshot(
            run_id=self._status.run_id,
            state_dict=self._state.to_dict() if self._state else None,
            latest_bar=self._latest_bar,
            bars=self._bars[-self._status.config.candle_limit:] if self._bars else [],
            last_processed_bar_ts=self._last_processed_bar_ts,
            last_signal=self._last_signal,
            trade_id=self._trade_id,
        )

        self._record_equity_point()

    def _persist_fill(self, fill: Fill) -> None:
        if not self._status.run_id:
            return
        insert_runtime_fill(self._status.run_id, fill.to_dict())

    def _restore_paper_session(self) -> None:
        restored = get_latest_paper_run()
        if not restored:
            return

        self._status.mode = Mode(restored["mode"])
        self._status.run_id = restored["run_id"]
        self._status.run_label = restored["run_id"]
        self._status.started_at = restored.get("started_at")
        self._status.stopped_at = restored.get("stopped_at")
        self._status.last_error = restored.get("last_error")

        cfg_data = restored.get("config") or {}
        for key, value in cfg_data.items():
            if hasattr(self._status.config, key) and value is not None:
                setattr(self._status.config, key, value)

        snapshot = restored.get("snapshot") or {}
        self._latest_bar = snapshot.get("latest_bar")
        self._bars = snapshot.get("bars") or []
        self._last_processed_bar_ts = snapshot.get("last_processed_bar_ts")
        self._last_signal = int(snapshot.get("last_signal") or 0)
        self._trade_id = int(snapshot.get("trade_id") or 0)

        restored_state = PortfolioState.from_dict(snapshot.get("state"))
        self._state = restored_state

        self._engine = (
            ExecutionEngine(
                fee_rate=self._status.config.fee_rate,
                slippage_bps=self._status.config.slippage_bps,
                liquidation_fee_rate=self._status.config.liquidation_fee_rate,
                maintenance_margin=self._status.config.maintenance_margin,
                max_leverage=self._status.config.max_leverage,
                max_qty=self._status.config.max_qty,
            )
            if restored_state
            else None
        )

        self._fills = []
        self._recent_fill_keys = []
        self._recent_fill_key_set = set()
        for item in restored.get("fills", []):
            fill = Fill.from_dict(item)
            if fill:
                self._fills.append(fill)
                self._remember_fill_key(self._fill_dedupe_key(fill))

        restored_points_raw = restored.get("equity_points") or restored.get("equity_snapshots") or []
        restored_points: list[Mapping[str, Any]] = [
            item for item in restored_points_raw if isinstance(item, Mapping)
        ]
        self._equity_points = self._normalize_equity_points(restored_points)
        self._paper_metrics_cache = None
        self._paper_metrics_dirty = True
        self._reset_risk_runtime_locked()
        for fill in self._fills:
            self._register_fill_for_risk_locked(fill)
        self._update_peak_equity_locked()

        restored_state_value = restored.get("state", EngineState.IDLE.value)
        if restored_state_value in {
            EngineState.RUNNING.value,
            EngineState.STARTING.value,
            EngineState.STOPPING.value,
        }:
            self._status.state = EngineState.IDLE
            self._status.last_error = (
                self._status.last_error or "Recovered paper session after restart."
            )
            if self._status.run_id:
                update_run_state(
                    run_id=self._status.run_id,
                    state=EngineState.IDLE.value,
                    stopped_at=time.time(),
                    last_error=self._status.last_error,
                )
        else:
            self._status.state = EngineState(restored_state_value)

    def _reset_runtime_memory(self) -> None:
        self._engine = None
        self._live_service = None
        self._state = None
        self._trade_id = 0
        self._latest_bar = None
        self._bars = []
        self._last_processed_bar_ts = None
        self._last_signal = 0
        self._fills = []
        self._equity_points = []
        self._trace = []
        self._paper_metrics_cache = None
        self._paper_metrics_dirty = True
        self._recent_fill_keys = []
        self._recent_fill_key_set = set()
        self._reconnect_attempts = 0
        self._last_fetch_error = None
        self._reset_risk_runtime_locked()

    def _build_runtime_objects(self) -> None:
        cfg = self._status.config

        self._engine = ExecutionEngine(
            fee_rate=cfg.fee_rate,
            liquidation_fee_rate=cfg.liquidation_fee_rate,
            slippage_bps=cfg.slippage_bps,
            maintenance_margin=cfg.maintenance_margin,
            max_leverage=cfg.max_leverage,
            max_qty=cfg.max_qty,
        )

        self._state = PortfolioState(
            cash=cfg.initial_balance,
            position_qty=0.0,
            entry_price=None,
            side=None,
            equity=cfg.initial_balance,
            liquidation_price=None,
            realized_pnl=0.0,
            active_trade_id=None,
            margin=0.0,
            borrowed=0.0,
            margin_mode=cfg.margin_mode,
        )

        self._build_live_service_locked()

    def _build_runtime_objects_from_existing_or_new(self) -> None:
        cfg = self._status.config
        
        if self._engine is None:
            self._engine = ExecutionEngine(
                fee_rate=cfg.fee_rate,
                liquidation_fee_rate=cfg.liquidation_fee_rate,
                slippage_bps=cfg.slippage_bps,
                maintenance_margin=cfg.maintenance_margin,
                max_leverage=cfg.max_leverage,
                max_qty=cfg.max_qty,
            )

        if self._state is None:
            self._state = PortfolioState(
                cash=self._status.config.initial_balance,
                position_qty=0.0,
                entry_price=None,
                side=None,
                equity=cfg.initial_balance,
                liquidation_price=None,
                realized_pnl=0.0,
                active_trade_id=None,
                margin=0.0,
                borrowed=0.0,
                margin_mode=cfg.margin_mode,
            )

        self._build_live_service_locked()

        if self._status.started_at is None:
            self._status.started_at = time.time()
        self._status.stopped_at = None
        self._status.last_error = None

    def _uses_exchange_execution_locked(self) -> bool:
        return self._status.mode in (Mode.DEMO, Mode.LIVE)

    def _build_live_service_locked(self) -> None:
        if not self._uses_exchange_execution_locked():
            self._live_service = None
            return

        cfg = self._status.config
        dry_run = bool(cfg.live_dry_run) if self._status.mode == Mode.LIVE else False
        self._live_service = LiveExecutionService(
            exchange_name=cfg.exchange_name,
            market_type=cfg.market_type,
            testnet=bool(self._status.mode == Mode.DEMO or cfg.exchange_testnet),
            settle_currency=cfg.exchange_settle_currency,
            api_key_env=cfg.exchange_api_key_env,
            api_secret_env=cfg.exchange_api_secret_env,
            api_passphrase_env=cfg.exchange_api_passphrase_env,
            client_order_id_prefix=cfg.client_order_id_prefix,
            armed=bool(cfg.enable_live_trading),
            dry_run=dry_run,
        )

    def _symbol_assets_locked(self) -> tuple[str | None, str | None]:
        raw_symbol = str(self._status.config.symbol or "").strip().upper()
        if "/" in raw_symbol:
            parts = raw_symbol.split("/", 1)
            return parts[0] or None, parts[1] or None
        if raw_symbol.endswith("USDT"):
            return raw_symbol[:-4] or None, "USDT"
        if raw_symbol.endswith("USD"):
            return raw_symbol[:-3] or None, "USD"
        return None, str(self._status.config.exchange_settle_currency or "").strip().upper() or None

    async def _sync_exchange_account_locked(self, market_price: float | None = None) -> dict[str, Any] | None:
        if self._live_service is None:
            return None

        snapshot = await asyncio.to_thread(
            self._live_service.sync_account,
            symbol=self._status.config.symbol,
            include_recent_trades=False,
        )

        if self._state is None:
            self._state = PortfolioState(cash=0.0, equity=0.0)

        balance = dict(snapshot.get("balance") or {})
        free_bal = dict(balance.get("free") or {})
        total_bal = dict(balance.get("total") or {})
        settle = str(self._status.config.exchange_settle_currency or "USDT").strip().upper() or "USDT"
        free_settle = self._safe_float_value(free_bal.get(settle), 0.0)
        total_settle = self._safe_float_value(total_bal.get(settle), free_settle)

        prev_qty = float(getattr(self._state, "position_qty", 0.0) or 0.0)
        prev_entry = getattr(self._state, "entry_price", None)
        prev_side = getattr(self._state, "side", None)
        prev_trade_id = getattr(self._state, "active_trade_id", None)
        entry_price = prev_entry
        liquidation_price = None
        position_qty = 0.0
        side = None
        margin_mode = self._status.config.margin_mode

        if is_derivatives_market(self._status.config.market_type):
            normalized_symbol = self._live_service.adapter.normalize_symbol(self._status.config.symbol)
            for row in list(snapshot.get("positions") or []):
                if str(row.get("symbol") or "") != normalized_symbol:
                    continue
                base_qty = self._safe_float_value(row.get("base_qty"), 0.0)
                row_side = str(row.get("side") or "").strip().lower()
                if base_qty <= 0.0 or row_side not in {"long", "short"}:
                    continue
                position_qty = -base_qty if row_side == "short" else base_qty
                side = row_side
                entry_price = row.get("entry_price") if row.get("entry_price") is not None else prev_entry
                liquidation_price = row.get("liquidation_price")
                margin_mode = str(row.get("margin_mode") or margin_mode or "cross")
                break
        else:
            base_asset, quote_asset = self._symbol_assets_locked()
            if quote_asset:
                free_settle = self._safe_float_value(free_bal.get(quote_asset), free_settle)
                total_settle = self._safe_float_value(total_bal.get(quote_asset), total_settle)
            if base_asset:
                base_total = self._safe_float_value(total_bal.get(base_asset), self._safe_float_value(free_bal.get(base_asset), 0.0))
                if base_total > 1e-12:
                    position_qty = base_total
                    side = "long"
                    if entry_price is None:
                        entry_price = market_price

        if abs(position_qty) <= 1e-12:
            entry_price = None
            side = None
            liquidation_price = None
            active_trade_id = None
        else:
            active_trade_id = prev_trade_id
            if active_trade_id is None:
                active_trade_id = self._trade_id or None
            if side == prev_side and prev_entry is not None and entry_price is None:
                entry_price = prev_entry

        if market_price is None:
            ticker = snapshot.get("ticker")
            ticker_last = ticker.get("last") if isinstance(ticker, Mapping) else None
            market_price = self._safe_float_value(ticker_last, 0.0) or None

        if is_derivatives_market(self._status.config.market_type):
            equity = total_settle if total_settle > 0.0 else free_settle
        else:
            base_asset, quote_asset = self._symbol_assets_locked()
            base_total = self._safe_float_value(total_bal.get(base_asset or ""), max(0.0, position_qty))
            quote_total = self._safe_float_value(total_bal.get(quote_asset or ""), total_settle)
            equity = quote_total
            if market_price is not None and base_total > 0.0:
                equity += base_total * float(market_price)

        self._state.cash = float(free_settle)
        self._state.position_qty = float(position_qty)
        self._state.entry_price = entry_price
        self._state.side = side
        self._state.equity = float(equity)
        self._state.liquidation_price = liquidation_price
        self._state.active_trade_id = active_trade_id
        self._state.margin_mode = str(margin_mode or "cross")
        if market_price is not None:
            self._update_peak_equity_locked(float(market_price))
        return snapshot

    def _next_client_order_id_locked(self, trade_id: int, action: str) -> str:
        prefix = str(self._status.config.client_order_id_prefix or "tb").strip() or "tb"
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-.")

        def clean_token(value: object, fallback: str, max_len: int) -> str:
            token = "".join(
                ch if ch in allowed else "-"
                for ch in str(value or "").strip()
            ).strip("-_.")
            return (token or fallback)[:max_len].strip("-_.") or fallback

        prefix_part = clean_token(prefix, "tb", 12)
        if prefix_part.lower().startswith("t-"):
            prefix_part = prefix_part[2:] or "tb"
        action_part = clean_token(str(action or "order").lower(), "o", 1)
        run_part = clean_token(str(self._status.run_id or "run").split("-")[-1], "run", 8)
        trade_part = clean_token(int(trade_id or 0), "0", 6)

        # Gate.io/CCXT rejects text/clientOrderId values over 28 chars.
        # It also requires user-supplied IDs to start with the literal "t-".
        # Keep all exchange client IDs compact so demo/live submissions do
        # not fail before reaching the exchange.
        client_order_id = f"t-{prefix_part}-{action_part}{trade_part}-{run_part}"
        return client_order_id[:28].strip("-_.") or "t-tb-o0"

    async def _submit_exchange_order_locked(
        self,
        *,
        side: str,
        qty: float,
        fill_type: str,
        market_price: float,
        trade_id: int,
        reduce_only: bool = False,
    ) -> Fill | None:
        if self._live_service is None:
            raise RuntimeError("Exchange execution service is not initialized.")
        if not math.isfinite(float(qty)) or float(qty) <= 0.0:
            return None

        order = await asyncio.to_thread(
            self._live_service.submit_order,
            symbol=self._status.config.symbol,
            side=side,
            qty=float(abs(qty)),
            order_type="market",
            price=None,
            reduce_only=reduce_only,
            client_order_id=self._next_client_order_id_locked(trade_id, fill_type.lower()),
            leverage=self._status.config.leverage,
            margin_mode=self._status.config.margin_mode,
        )
        fill = self._live_service.fill_from_order(
            order=order.order,
            fill_type=fill_type,
            market_price=float(market_price),
            trade_id=trade_id,
            expected_price=float(market_price),
            expected_qty=float(abs(qty)),
        )
        await self._append_fill(fill)
        await self._sync_exchange_account_locked(float(market_price))
        self._persist_status()
        self._persist_snapshot()
        return fill

    async def _broadcast_runtime_update(self) -> None:
        latest_price = None
        latest_candle = None

        if self._latest_bar:
            latest_price = self._latest_bar.get("close")
            latest_candle = self._serialize_chart_bar(self._latest_bar)

        position_qty = None
        entry_price = None
        equity = None

        if self._state is not None:
            position_qty = self._state.position_qty
            entry_price = self._state.entry_price
            equity = self._state.equity

        await ws_manager.broadcast({"type": "status", "data": self.status()})
        await ws_manager.broadcast({"type": "tick", "price": latest_price})
        await ws_manager.broadcast({"type": "position", "qty": position_qty, "entry_price": entry_price})
        await ws_manager.broadcast({"type": "equity", "equity": equity})

        if latest_candle:
            await ws_manager.broadcast(
                {
                    "type": "candle",
                    "symbol": self._status.config.symbol,
                    "interval": self._status.config.interval,
                    "candle": latest_candle,
                }
            )

    def _fetch_recent_bars(self, symbol: str, interval: str, limit: int) -> list[dict]:
        try:
            bars = self.market_data.get_candles(symbol=symbol, interval=interval, limit=limit)
            return bars or []
        except Exception:
            return []

    async def _append_fill(self, fill: Fill | dict) -> None:
        normalized = fill if isinstance(fill, Fill) else Fill.from_dict(getattr(fill, "__dict__", fill))
        if normalized is None:
            return

        if self._is_duplicate_fill(normalized):
            await self._broadcast_trace_event(
                "duplicate_fill_ignored",
                note="Skipped duplicate paper fill.",
                data={
                    "fill": normalized.to_dict(),
                    "run_id": self._status.run_id,
                },
            )
            return

        self._fills.append(normalized)
        self._paper_metrics_dirty = True
        if len(self._fills) > 5000:
            self._fills = self._fills[-5000:]

        self._remember_fill_key(self._fill_dedupe_key(normalized))
        self._register_fill_for_risk_locked(normalized)

        if not self._status.run_id:
            print("[paper][warn] fill created but run_id is missing; fill will not persist")

        self._persist_fill(normalized)
        self._persist_snapshot()

        await self._broadcast_trace_event(
            "fill_recorded",
            data={
                "type": normalized.type,
                "side": normalized.side,
                "price": normalized.price,
                "qty": normalized.qty,
                "fee": normalized.fee,
                "trade_id": normalized.trade_id,
                "pnl": normalized.pnl,
            },
        )

        await ws_manager.broadcast({"type": "fills"})
        await self._broadcast_runtime_update()

    def _calculate_order_qty(self, price: float) -> float:
        if self._state is None or price <= 0:
            return 0.0

        notional = self._state.cash * 0.95
        if is_derivatives_market(self._status.config.market_type):
            notional *= self._status.config.leverage

        qty = min(notional / price, self._status.config.max_qty)

        if not math.isfinite(qty) or qty <= 0:
            return 0.0

        return float(qty)

    def _resolve_entry_qty_locked(self, price: float) -> float:
        sized_qty = self._compute_entry_qty_locked(price)
        sizing_mode = self._risk_engine._normalize_mode(
            self._status.config.position_sizing_mode
        )
        if sized_qty is None and sizing_mode == "all_in":
            sized_qty = self._calculate_order_qty(price)
        elif sized_qty is None:
            return 0.0
        sized_qty = float(sized_qty or 0.0)
        if not math.isfinite(sized_qty) or sized_qty <= 0.0:
            return 0.0
        return sized_qty

    async def _mark_to_market(self, price: float) -> None:
        if self._uses_exchange_execution_locked() and self._live_service is not None:
            await self._sync_exchange_account_locked(price)
            return

        if self._engine is None or self._state is None:
            return

        self._state.equity = self._engine.mark_equity(
            state=self._state,
            market_price=price,
            market_type=self._status.config.market_type,
        )

        self._update_peak_equity_locked(price)

        if not is_derivatives_market(self._status.config.market_type):
            return

        if float(self._state.position_qty or 0.0) == 0.0:
            self._state.mark_price = float(price)
            self._state.liquidation_price = None
            self._state.maintenance_margin = 0.0
            self._state.maintenance_margin_rate = 0.0
            self._state.maintenance_amount = 0.0
            self._state.margin_balance = 0.0
            self._state.margin_ratio = None
            self._state.bankruptcy_price = None
            return

        snapshot = evaluate_position_margin(
            state=self._state,
            market_type=self._status.config.market_type,
            mark_price=float(price),
            leverage=self._status.config.leverage,
            margin_mode=self._status.config.margin_mode,
            maintenance_margin_override=self._status.config.maintenance_margin_override,
        )

        self._engine.apply_margin_snapshot(self._state, snapshot)

        if not self._status.config.enable_liquidation:
            return

        if snapshot is None or not snapshot.should_liquidate:
            return

        self._state, fill = self._engine.liquidate(
            ts_iso=str(int(time.time())),
            state=self._state,
            close=float(price),
            market_type=self._status.config.market_type,
            trade_id=self._trade_id,
            exec_price=snapshot.liquidation_price,
            mark_price=snapshot.mark_price,
            margin_snapshot=snapshot,
        )
        if fill:
            await self._append_fill(fill)

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                while not self._stop_event.is_set():
                    cfg = self._status.config

                    bars = await asyncio.to_thread(
                        self._fetch_recent_bars,
                        cfg.symbol,
                        cfg.interval,
                        max(50, int(cfg.candle_limit or 300)),
                    )

                    if not bars:
                        raise RuntimeError("No bars returned from market data service.")

                    self._reconnect_attempts = 0
                    self._last_fetch_error = None

                    latest_bar = bars[-1]
                    self._latest_bar = latest_bar
                    self._bars = bars[-max(1000, int(cfg.candle_limit or 300)) :]

                    latest_price = float(latest_bar["close"])
                    await self._mark_to_market(latest_price)

                    static_exit_done = await self._maybe_execute_static_exit_locked(
                        market_price=latest_price,
                        now_ts=latest_bar.get("timestamp"),
                    )
                    if static_exit_done:
                        self._persist_status()
                        self._persist_snapshot()
                        await self._sleep_or_stop(cfg.poll_seconds)
                        continue

                    if bool(getattr(cfg, "debug_stream", False)):
                        print(
                            "[paper-loop]",
                            f"run_id={self._status.run_id}",
                            f"symbol={cfg.symbol}",
                            f"interval={cfg.interval}",
                            f"bar_ts={latest_bar.get('timestamp')}",
                            f"close={latest_price}",
                            f"position_qty={self._state.position_qty if self._state else 0.0}",
                            f"signal={self._last_signal}",
                            flush=True,
                        )

                    stable_bars, processed_ts = self._stable_bars_for_signal(bars)

                    if (
                        processed_ts is not None
                        and (
                            self._last_processed_bar_ts is None
                            or int(processed_ts) > int(self._last_processed_bar_ts)
                        )
                    ):
                        signal = int(self._latest_signal_from_bars(stable_bars, cfg))
                        self._last_signal = signal
                        self._last_processed_bar_ts = int(processed_ts)

                        if self._state is not None and self._engine is not None:
                            fill = None
                            qty_now = float(self._state.position_qty or 0.0)
                            position_side = str(getattr(self._state, "side", None) or "").lower() or None
                            is_flat = abs(qty_now) <= 1e-12

                            # side-aware marker: signal > 0 and position_side == 'short' means cover short first.
                            if cfg.exit_on_signal and signal > 0 and position_side == "short":
                                if self._uses_exchange_execution_locked():
                                    fill = await self._submit_exchange_order_locked(
                                        side="buy",
                                        qty=abs(qty_now),
                                        fill_type="EXIT",
                                        market_price=latest_price,
                                        trade_id=int(getattr(self._state, "active_trade_id", None) or self._trade_id or 0),
                                        reduce_only=True,
                                    )
                                else:
                                    self._state, fill = self._engine.exit_short(
                                        ts_iso=str(int(processed_ts)),
                                        state=self._state,
                                        close=latest_price,
                                        market_type=cfg.market_type,
                                        trade_id=self._trade_id,
                                    )
                                if fill is not None:
                                    if not self._uses_exchange_execution_locked():
                                        await self._append_fill(fill)
                                    await self._broadcast_trace_event(
                                        "signal_fill",
                                        data={
                                            "signal": signal,
                                            "processed_bar_ts": int(processed_ts),
                                            "price": latest_price,
                                            "trade_id": getattr(fill, "trade_id", None),
                                            "fill_type": getattr(fill, "type", None),
                                        },
                                    )
                                qty_now = float(self._state.position_qty or 0.0)
                                position_side = str(getattr(self._state, "side", None) or "").lower() or None
                                is_flat = abs(qty_now) <= 1e-12

                            if signal > 0 and is_flat:
                                entry_gate = self._check_entry_gate_locked(latest_price, now_ts=processed_ts)
                                if entry_gate["allowed"]:
                                    self._trade_id += 1
                                    qty_override = self._resolve_entry_qty_locked(latest_price)
                                    if self._uses_exchange_execution_locked():
                                        fill = await self._submit_exchange_order_locked(
                                            side="buy",
                                            qty=float(qty_override or 0.0),
                                            fill_type="ENTRY",
                                            market_price=latest_price,
                                            trade_id=self._trade_id,
                                            reduce_only=False,
                                        )
                                    else:
                                        self._state, fill = self._engine.enter_long(
                                            ts_iso=str(int(processed_ts)),
                                            state=self._state,
                                            close=latest_price,
                                            market_type=cfg.market_type,
                                            leverage=cfg.leverage,
                                            trade_id=self._trade_id,
                                            qty_override=qty_override,
                                        )
                                    if fill is not None:
                                        if not self._uses_exchange_execution_locked():
                                            await self._append_fill(fill)
                                        await self._broadcast_trace_event(
                                            "signal_fill",
                                            data={
                                                "signal": signal,
                                                "processed_bar_ts": int(processed_ts),
                                                "price": latest_price,
                                                "trade_id": getattr(fill, "trade_id", None),
                                                "fill_type": getattr(fill, "type", None),
                                            },
                                        )
                                else:
                                    self._risk_halt_reason = entry_gate["reason"]
                                    await self._broadcast_trace_event(
                                        "entry_blocked",
                                        note=f"Risk blocked entry: {entry_gate['reason']}",
                                        data={
                                            "processed_bar_ts": int(processed_ts),
                                            "price": latest_price,
                                            **(entry_gate.get("meta", {}) or {}),
                                        },
                                    )

                            # side-aware marker: signal < 0 and position_side == 'long' means exit long first.
                            elif cfg.exit_on_signal and signal < 0 and position_side == "long":
                                if self._uses_exchange_execution_locked():
                                    fill = await self._submit_exchange_order_locked(
                                        side="sell",
                                        qty=abs(qty_now),
                                        fill_type="EXIT",
                                        market_price=latest_price,
                                        trade_id=int(getattr(self._state, "active_trade_id", None) or self._trade_id or 0),
                                        reduce_only=is_derivatives_market(cfg.market_type),
                                    )
                                else:
                                    self._state, fill = self._engine.exit_long(
                                        ts_iso=str(int(processed_ts)),
                                        state=self._state,
                                        close=latest_price,
                                        market_type=cfg.market_type,
                                        trade_id=self._trade_id,
                                    )
                                if fill is not None:
                                    if not self._uses_exchange_execution_locked():
                                        await self._append_fill(fill)
                                    await self._broadcast_trace_event(
                                        "signal_fill",
                                        data={
                                            "signal": signal,
                                            "processed_bar_ts": int(processed_ts),
                                            "price": latest_price,
                                            "trade_id": getattr(fill, "trade_id", None),
                                            "fill_type": getattr(fill, "type", None),
                                        },
                                    )
                                qty_now = float(self._state.position_qty or 0.0)
                                position_side = str(getattr(self._state, "side", None) or "").lower() or None
                                is_flat = abs(qty_now) <= 1e-12

                                if cfg.allow_short and is_flat and is_derivatives_market(cfg.market_type):
                                    self._trade_id += 1
                                    qty_override = self._resolve_entry_qty_locked(latest_price)
                                    if self._uses_exchange_execution_locked():
                                        fill = await self._submit_exchange_order_locked(
                                            side="sell",
                                            qty=float(qty_override or 0.0),
                                            fill_type="ENTRY",
                                            market_price=latest_price,
                                            trade_id=self._trade_id,
                                            reduce_only=False,
                                        )
                                    else:
                                        self._state, fill = self._engine.enter_short(
                                            ts_iso=str(int(processed_ts)),
                                            state=self._state,
                                            close=latest_price,
                                            market_type=cfg.market_type,
                                            leverage=cfg.leverage,
                                            trade_id=self._trade_id,
                                            qty_override=qty_override,
                                        )
                                    if fill is not None:
                                        if not self._uses_exchange_execution_locked():
                                            await self._append_fill(fill)
                                        await self._broadcast_trace_event(
                                            "signal_fill",
                                            data={
                                                "signal": signal,
                                                "processed_bar_ts": int(processed_ts),
                                                "price": latest_price,
                                                "trade_id": getattr(fill, "trade_id", None),
                                                "fill_type": getattr(fill, "type", None),
                                            },
                                        )

                            # side-aware marker: signal < 0 and is_flat and bool(cfg.allow_short) and is_derivatives_market(cfg.market_type) opens a fresh short.
                            elif signal < 0 and is_flat and bool(cfg.allow_short) and is_derivatives_market(cfg.market_type):
                                entry_gate = self._check_entry_gate_locked(latest_price, now_ts=processed_ts)
                                if entry_gate["allowed"]:
                                    self._trade_id += 1
                                    qty_override = self._resolve_entry_qty_locked(latest_price)
                                    if self._uses_exchange_execution_locked():
                                        fill = await self._submit_exchange_order_locked(
                                            side="sell",
                                            qty=float(qty_override or 0.0),
                                            fill_type="ENTRY",
                                            market_price=latest_price,
                                            trade_id=self._trade_id,
                                            reduce_only=False,
                                        )
                                    else:
                                        self._state, fill = self._engine.enter_short(
                                            ts_iso=str(int(processed_ts)),
                                            state=self._state,
                                            close=latest_price,
                                            market_type=cfg.market_type,
                                            leverage=cfg.leverage,
                                            trade_id=self._trade_id,
                                            qty_override=qty_override,
                                        )
                                    if fill is not None:
                                        if not self._uses_exchange_execution_locked():
                                            await self._append_fill(fill)
                                        await self._broadcast_trace_event(
                                            "signal_fill",
                                            data={
                                                "signal": signal,
                                                "processed_bar_ts": int(processed_ts),
                                                "price": latest_price,
                                                "trade_id": getattr(fill, "trade_id", None),
                                                "fill_type": getattr(fill, "type", None),
                                            },
                                        )
                                else:
                                    self._risk_halt_reason = entry_gate["reason"]
                                    await self._broadcast_trace_event(
                                        "entry_blocked",
                                        note=f"Risk blocked short entry: {entry_gate['reason']}",
                                        data={
                                            "processed_bar_ts": int(processed_ts),
                                            "price": latest_price,
                                            **(entry_gate.get("meta", {}) or {}),
                                        },
                                    )
                    self._persist_status()
                    self._persist_snapshot()
                    await self._sleep_or_stop(cfg.poll_seconds)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._reconnect_attempts += 1
                self._last_fetch_error = str(e)

                self._persist_status()
                self._persist_snapshot()

                await self._broadcast_trace_event(
                    "reconnect_wait",
                    note="Paper loop fetch/process error. Retrying.",
                    data={
                        "attempt": self._reconnect_attempts,
                        "error": str(e),
                    },
                )

                max_attempts = max(1, int(self._status.config.max_reconnect_attempts or 8))
                backoff_base = max(1.0, float(self._status.config.reconnect_backoff_base or 1.5))

                if self._reconnect_attempts >= max_attempts:
                    self._status.state = EngineState.ERROR
                    self._status.last_error = (
                        f"Paper loop stopped after {self._reconnect_attempts} reconnect attempts: {e}"
                    )
                    self._status.stopped_at = time.time()
                    self._persist_status()
                    self._persist_snapshot()

                    await self._broadcast_trace_event(
                        "paper_error",
                        note="Reconnect limit reached. Paper loop stopped.",
                        data={
                            "attempts": self._reconnect_attempts,
                            "error": str(e),
                        },
                    )
                    return

                delay = min(30.0, backoff_base ** max(0, self._reconnect_attempts - 1))
                await self._sleep_or_stop(delay)
                if not self._stop_event.is_set():
                    continue

    def _coerce_epoch_seconds(self, value: object) -> float | None:
        return self._risk_engine._coerce_epoch_seconds(value)

    def _day_key_from_ts(self, value: object | None = None) -> str:
        ts = self._coerce_epoch_seconds(value)
        if ts is None:
            return datetime.utcnow().strftime("%Y-%m-%d")
        return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")

    def _risk_day_key_now(self) -> str:
        if self._latest_bar and self._latest_bar.get("timestamp") is not None:
            return self._day_key_from_ts(self._latest_bar.get("timestamp"))
        return self._day_key_from_ts()

    def _roll_trade_day_if_needed(self, now_ts: object | None = None) -> None:
        today = self._day_key_from_ts(now_ts)
        if self._trade_day_key != today:
            self._trade_day_key = today
            self._trades_today = 0

    def _current_equity_locked(self, market_price: float | None = None) -> float:
        if self._state is None:
            return float(self._status.config.initial_balance or 0.0)

        resolved_price = market_price
        if resolved_price is None and self._latest_bar and self._latest_bar.get("close") is not None:
            try:
                resolved_price = float(self._latest_bar["close"])
            except Exception:
                resolved_price = None

        if resolved_price is None and self._state.entry_price is not None:
            resolved_price = float(self._state.entry_price)

        if self._engine is None or resolved_price is None:
            fallback_equity = self._state.equity
            if fallback_equity is None:
                fallback_equity = self._state.cash
            if fallback_equity is None:
                fallback_equity = self._status.config.initial_balance
            return float(fallback_equity or 0.0)

        return float(self._engine.mark_equity(
            state=self._state,
            market_type=self._status.config.market_type,
            market_price=float(resolved_price),
        ))

    def _reset_risk_runtime_locked(self) -> None:
        self._trade_day_key = self._risk_day_key_now()
        self._trades_today = 0
        self._last_exit_ts = None
        self._risk_halt_reason = None
        starting_equity = float(self._status.config.initial_balance or 0.0)
        self._peak_equity = max(0.0, starting_equity)
        self._position_peak_price = None

    def _update_peak_equity_locked(self, market_price: float | None = None) -> None:
        current_equity = self._current_equity_locked(market_price)
        self._peak_equity = max(float(self._peak_equity or 0.0), float(current_equity or 0.0))

    def _register_fill_for_risk_locked(self, fill: Fill) -> None:
        fill_ts = self._coerce_epoch_seconds(getattr(fill, "timestamp", None))
        self._roll_trade_day_if_needed(fill_ts)

        fill_type = str(getattr(fill, "type", "")).upper()
        if fill_type == "ENTRY":
            self._trades_today += 1
            try:
                self._position_peak_price = float(getattr(fill, "price", 0.0) or 0.0)
            except Exception:
                self._position_peak_price = None

        if fill_type in {"EXIT", "LIQUIDATION"}:
            self._last_exit_ts = fill_ts if fill_ts is not None else time.time()
            self._position_peak_price = None

        try:
            eq_after = float(getattr(fill, "equity_after", 0.0) or 0.0)
            self._peak_equity = max(float(self._peak_equity or 0.0), eq_after)
        except Exception:
            pass

    def _build_risk_payload(self) -> dict:
        self._roll_trade_day_if_needed()
        current_equity = self._current_equity_locked()
        peak_equity = max(float(self._peak_equity or 0.0), current_equity)
        drawdown_pct = 0.0
        if peak_equity > 0:
            drawdown_pct = max(0.0, (peak_equity - current_equity) / peak_equity * 100.0)

        return {
            "position_sizing_mode": self._status.config.position_sizing_mode,
            "position_size_value": self._status.config.position_size_value,
            "max_drawdown_pct": self._status.config.max_drawdown_pct,
            "max_trades_per_day": self._status.config.max_trades_per_day,
            "cooldown_seconds": self._status.config.cooldown_seconds,
            "stop_loss_pct": self._status.config.stop_loss_pct,
            "take_profit_pct": self._status.config.take_profit_pct,
            "peak_equity": peak_equity,
            "current_equity": current_equity,
            "drawdown_pct": drawdown_pct,
            "trades_today": self._trades_today,
            "trade_day": self._trade_day_key,
            "last_exit_ts": self._last_exit_ts,
            "risk_halt_reason": self._risk_halt_reason,
            "exit_mode": self._status.config.exit_mode,
            "atr_period": self._status.config.atr_period,
            "atr_stop_mult": self._status.config.atr_stop_mult,
            "atr_take_mult": self._status.config.atr_take_mult,
            "atr_reference_mode": self._status.config.atr_reference_mode,
            "maintenance_margin": self._status.config.maintenance_margin,
            "max_leverage": self._status.config.max_leverage,
        }

    def _compute_entry_qty_locked(self, entry_price: float) -> float | None:
        if self._state is None or self._engine is None:
            return None

        return self._risk_engine.compute_entry_qty(
            state=self._state,
            market_type=self._status.config.market_type,
            entry_price=entry_price,
            leverage=self._status.config.leverage,
            fee_rate=self._status.config.fee_rate,
            max_leverage=self._status.config.max_leverage,
            max_qty=self._status.config.max_qty,
            sizing_mode=self._status.config.position_sizing_mode,
            sizing_value=self._status.config.position_size_value,
            current_equity=self._current_equity_locked(entry_price),
            stop_loss_pct=self._status.config.stop_loss_pct,
            exit_mode=self._status.config.exit_mode,
            atr_value=self._latest_atr_value_locked(),
            atr_stop_mult=self._status.config.atr_stop_mult,
            enable_volatility_scaling=self._status.config.enable_volatility_scaling,
            volatility_target_pct=self._status.config.volatility_target_pct,
            min_volatility_scale=self._status.config.min_volatility_scale,
            max_volatility_scale=self._status.config.max_volatility_scale,
        )

    def _check_entry_gate_locked(self, market_price: float | None = None, now_ts: object | None = None) -> dict:
        self._roll_trade_day_if_needed(now_ts)
        current_equity = self._current_equity_locked(market_price)
        self._peak_equity = max(self._safe_float_value(self._peak_equity, 0.0), current_equity)

        effective_now_ts = now_ts
        if effective_now_ts is None and self._latest_bar and self._latest_bar.get("timestamp") is not None:
            effective_now_ts = self._latest_bar.get("timestamp")
        if effective_now_ts is None:
            effective_now_ts = time.time()

        result = self._risk_engine.evaluate_entry_gate(
            now_ts=effective_now_ts,
            current_equity=current_equity,
            peak_equity=self._peak_equity,
            trades_today=self._trades_today,
            last_exit_ts=self._last_exit_ts,
            max_drawdown_pct=self._status.config.max_drawdown_pct,
            max_trades_per_day=self._status.config.max_trades_per_day,
            cooldown_seconds=self._status.config.cooldown_seconds,
        )
        return {
            "allowed": result.allowed,
            "reason": result.reason,
            "meta": result.meta or {},
        }

    def _latest_atr_value_locked(self) -> float | None:
        period = int(getattr(self._status.config, "atr_period", 14) or 14)
        return latest_atr_value(self._bars, period)

    def _check_static_exit_locked(self, market_price: float) -> dict:
        if self._state is None or self._state.position_qty <= 0:
            return {"should_exit": False, "reason": None, "meta": {}}

        if self._position_peak_price is None:
            self._position_peak_price = float(market_price)
        else:
            self._position_peak_price = max(float(self._position_peak_price), float(market_price))

        result = self._risk_engine.evaluate_long_exit(
            entry_price=self._state.entry_price,
            market_price=market_price,
            stop_loss_pct=self._status.config.stop_loss_pct,
            take_profit_pct=self._status.config.take_profit_pct,
            exit_mode=self._status.config.exit_mode,
            atr_value=self._latest_atr_value_locked(),
            atr_stop_mult=self._status.config.atr_stop_mult,
            atr_take_mult=self._status.config.atr_take_mult,
            atr_reference_mode=self._status.config.atr_reference_mode,
            peak_price_since_entry=self._position_peak_price,
            fee_rate=getattr(self._engine, "fee_rate", 0.0),
        )
        return {
            "should_exit": result.should_exit,
            "reason": result.reason,
            "meta": result.meta or {},
        }

    async def _maybe_execute_static_exit_locked(
        self,
        *,
        market_price: float,
        now_ts: object | None = None,
    ) -> bool:
        if self._state is None or self._engine is None:
            return False

        if float(self._state.position_qty or 0.0) <= 0.0:
            return False

        decision = self._check_static_exit_locked(market_price)
        if not decision.get("should_exit"):
            return False

        effective_ts = now_ts
        if effective_ts is None and self._latest_bar:
            effective_ts = self._latest_bar.get("timestamp")

        ts_sec = self._coerce_epoch_seconds(effective_ts)
        ts_iso = (
            datetime.utcfromtimestamp(ts_sec).isoformat()
            if ts_sec is not None
            else str(int(time.time()))
        )

        if self._uses_exchange_execution_locked():
            exit_side = "sell"
            reduce_only = is_derivatives_market(self._status.config.market_type)
            fill = await self._submit_exchange_order_locked(
                side=exit_side,
                qty=float(abs(self._state.position_qty or 0.0)),
                fill_type="EXIT",
                market_price=float(market_price),
                trade_id=int(getattr(self._state, "active_trade_id", None) or self._trade_id or 0),
                reduce_only=reduce_only,
            )
        else:
            self._state, fill = self._engine.exit_long(
                ts_iso=ts_iso,
                state=self._state,
                close=float(market_price),
                market_type=self._status.config.market_type,
                trade_id=self._trade_id,
            )

        if not fill:
            return False

        if not self._uses_exchange_execution_locked():
            await self._append_fill(fill)
            await self._mark_to_market(float(market_price))
            self._persist_status()
            self._persist_snapshot()

        await self._broadcast_trace_event(
            "paper_risk_exit",
            note=f"Static {decision.get('reason')} triggered.",
            data={
                "reason": decision.get("reason"),
                "market_price": float(market_price),
                "trade_id": getattr(fill, "trade_id", None),
                "meta": decision.get("meta", {}) or {},
            },
        )
        return True


mode_controller = ModeController()
