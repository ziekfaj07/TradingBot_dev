from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from routers.dashboard_router import router as dashboard_router
from routers.export_router import router as export_router
from routers.run_router import router as run_router
from routers.strategy_router import router as strategy_router
from routers.ws_router import router as ws_router
from services.exchange_service import connect_exchange

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard_router)
app.include_router(export_router)
app.include_router(run_router)
app.include_router(strategy_router)
app.include_router(ws_router)

app.mount("/static", StaticFiles(directory="static"), name="static")

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "templates"


@app.get("/")
def root():
    return FileResponse(FRONTEND_DIR / "dashboard.html")


@app.get("/dashboard")
def dashboard_page():
    return FileResponse(FRONTEND_DIR / "dashboard.html")


@app.get("/backtest")
def backtest_page():
    return FileResponse(FRONTEND_DIR / "backtest.html")


@app.get("/health")
def health_check():
    return {"status": "ok", "message": "Trading Lab backend running"}


@app.get("/connect/{exchange_name}")
def connect(exchange_name: str):
    return connect_exchange(exchange_name)