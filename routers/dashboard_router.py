from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"


@router.get("/")
def dashboard_root():
    return FileResponse(TEMPLATES_DIR / "dashboard.html")


@router.get("/dashboard")
def dashboard_page():
    return FileResponse(TEMPLATES_DIR / "dashboard.html")


@router.get("/backtest")
def backtest_page():
    return FileResponse(TEMPLATES_DIR / "backtest.html")
