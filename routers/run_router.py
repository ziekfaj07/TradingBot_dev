# routers/run_router.py

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.mode_controller import Mode, ModeController
from services.controller_singleton import mode_controller

router = APIRouter(prefix="/api/run", tags=["run"])


class SetModeBody(BaseModel):
    mode: Mode


class ConfigureBody(BaseModel):
    # Only provided fields will be updated
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

    ema_short: int | None = None
    ema_long: int | None = None
    candle_limit: int | None = None


@router.get("/status")
async def status():
    return mode_controller.status()


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
    """
    One-shot backtest endpoint that reuses the same controller.
    """
    try:
        overrides = {k: v for k, v in body.model_dump().items() if v is not None}
        return await mode_controller.run_backtest(**overrides)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))