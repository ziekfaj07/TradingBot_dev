from __future__ import annotations

from datetime import datetime

import asyncio
import math
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd

from core.binance_vision_provider import BinanceVisionProvider
from core.database import (
    count_runtime_fills,
    delete_runtime_run,
    export_runtime_fills_csv,
    get_latest_paper_run,
    init_runtime_db,
    insert_runtime_fill,
    load_runtime_fills,
    save_runtime_snapshot,
    update_run_state,
    upsert_runtime_run,
)
from core.execution_models import Fill, PortfolioState
from core.run_naming import make_run_id

from services.execution_engine import ExecutionEngine
from services.market_data_service import CoinGeckoService, GateIOService
from services.runner import run_signal_backed_loop
from services.strategy_engine import StrategyEngine
from services.ws_manager import ws_manager

class Mode(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
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
    include_equity: bool = False
    equity_stride: int = 1
    poll_seconds: float = 5.0

    # legacy EMA field kept for backward compatibility
    ema_short: int = 9
    ema_long: int = 21

    #v0.4.5 generic strategy selection
    strategy_name: str = "ema_crossover"
    strategy_params: dict = field(default_factory=dict)

    candle_limit: int = 300


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
        self._state: Optional[PortfolioState] = None
        self._trade_id: int = 0

        self._latest_bar: dict | None = None
        self._bars: list[dict] = []
        self._last_processed_bar_ts: int | None = None
        self._last_signal: int = 0
        self._fills: list[Fill] = []
        self._trace: list[dict] = []

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

            # --- Resolve strategy name ---
            strategy_name = (
                kwargs.get("strategy_name")
                or self._status.config.strategy_name
                or "ema_crossover"
            )
            strategy_name = str(strategy_name).strip().lower()

            # --- Incoming params ---
            incoming = dict(kwargs.get("strategy_params", {}) or {})

            # --- Remove Swagger junk ---
            incoming.pop("additionalProp1", None)

            # --- Normalize common typos ---
            if "look_back" in incoming:
                incoming["lookback"] = incoming.pop("look_back")

            if "confirm_break_previous_extreme" in incoming:
                incoming["confirm_break_prev_extreme"] = incoming.pop(
                    "confirm_break_previous_extreme"
                )

            # --- Fold legacy EMA fields ---
            if kwargs.get("ema_short") is not None:
                incoming["short"] = int(kwargs["ema_short"])
            if kwargs.get("ema_long") is not None:
                incoming["long"] = int(kwargs["ema_long"])

            # --- Strategy-specific cleaning ---
            if strategy_name == "ema_crossover":
                clean = {
                    "short": int(incoming.get("short", 9)),
                    "long": int(incoming.get("long", 21)),
                }

            elif strategy_name == "donchian_breakout":
                clean = {
                    "lookback": int(incoming.get("lookback", 20))
                }

            elif strategy_name == "three_candle_reversal":
                clean = {
                    "min_body_ratio": float(incoming.get("min_body_ratio", 0.55)),
                    "require_full_range_engulf": bool(
                        incoming.get("require_full_range_engulf", True)
                    ),
                    "confirm_break_prev_extreme": bool(
                        incoming.get("confirm_break_prev_extreme", True)
                    ),
                }

            else:
                # fallback for future strategies
                clean = incoming

            # --- Apply updates ---
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
        effective_started_at = started_at if started_at is not None else self._status.started_at

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

        # backward compatibility for old EMA config fields
        if strategy_name == "ema_crossover":
            params.setdefault("short", int(getattr(cfg, "ema_short", 9)))
            params.setdefault("long", int(getattr(cfg, "ema_long", 21)))

        return params

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

            self._status.state = EngineState.STARTING
            self._status.started_at = time.time()
            self._status.stopped_at = None
            self._status.last_error = None

            # Always generate a fresh identity from the CURRENT config when starting.
            self._refresh_run_identity(
                self._status.config,
                started_at=self._status.started_at,
                mode=self._status.mode,
            )

            # Start a clean runtime for the current config/run identity.
            self._reset_runtime_memory()
            self._build_runtime_objects()

            self._stop_event = asyncio.Event()
            self._persist_status()
            self._persist_snapshot()

            self._task = asyncio.create_task(self._run_loop())

            self._status.state = EngineState.RUNNING
            self._persist_status()

            if self._status.mode == Mode.PAPER:
                await self._broadcast_trace_event(
                    "paper_started",
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

            if self._status.mode == Mode.PAPER:
                await self._broadcast_trace_event(
                    "paper_stopped",
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

            self._status.state = EngineState.STARTING
            self._status.started_at = time.time()
            self._status.stopped_at = None
            self._status.last_error = None

            # Backtest response/status should reflect the EFFECTIVE backtest config.
            self._refresh_run_identity(
                cfg,
                started_at=self._status.started_at,
                mode=Mode.BACKTEST,
            )

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
                raise RuntimeError("No historical data returned for backtest.")

            df = self._apply_strategy_to_df(df, cfg)
            df["signal"] = df["signal"].shift(1).fillna(0).astype(int)

            engine = ExecutionEngine(
                fee_rate=cfg.fee_rate,
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
            )

            output = run_signal_backed_loop(
                df,
                engine=engine,
                state=state,
                market_type=cfg.market_type,
                leverage=cfg.leverage,
                include_equity=cfg.include_equity,
                equity_stride=cfg.equity_stride,
            )

            async with self._lock:
                self._status.state = EngineState.IDLE
                self._status.stopped_at = time.time()
                self._persist_status()

            return {
                "status": self.status(),
                "result": {
                    "final_equity": output.final_equity,
                    "liquidated": output.liquidated,
                    "trades": output.trades,
                    "equity_curve": output.equity_curve,
                },
            }

        except Exception as e:
            async with self._lock:
                self._status.state = EngineState.ERROR
                self._status.last_error = str(e)
                self._status.stopped_at = time.time()
                self._persist_status()
            raise

    def status(self) -> dict:
        cfg = asdict(self._status.config)

        runtime = {
            "has_engine": self._engine is not None,
            "has_state": self._state is not None,
            "latest_bar": self._latest_bar,
            "bar_count": len(self._bars),
            "last_processed_bar_ts": self._last_processed_bar_ts,
            "last_signal": self._last_signal,
            "fill_count": len(self._fills),
            "fills": [getattr(f, "__dict__", f) for f in reversed(self._fills[-10:])],
            "paper_state": self._serialize_state(),
            "chart_symbol": cfg.get("symbol"),
            "chart_interval": cfg.get("interval"),
            "strategy_name": self._resolved_strategy_name_from_config(self._status.config),
            "strategy_params": self._resolved_strategy_params_from_config(self._status.config),
        }

        return {
            "mode": self._status.mode.value,
            "state": self._status.state.value,
            "run_id": self._status.run_id,
            "run_label": self._status.run_label,
            "started_at": self._status.started_at,
            "stopped_at": self._status.stopped_at,
            "last_error": self._status.last_error,
            "config": cfg,
            "runtime": runtime,
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
                "time": int(bar["timestamp"]),   # lightweight-charts wants UNIX seconds
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": float(bar.get("volume", 0.0)),
            }
        except (KeyError, TypeError, ValueError):
            return None

    def get_chart_snapshot(self, limit: int | None = None) -> dict:
        cfg = self._status.config
        effective_limit = max(10, int(limit or cfg.candle_limit or 300))

        # Prefer in-memory bars when available so chart matches current paper session exactly.
        bars = self._bars[-effective_limit:] if self._bars else []

        # If engine is idle or bars are empty, bootstrap from market data using current config.
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

        return {
            "symbol": cfg.symbol,
            "interval": cfg.interval,
            "candles": candles,
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

    async def force_buy(self, price: float | None = None, note: str | None = None) -> dict:
        async with self._lock:
            self._ensure_manual_paper_runtime_locked()

            if self._state is None or self._engine is None:
                raise RuntimeError("Paper runtime is not initialized.")

            if self._state.position_qty > 0:
                raise RuntimeError("Already in a long position.")

            resolved_price = await self._resolve_reference_price_locked(price)

            if self._state.position_qty < 0:
                self._state, fill = self._engine.liquidate(
                    ts_iso=str(int(time.time())),
                    state=self._state,
                    close=resolved_price,
                    market_type=self._status.config.market_type,
                    trade_id=self._trade_id,
                )
                if fill:
                    await self._append_fill(fill)

            self._trade_id += 1
            self._state, fill = self._engine.enter_long(
                ts_iso=str(int(time.time())),
                state=self._state,
                close=resolved_price,
                market_type=self._status.config.market_type,
                leverage=self._status.config.leverage,
                trade_id=self._trade_id,
            )

            if not fill:
                raise RuntimeError("Force buy did not produce a fill.")

            await self._append_fill(fill)
            await self._mark_to_market(resolved_price)
            self._persist_status()
            self._persist_snapshot()

            await self._broadcast_trace_event(
                "force_buy",
                note=note or "Manual paper buy executed.",
                data={
                    "price": resolved_price,
                    "trade_id": self._trade_id,
                    "position_qty": self._state.position_qty,
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
        await ws_manager.broadcast({
            "type": "trace",
            "data": entry,
        })
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

            old_run_id = self._status.run_id

            if old_run_id:
                delete_runtime_run(old_run_id)

            self._status = RunStatus(mode=Mode.PAPER)
            self._stop_event = asyncio.Event()
            self._task = None
            self._reset_runtime_memory()

            self._status.last_error = None
            self._status.started_at = None
            self._status.stopped_at = None
            self._status.run_id = None

            await ws_manager.broadcast({
                "type": "trace",
                "data": {
                    "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                    "event": "trace_cleared",
                    "note": "Paper reset cleared runtime trace.",
                    "run_id": None,
                    "data": {},
                },
            })

            return self.status()

    def export_paper_fills_csv(self) -> str:
        if not self._status.run_id:
            return (
                "id,timestamp,type,side,price,qty,fee,equity_after,"
                "entry_price,exit_price,trade_id,pnl,created_at\n"
            )
        return export_runtime_fills_csv(self._status.run_id)

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
        self._engine = ExecutionEngine(
            fee_rate=self._status.config.fee_rate,
            slippage_bps=self._status.config.slippage_bps,
            maintenance_margin=self._status.config.maintenance_margin,
            max_leverage=self._status.config.max_leverage,
            max_qty=self._status.config.max_qty,
        ) if restored_state else None

        self._fills = []
        for item in restored.get("fills", []):
            fill = Fill.from_dict(item)
            if fill:
                self._fills.append(fill)

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
            update_run_state(
                run_id=self._status.run_id,
                state=EngineState.IDLE.value,
                stopped_at=time.time(),
                last_error=self._status.last_error,
            )
        else:
            self._status.state = EngineState(restored_state_value)

    def _reset_runtime_memory(self) -> None:
        self._latest_bar = None
        self._bars = []
        self._last_processed_bar_ts = None
        self._last_signal = 0
        self._trade_id = 0
        self._fills = []
        self._trace = []
        self._state = None
        self._engine = None

    def _build_runtime_objects(self) -> None:
        cfg = self._status.config

        self._engine = ExecutionEngine(
            fee_rate=cfg.fee_rate,
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
        )

    def _build_runtime_objects_from_existing_or_new(self) -> None:
        if self._engine is None:
            cfg = self._status.config
            self._engine = ExecutionEngine(
                fee_rate=cfg.fee_rate,
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
                equity=self._status.config.initial_balance,
                liquidation_price=None,
                realized_pnl=0.0,
                active_trade_id=None,
            )

        if self._status.started_at is None:
            self._status.started_at = time.time()
        self._status.stopped_at = None
        self._status.last_error = None

    async def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                await self._paper_step()

                if self._stop_event.is_set():
                    break

                await asyncio.sleep(self._status.config.poll_seconds)
                
        except asyncio.CancelledError:
            raise
        except Exception as e:
            async with self._lock:
                self._status.state = EngineState.ERROR
                self._status.last_error = str(e)
                self._status.stopped_at = time.time()
                self._persist_status()
                self._persist_snapshot()
                await self._broadcast_trace_event(
                    "runtime_error",
                    note=str(e),
                    data={"run_id": self._status.run_id},
                )
            return

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

        await ws_manager.broadcast({
            "type": "status",
            "data": self.status(),
        })

        await ws_manager.broadcast({
            "type": "tick",
            "price": latest_price,
        })

        await ws_manager.broadcast({
            "type": "position",
            "qty": position_qty,
            "entry_price": entry_price,
        })

        await ws_manager.broadcast({
            "type": "equity",
            "equity": equity,
        })

        if latest_candle:
            await ws_manager.broadcast({
                "type": "candle",
                "symbol": self._status.config.symbol,
                "interval": self._status.config.interval,
                "candle": latest_candle,
            })

    async def _paper_step(self) -> None:
        async with self._lock:
            cfg = self._status.config

            bars = await asyncio.to_thread(
                self._fetch_recent_bars,
                cfg.symbol,
                cfg.interval,
                cfg.candle_limit,
            )

            if not bars:
                return

            self._bars = bars[-cfg.candle_limit:]
            self._latest_bar = self._bars[-1]

            closed_bar = self._get_newly_closed_bar(self._bars)
            if closed_bar is None:
                await self._mark_to_market(self._latest_bar["close"])
                self._persist_snapshot()
                await self._broadcast_runtime_update()
                return

            signal = self._compute_signal_from_closed_bars(self._bars)
            previous_signal = self._last_signal

            self._last_processed_bar_ts = closed_bar["timestamp"]
            self._last_signal = signal

            if signal != 0 and signal != previous_signal:
                await self._broadcast_trace_event(
                    "signal_changed",
                    data={
                        "signal": signal,
                        "bar_ts": closed_bar["timestamp"],
                        "price": closed_bar["close"],
                    },
                )

            print(
                f"[paper] closed_ts={closed_bar['timestamp']} "
                f"close={closed_bar['close']} signal={signal} "
                f"pos={self._state.position_qty if self._state else None} "
                f"fills={len(self._fills)}"
            )

            await self._apply_signal(signal=signal, bar=closed_bar)
            await self._mark_to_market(closed_bar["close"])
            self._persist_snapshot()
            await self._broadcast_runtime_update()

    def _fetch_recent_bars(self, symbol: str, interval: str, limit: int) -> list[dict]:
        try:
            bars = self.market_data.get_candles(symbol=symbol, interval=interval, limit=limit)
            return bars or []
        except Exception:
            return []

    def _get_newly_closed_bar(self, bars: list[dict]) -> dict | None:
        if len(bars) < 2:
            return None

        confirmed_bar = bars[-2]
        ts = confirmed_bar["timestamp"]

        if self._last_processed_bar_ts is None:
            self._last_processed_bar_ts = ts
            return confirmed_bar

        if ts > self._last_processed_bar_ts:
            return confirmed_bar

        return None

    def _compute_signal_from_closed_bars(self, bars: list[dict]) -> int:
        try:
            return self._latest_signal_from_bars(bars, self._status.config)
        except Exception as e:
            self._status.last_error = str(e)
            return 0

    async def _apply_signal(self, signal: int, bar: dict) -> None:
        if self._engine is None or self._state is None:
            return

        price = float(bar["close"])
        ts_iso = str(bar["timestamp"])
        cfg = self._status.config

        if signal == 1:
            if self._state.position_qty < 0:
                self._state, fill = self._engine.liquidate(
                    ts_iso=ts_iso,
                    state=self._state,
                    close=price,
                    market_type=cfg.market_type,
                    trade_id=self._trade_id,
                )
                if fill:
                    await self._append_fill(fill)

            if self._state.position_qty == 0:
                self._trade_id += 1
                self._state, fill = self._engine.enter_long(
                    ts_iso=ts_iso,
                    state=self._state,
                    close=price,
                    market_type=cfg.market_type,
                    leverage=cfg.leverage,
                    trade_id=self._trade_id,
                )
                if fill:
                    await self._append_fill(fill)

        elif signal == -1:
            if self._state.position_qty > 0:
                self._state, fill = self._engine.exit_long(
                    ts_iso=ts_iso,
                    state=self._state,
                    close=price,
                    market_type=cfg.market_type,
                    trade_id=self._trade_id,
                )
                if fill:
                    await self._append_fill(fill)

    async def _append_fill(self, fill) -> None:
        normalized = fill if isinstance(fill, Fill) else Fill.from_dict(getattr(fill, "__dict__", fill))
        if normalized is None:
            return

        self._fills.append(normalized)

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

        await ws_manager.broadcast({
            "type": "fills"
        })

        await self._broadcast_runtime_update()

    def _calculate_order_qty(self, price: float) -> float:
        if self._state is None or price <= 0:
            return 0.0

        notional = self._state.cash * 0.95
        if self._status.config.market_type == "futures":
            notional *= self._status.config.leverage

        qty = min(notional / price, self._status.config.max_qty)

        if not math.isfinite(qty) or qty <= 0:
            return 0.0

        return float(qty)

    async def _mark_to_market(self, price: float) -> None:
        if self._engine is None or self._state is None:
            return

        self._state.equity = self._engine.mark_equity(
            state=self._state,
            market_price=price,
            market_type=self._status.config.market_type,
        )

        if self._status.config.market_type == "futures":
            is_liquidatable = self._engine.compute_liquidation_price(
                state=self._state,
                market_type=self._status.config.market_type,
                market_price=price,
                leverage=self._status.config.leverage,
            )
            self._state.liquidation_price = None

            if is_liquidatable and self._state.position_qty != 0.0:
                self._state, fill = self._engine.liquidate(
                    ts_iso=str(int(time.time())),
                    state=self._state,
                    close=price,
                    market_type=self._status.config.market_type,
                    trade_id=self._trade_id,
                )
                if fill:
                    await self._append_fill(fill)

    def _serialize_state(self) -> dict | None:
        if self._state is None:
            return None

        return {
            "cash": self._state.cash,
            "position_qty": self._state.position_qty,
            "entry_price": self._state.entry_price,
            "side": self._state.side,
            "equity": self._state.equity,
            "liquidation_price": self._state.liquidation_price,
            "realized_pnl": self._state.realized_pnl,
            "active_trade_id": self._state.active_trade_id,
            "margin": self._state.margin,
            "borrowed": self._state.borrowed,
        }

mode_controller = ModeController()