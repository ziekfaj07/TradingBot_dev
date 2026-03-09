from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "title": "Trading Dashboard"
    })

@router.get("/backtest")
def backtest_page(request: Request):
    return templates.TemplateResponse("backtest.html", {"request": request})