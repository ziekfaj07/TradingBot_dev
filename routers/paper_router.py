from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

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
        return PlainTextResponse(
            content=csv_text,
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="paper_fills.csv"'
            },
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))