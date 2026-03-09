from fastapi import APIRouter
from core.binance_vision_provider import BinanceVisionProvider

router = APIRouter()
provider = BinanceVisionProvider()

@router.get("/api/export/{symbol}")
def export_symbol(
    symbol: str,
    interval: str = "1h",
    market_type: str = "spot",
    start: str | None = None,
    end: str | None = None,
):
    path = provider.export_to_csv(symbol, interval, market_type, start=start, end=end)
    return {"ok": True, "file_path": path}