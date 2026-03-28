# routers/run_router.py
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from core.run_naming import csv_filename_from_run_id
from core.database import get_persisted_run, list_persisted_runs
from services.mode_controller import Mode
from services.controller_singleton import mode_controller

router = APIRouter(prefix="/api/run", tags=["run"])


class SetModeBody(BaseModel):
    mode: Mode


class ConfigureBody(BaseModel):
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

    strategy_name: str | None = None
    strategy_params: dict | None = None


class DevActionBody(BaseModel):
    price: float | None = None
    note: str | None = None


@router.get("/status")
async def status():
    return mode_controller.status()


@router.get("/paper/chart")
async def paper_chart(limit: int = Query(default=300, ge=10, le=5000)):
    try:
        return mode_controller.get_chart_snapshot(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/paper/fills")
async def paper_fills(
    limit: int = Query(default=200, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
):
    try:
        return mode_controller.get_paper_fills(limit=limit, offset=offset)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/paper/equity")
async def paper_equity(
    limit: int = Query(default=500, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
):
    try:
        return mode_controller.get_paper_equity(limit=limit, offset=offset)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/paper/metrics")
async def paper_metrics():
    try:
        return mode_controller.get_paper_metrics()
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
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/paper/equity/export.csv")
async def export_paper_equity_csv():
    try:
        csv_text = mode_controller.export_paper_equity_csv()
        run_id = mode_controller.status().get("run_id") or "paper_run"
        filename = csv_filename_from_run_id(run_id).replace(".csv", "-equity.csv")
        return PlainTextResponse(
            content=csv_text,
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/runs/history")
async def runs_history(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    mode: Optional[str] = Query(default=None),
    symbol: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    strategy_name: Optional[str] = Query(default=None),
):
    try:
        return list_persisted_runs(
            limit=limit,
            offset=offset,
            mode=mode,
            symbol=symbol,
            state=state,
            strategy_name=strategy_name,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/runs/history/{run_id}")
async def run_history_detail(
    run_id: str,
    fill_preview_limit: int = Query(default=50, ge=1, le=500),
    equity_preview_limit: int = Query(default=200, ge=1, le=1000),
):
    try:
        run = get_persisted_run(
            run_id=run_id,
            fill_preview_limit=fill_preview_limit,
            equity_preview_limit=equity_preview_limit,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return run
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/paper/dev/trace")
async def paper_trace(
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
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