from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers.dashboard_router import router as dashboard_router
from routers.backtest_router import router as backtest_router
from routers.export_router import router as export_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard_router)
app.include_router(backtest_router)
app.include_router(export_router)

# Then define routes
@app.get("/health")
def health_check():
    return {"status": "ok", "message": "Trading Lab backend running"}


@app.get("/connect/{exchange_name}")
def connect(exchange_name: str):
    return connect_exchange(exchange_name)