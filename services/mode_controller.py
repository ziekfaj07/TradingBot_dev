# services/mode_controller.py
from __future__ import annotations

import asyncio
import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd

from core.binance_vision_provider import BinanceVisionProvider
from core.execution_models import PortfolioState
from services.execution_engine import ExecutionEngine
from services.runner import run_signal_backed_loop
from services.strategy_engine import StrategyEngine
from services.market_data_service import GateIOService, CoinGeckoService


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
    # market
    symbol: str = "BTCUSDT"
    interval: str = "1m"
    market_type: str = "spot"   # "spot" | "futures"

    # backtest only
    start: str | None = None
    end: str | None = None

    # portfolio/execution
    initial_balance: float = 1000.0
    fee_rate: float = 0.001
    slippage_bps: float = 2.0
    allow_short: bool = False
    leverage: float = 1.0
    maintenance_margin: float = 0.005

    # engine guards
    max_leverage: float = 50.0
    max_qty: float = 10.0

    # output controls
    include_equity: bool = False
    equity_stride: int = 1

    # paper/live loop cadence
    poll_seconds: float = 5.0

    # EMA settings
    ema_short: int = 9
    ema_long: int = 21

    # rolling candle memory
    candle_limit: int = 300


@dataclass
class RunStatus:
    mode: Mode = Mode.BACKTEST
    state: EngineState = EngineState.IDLE
    run_id: Optional[str] = None
    started_at: Optional[float] = None
    stopped_at: Optional[float] = None
    last_error: Optional[str] = None
    config: RunConfig = field(default_factory=RunConfig)


class ModeController:
    """
    Single source of truth for:
    - mode (BACKTEST / PAPER / LIVE)
    - lifecycle (IDLE / STARTING / RUNNING / STOPPING / ERROR)
    - config
    """

    def __init__(self):
        self.provider = BinanceVisionProvider()
        self.market_data = GateIOService()      # primary
        self.fallback_data = CoinGeckoService() # fallback

        self._lock = asyncio.Lock()
        self._status = RunStatus()

        # runtime controls
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

        # runtime objects
        self._engine: Optional[ExecutionEngine] = None
        self._state: Optional[PortfolioState] = None
        self._trade_id: int = 0

        # paper/live memory
        self._latest_bar: dict | None = None
        self._bars: list[dict] = []
        self._last_processed_bar_ts: int | None = None
        self._last_signal: int = 0
        self._fills: list = []

    # -----------------------
    # Public API
    # -----------------------
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
            return self.status()

    async def configure(self, **kwargs) -> dict:
        async with self._lock:
            if self._status.state != EngineState.IDLE:
                raise RuntimeError("Can only configure while engine is idle.")

            config_dict = asdict(self._status.config)
            for key, value in kwargs.items():
                if key in config_dict and value is not None:
                    setattr(self._status.config, key, value)

            self._status.last_error = None
            return self.status()

    async def start(self) -> dict:
        async with self._lock:
            if self._status.state != EngineState.IDLE:
                raise RuntimeError("Engine must be idle before starting.")

            if self._status.mode == Mode.BACKTEST:
                raise RuntimeError("Use run_backtest() for backtest mode.")

            self._status.state = EngineState.STARTING
            self._status.run_id = str(uuid.uuid4())
            self._status.started_at = time.time()
            self._status.stopped_at = None
            self._status.last_error = None

            self._stop_event = asyncio.Event()
            self._reset_runtime_memory()
            self._build_runtime_objects()

            self._task = asyncio.create_task(self._run_loop())

            self._status.state = EngineState.RUNNING
            return self.status()

    async def stop(self) -> dict:
        async with self._lock:
            if self._status.state not in (EngineState.STARTING, EngineState.RUNNING):
                raise RuntimeError("Engine is not running.")

            self._status.state = EngineState.STOPPING
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
            return self.status()

    async def run_backtest(self) -> dict:
        """
        Keeps your backtest path separate from paper/live loop.
        """
        async with self._lock:
            if self._status.state != EngineState.IDLE:
                raise RuntimeError("Engine must be idle before running backtest.")

            self._status.state = EngineState.STARTING
            self._status.run_id = str(uuid.uuid4())
            self._status.started_at = time.time()
            self._status.stopped_at = None
            self._status.last_error = None

            cfg = self._status.config

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

            df = StrategyEngine.ema_crossover(
                df,
                short=cfg.ema_short,
                long=cfg.ema_long,
            )

            # shift by 1 bar to avoid lookahead
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
                active_trade_id=None
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
            "fills": [getattr(f, "__dict__", f) for f in self._fills[-10:]],
            "paper_state": self._serialize_state(),
        }

        return {
            "mode": self._status.mode.value,
            "state": self._status.state.value,
            "run_id": self._status.run_id,
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

    # -----------------------
    # Runtime internals
    # -----------------------
    def _reset_runtime_memory(self) -> None:
        self._latest_bar = None
        self._bars = []
        self._last_processed_bar_ts = None
        self._last_signal = 0
        self._trade_id = 0
        self._fills = []

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
            active_trade_id=None
        )

    async def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                await self._paper_step()
                await asyncio.sleep(self._status.config.poll_seconds)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            async with self._lock:
                self._status.state = EngineState.ERROR
                self._status.last_error = str(e)
                self._status.stopped_at = time.time()
            return

    async def _paper_step(self) -> None:
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
            self._mark_to_market(self._latest_bar["close"])
            return

        signal = self._compute_signal_from_closed_bars(self._bars)

        self._last_processed_bar_ts = closed_bar["timestamp"]
        self._last_signal = signal

        print(
            f"[paper] closed_ts={closed_bar['timestamp']} "
            f"close={closed_bar['close']} signal={signal} "
            f"pos={self._state.position_qty if self._state else None} "
            f"fills={len(self._fills)}"
        )

        self._apply_signal(signal=signal, bar=closed_bar)
        self._mark_to_market(closed_bar["close"])

    def _fetch_recent_bars(self, symbol: str, interval: str, limit: int) -> list[dict]:
        try:
            bars = self.market_data.get_candles(symbol=symbol, interval=interval, limit=limit)
            return bars or []
        except Exception:
            return []

    def _get_newly_closed_bar(self, bars: list[dict]) -> dict | None:
        """
        We use the SECOND TO LAST bar as the confirmed bar.
        Why?
        Because the very last bar may still be forming.
        """
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
        if len(bars) < max(self._status.config.ema_short, self._status.config.ema_long) + 2:
            return 0

        # use only fully closed bars -> exclude the newest still-forming bar
        closed = bars[:-1]
        df = pd.DataFrame(closed)

        strat = StrategyEngine.ema_crossover(
            df,
            short=self._status.config.ema_short,
            long=self._status.config.ema_long,
        )

        signal = int(strat.iloc[-1]["signal"])
        return signal

    def _apply_signal(self, signal: int, bar: dict) -> None:
        if self._engine is None or self._state is None:
            return

        price = float(bar["close"])
        ts_iso = str(bar["timestamp"])
        cfg = self._status.config

        # 1 = bullish cross
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
                    self._fills.append(fill)

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
                    self._fills.append(fill)

        # -1 = bearish cross
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
                    self._fills.append(fill)

            # shorts not implemented yet

    def _calculate_order_qty(self, price: float) -> float:
        if self._state is None or price <= 0:
            return 0.0

        # simple sizing:
        # use nearly all available paper cash
        notional = self._state.cash * 0.95

        if self._status.config.market_type == "futures":
            notional *= self._status.config.leverage

        qty = notional / price
        qty = min(qty, self._status.config.max_qty)

        if not math.isfinite(qty) or qty <= 0:
            return 0.0

        return float(qty)

    def _mark_to_market(self, price: float) -> None:
        if self._engine is None or self._state is None:
            return

        self._state.equity = self._engine.mark_equity(
            state=self._state,
            market_price=price,
            market_type=self._status.config.market_type,
        )

        # Current engine method returns a bool liquidation condition, not a price.
        # So do not store it as liquidation_price.
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
                    self._fills.append(fill)

    def _serialize_state(self) -> dict | None:
        if self._state is None:
            return None

        return {
            "cash": self._state.cash,
            "position_qty": self._state.position_qty,
            "entry_price": self._state.entry_price,
            "equity": self._state.equity,
            "liquidation_price": self._state.liquidation_price,
            "realized_pnl": self._state.realized_pnl,
        }