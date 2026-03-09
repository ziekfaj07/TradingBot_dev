# services/mode_controller.py

from __future__ import annotations

import asyncio
import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional
from datetime import datetime, timezone

import numpy as np

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
    interval: str = "1h"
    market_type: str = "spot"       # "spot" | "futures"
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

    # paper/live loop cadence (not used by backtest)
    poll_seconds: float = 5.0


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
      - mode (BACKTEST/PAPER/LIVE)
      - lifecycle (IDLE/STARTING/RUNNING/STOPPING/ERROR)
      - config
    """

    def __init__(self):
        
        self.provider = BinanceVisionProvider()
        self._lock = asyncio.Lock()
        self._status = RunStatus()
        self.market_data = GateIOService()   # primary
        self.fallback_data = CoinGeckoService()  # fallback

        # PAPER/LIVE runtime controls
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

        # PAPER/LIVE runtime objects (kept here so status() can report)
        self._engine: Optional[ExecutionEngine] = None
        self._state: Optional[PortfolioState] = None
        self._trade_id: int = 0

    # -----------------------
    # Public API for FastAPI
    # -----------------------

    async def set_mode(self, mode: Mode) -> dict:
        async with self._lock:
            if self._status.state in (EngineState.STARTING, EngineState.RUNNING, EngineState.STOPPING):
                raise RuntimeError("Cannot change mode while engine is running.")
            self._status.mode = mode
            self._status.last_error = None
            return self.status()

    async def configure(self, **kwargs) -> dict:
        """
        Update config fields. To keep things safe/clean, only allow when IDLE.
        """
        async with self._lock:
            if self._status.state in (EngineState.STARTING, EngineState.RUNNING, EngineState.STOPPING):
                raise RuntimeError("Cannot configure while engine is running.")

            cfg = self._status.config
            for k, v in kwargs.items():
                if not hasattr(cfg, k):
                    raise ValueError(f"Unknown config field: {k}")
                setattr(cfg, k, v)

            # normalize constraints (same as your BacktestService)
            cfg.market_type = (cfg.market_type or "spot").lower()
            if cfg.market_type not in ("spot", "futures"):
                raise ValueError("market_type must be 'spot' or 'futures'")

            if cfg.market_type == "spot":
                cfg.leverage = 1.0
                cfg.allow_short = False

            if cfg.equity_stride < 1:
                cfg.equity_stride = 1

            self._status.last_error = None
            return self.status()

    async def start(self) -> dict:
        """
        Starts PAPER or LIVE continuous loop.
        BACKTEST should use run_backtest().
        """
        async with self._lock:
            if self._status.state != EngineState.IDLE:
                raise RuntimeError(f"Cannot start from state={self._status.state}")

            if self._status.mode == Mode.BACKTEST:
                raise RuntimeError("Mode is BACKTEST. Use run_backtest() for one-shot run.")

            if self._status.mode == Mode.LIVE:
                self._validate_live_safety_or_raise()

            cfg = self._status.config

            self._status.state = EngineState.STARTING
            self._status.run_id = uuid.uuid4().hex
            self._status.started_at = time.time()
            self._status.stopped_at = None
            self._status.last_error = None

            self._stop_event.clear()
            self._engine = self._build_engine(cfg)
            self._state = PortfolioState(cash=float(cfg.initial_balance))
            self._trade_id = 0

            self._task = asyncio.create_task(self._run_loop(), name=f"mode-run-{self._status.run_id}")
            self._status.state = EngineState.RUNNING
            return self.status()

    async def stop(self) -> dict:
        async with self._lock:
            if self._status.state != EngineState.RUNNING:
                return self.status()

            self._status.state = EngineState.STOPPING
            self._stop_event.set()

        # wait outside lock
        if self._task:
            try:
                await self._task
            except Exception:
                pass

        async with self._lock:
            self._task = None
            self._engine = None
            self._state = None
            self._status.stopped_at = time.time()
            # If an error happened, state will already be ERROR
            if self._status.state != EngineState.ERROR:
                self._status.state = EngineState.IDLE
            return self.status()

    def status(self) -> dict:
        s = self._status
        out = {
            "mode": s.mode.value,
            "state": s.state.value,
            "run_id": s.run_id,
            "started_at": s.started_at,
            "stopped_at": s.stopped_at,
            "last_error": s.last_error,
            "config": asdict(s.config),
        }

        # Optional runtime snapshot
        if self._engine is not None and self._state is not None:
            cfg = s.config
            # We can't mark equity without a price; show last known entry price as placeholder.
            # (When you implement paper feed, update this to last close.)
            last_price = float(self._state.entry_price or 0.0)
            out["runtime"] = {
                "cash": float(self._state.cash),
                "pos_qty": float(self._state.pos_qty),
                "entry_price": float(self._state.entry_price),
                "equity_estimate": float(self._engine.mark_equity(self._state, cfg.market_type, last_price)),
            }
        return out

    async def run_backtest(self, **kwargs) -> dict:
        """
        One-shot backtest using your exact BacktestService logic, centralized here.
        """
        async with self._lock:
            if self._status.state in (EngineState.STARTING, EngineState.RUNNING, EngineState.STOPPING):
                raise RuntimeError("Cannot run backtest while engine is running.")

            # copy current config, then apply overrides
            cfg = RunConfig(**asdict(self._status.config))
            for k, v in kwargs.items():
                if not hasattr(cfg, k):
                    raise ValueError(f"Unknown config field: {k}")
                setattr(cfg, k, v)

            # normalize constraints exactly like your service
            cfg.market_type = (cfg.market_type or "spot").lower()
            if cfg.market_type not in ("spot", "futures"):
                return {"error": "market_type must be 'spot' or 'futures'"}

            if cfg.market_type == "spot":
                cfg.leverage = 1.0
                cfg.allow_short = False

            if cfg.equity_stride < 1:
                cfg.equity_stride = 1

        # ----- heavy work outside lock -----
        df = self.provider.load_ohlcv(
            cfg.symbol, cfg.interval, cfg.market_type, start=cfg.start, end=cfg.end
        )
        if df is None or df.empty:
            return {"error": "No OHLCV data loaded"}

        df = StrategyEngine.ema_crossover(df)

        # ✅ Remove lookahead bias: signal computed on bar i acted on bar i+1
        df["signal"] = df["signal"].shift(1).fillna(0).astype(int)

        engine = self._build_engine(cfg)
        state = PortfolioState(cash=float(cfg.initial_balance))

        out = run_signal_backed_loop(
            df,
            engine=engine,
            state=state,
            market_type=cfg.market_type,
            leverage=cfg.leverage,
            include_equity=cfg.include_equity,
            equity_stride=cfg.equity_stride,
        )

        trades = out.trades
        equity_curve = out.equity_curve
        liquidated = out.liquidated
        final_equity = out.final_equity

        metrics = self._metrics(cfg.initial_balance, final_equity, equity_curve, trades, include_equity=cfg.include_equity)

        resp = {
            "symbol": cfg.symbol.upper(),
            "interval": cfg.interval,
            "market_type": cfg.market_type,
            "start": cfg.start,
            "end": cfg.end,
            "config": {
                "initial_balance": cfg.initial_balance,
                "fee_rate": cfg.fee_rate,
                "slippage_bps": cfg.slippage_bps,
                "allow_short": cfg.allow_short,
                "leverage": cfg.leverage,
                "maintenance_margin": cfg.maintenance_margin,
                "include_equity": cfg.include_equity,
                "equity_stride": cfg.equity_stride,
            },
            "results": metrics,
            "liquidated": liquidated,
            "trades": trades[-200:],
        }
        if cfg.include_equity:
            resp["equity_curve"] = equity_curve

        return resp

    # -----------------------
    # Internal helpers
    # -----------------------

    def _build_engine(self, cfg: RunConfig) -> ExecutionEngine:
        return ExecutionEngine(
            fee_rate=cfg.fee_rate,
            slippage_bps=cfg.slippage_bps,
            max_leverage=cfg.max_leverage,
            max_qty=cfg.max_qty,
            maintenance_margin=cfg.maintenance_margin,
        )

    def _validate_live_safety_or_raise(self) -> None:
        """
        Add real safety checks later (env flag, API keys, symbol whitelist, etc).
        """
        return

    async def _run_loop(self) -> None:
        """
        PAPER/LIVE loop skeleton (polling).
        You’ll plug in:
          - _get_latest_price_bar()
          - _compute_signal_from_stream()
        """
        try:
            while not self._stop_event.is_set():
                cfg = self._status.config
                assert self._engine is not None and self._state is not None

                bar = await self._get_latest_price_bar(cfg)   # {"timestamp": "...iso...", "close": float}
                ts_iso = bar["timestamp"]
                close = float(bar["close"])

                signal = self._compute_signal_from_stream(close, cfg)  # -1/0/1

                # futures liquidation guard
                if self._engine.liquidation_check(self._state, cfg.market_type, close, cfg.leverage) and self._state.pos_qty != 0.0:
                    self._state, fill = self._engine.liquidate(ts_iso, self._state, close, cfg.market_type, self._trade_id)
                    if fill:
                        self._trade_id += 1

                # entry/exit (long-only in spot)
                if signal == 1 and self._state.pos_qty == 0.0:
                    self._trade_id += 1
                    self._state, _ = self._engine.enter_long(ts_iso, self._state, close, cfg.market_type, cfg.leverage, self._trade_id)
                elif signal == -1 and self._state.pos_qty > 0.0:
                    self._state, _ = self._engine.exit_long(ts_iso, self._state, close, cfg.market_type, self._trade_id)

                await asyncio.sleep(cfg.poll_seconds)

        except asyncio.CancelledError:
            return
        except Exception as e:
            async with self._lock:
                self._status.last_error = f"{type(e).__name__}: {e}"
                self._status.state = EngineState.ERROR
            raise

    def _compute_signal_from_stream(self, close: float, cfg: RunConfig) -> int:
        """
        Plug-in point: incremental EMA crossover for PAPER/LIVE.
        For now: hold.
        """
        return 0

    # Same metrics as your BacktestService (kept here so controller is self-contained)
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
            if t.get("type") in ("EXIT", "LIQUIDATION"):
                if t.get("pnl") is not None:
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

    def _normalize_symbol_for_market_data(self, symbol: str) -> str:
        """
        Your MarketData services expect 'btc'/'eth'/'sol' (see SYMBOL_MAP).
        This normalizes common inputs like 'BTCUSDT', 'BTC_USDT', 'btc-usdt' -> 'btc'.
        """
        s = (symbol or "").lower()
        for suffix in ("usdt", "_usdt", "-usdt", "/usdt"):
            if s.endswith(suffix):
                s = s[: -len(suffix)]
                break
        # If symbol is like BTCUSDT and didn't match above (edge cases), try first 3 chars
        if len(s) > 4 and s.startswith(("btc", "eth", "sol")):
            return s[:3] if s[:3] in ("btc", "eth", "sol") else s
        return s


    async def _get_latest_price_bar(self, cfg: RunConfig) -> dict:
        """
        Polling implementation that matches your existing system: REST get_price().
        Returns: {"timestamp": "<iso8601>", "close": <float>}
        """
        sym = self._normalize_symbol_for_market_data(cfg.symbol)

        # Primary: GateIO spot ticker
        data = self.market_data.get_price(sym)
        if isinstance(data, dict) and "price_usd" in data:
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "close": float(data["price_usd"]),
            }

        # Fallback: CoinGecko
        data2 = self.fallback_data.get_price(sym)
        if isinstance(data2, dict) and "price_usd" in data2:
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "close": float(data2["price_usd"]),
            }

        err1 = data.get("error") if isinstance(data, dict) else str(data)
        err2 = data2.get("error") if isinstance(data2, dict) else str(data2)
        raise RuntimeError(f"Market data error. gateio={err1}; coingecko={err2}")

    async def latest_bar(self, symbol: str | None = None) -> dict:
        cfg = self._status.config
        tmp = type(cfg)(**cfg.__dict__)
        if symbol:
            tmp.symbol = symbol
        return await self._get_latest_price_bar(tmp)
