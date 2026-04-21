import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

load_dotenv()

from routers.dashboard_router import router as dashboard_router
from routers.exchange_router import router as exchange_router
from routers.export_router import router as export_router
from routers.run_router import router as run_router
from routers.strategy_router import router as strategy_router
from routers.ws_router import router as ws_router


def _cors_origins() -> list[str]:
    raw = os.getenv("TRADINGBOT_CORS_ORIGINS", "")
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    if origins:
        return origins
    return [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ]


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

app.include_router(dashboard_router)
app.include_router(exchange_router)
app.include_router(export_router)
app.include_router(run_router)
app.include_router(strategy_router)
app.include_router(ws_router)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health")
def health_check():
    return {"status": "ok", "message": "Trading Lab backend running"}
