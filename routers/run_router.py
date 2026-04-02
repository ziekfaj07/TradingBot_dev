from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.run_naming import csv_filename_from_run_id
from core.strategy_schemas import StrategyRegistry
from services.controller_singleton import mode_controller
from services.mode_controller import Mode

router = APIRouter(prefix="/api/run", tags=["run"])


JSONScalar = str | int | float | bool | None
JSONDict = dict[str, JSONScalar]


class SetModeBody(BaseModel):
    mode: Mode


class ConfigureBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str | None = None
    interval: str | None = None
    market_type: str | None = None
    start: str | None = None
    end: str | None = None
    initial_balance: float | None = None
    fee_rate: float | None = None
    slippage_bps: float | None = None
    allow_short: bool | None = None
    leverage: float | None = None
    maintenance_margin: float | None = None
    max_leverage: float | None = None
    max_qty: float | None = None
    include_equity: bool | None = None
    equity_stride: int | None = None
    poll_seconds: float | None = None
    bar_confirmations: int | None = None
    max_reconnect_attempts: int | None = None
    reconnect_backoff_base: float | None = None
    dedupe_fill_window: int | None = None
    ema_short: int | None = None
    ema_long: int | None = None
    strategy_name: str | None = None
    strategy_params: JSONDict | None = Field(default=None)
    candle_limit: int | None = None
    debug_stream: bool | None = None

    position_sizing_mode: str | None = None
    position_size_value: float | None = None

    max_drawdown_pct: float | None = None
    max_trades_per_day: int | None = None
    cooldown_seconds: int | None = None

    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None

    @model_validator(mode="after")
    def validate_strategy_block(self) -> "ConfigureBody":
        if self.strategy_name is None and self.strategy_params:
            raise ValueError("strategy_params was provided but strategy_name is missing")

        canonical_name, normalized_params = StrategyRegistry.validate(
            self.strategy_name,
            self.strategy_params,
        )

        self.strategy_name = canonical_name
        self.strategy_params = normalized_params
        return self


class DevActionBody(BaseModel):
    price: float | None = None
    note: str | None = None


@router.get("/status")
async def status():
    return mode_controller.status()


@router.get("/paper/chart")
async def paper_chart(limit: int = 300):
    try:
        return mode_controller.get_chart_snapshot(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/paper/fills/export.csv")
async def export_paper_fills_csv():
    try:
        csv_text = mode_controller.export_paper_fills_csv()
        run_id = mode_controller.status().get("run_id") or "paper_run"
        filename = csv_filename_from_run_id(run_id)
        return PlainTextResponse(
            content=csv_text,
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            },
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/paper/fills")
async def paper_fills(limit: int = 200, offset: int = 0):
    try:
        return mode_controller.get_paper_fills(limit=limit, offset=offset)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/paper/dev/trace")
async def paper_trace(limit: int = 200, offset: int = 0):
    try:
        return mode_controller.get_trace(limit=limit, offset=offset)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/paper/dev/force-buy")
async def force_buy(body: DevActionBody = DevActionBody()):
    try:
        return await mode_controller.force_buy(price=body.price, note=body.note)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/paper/dev/force-sell")
async def force_sell(body: DevActionBody = DevActionBody()):
    try:
        return await mode_controller.force_sell(price=body.price, note=body.note)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/paper/dev/flatten")
async def flatten(body: DevActionBody = DevActionBody()):
    try:
        return await mode_controller.flatten_position(price=body.price, note=body.note)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/paper/reset")
async def reset_paper():
    try:
        return await mode_controller.reset_paper()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/mode")
async def set_mode(body: SetModeBody):
    try:
        return await mode_controller.set_mode(body.mode)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/configure")
async def configure(body: ConfigureBody):
    try:
        updates = {k: v for k, v in body.model_dump().items() if v is not None}
        return await mode_controller.configure(**updates)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/start")
async def start():
    try:
        return await mode_controller.start()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/stop")
async def stop():
    try:
        return await mode_controller.stop()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/backtest")
async def run_backtest(body: ConfigureBody):
    try:
        overrides = {k: v for k, v in body.model_dump().items() if v is not None}
        return await mode_controller.run_backtest(**overrides)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))