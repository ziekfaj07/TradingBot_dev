from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from core.run_naming import csv_filename_from_run_id
from services.mode_controller import mode_controller

router = APIRouter(prefix="/paper", tags=["paper"])


@router.get("/fills")
async def get_paper_fills(
    limit: int = Query(default=200, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
):
    try:
        return mode_controller.get_paper_fills(limit=limit, offset=offset)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/chart")
async def get_paper_chart(
    limit: int = Query(default=300, ge=10, le=5000),
):
    try:
        return mode_controller.get_chart_snapshot(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/reset")
async def reset_paper():
    try:
        return await mode_controller.reset_paper()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/fills/export.csv")
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