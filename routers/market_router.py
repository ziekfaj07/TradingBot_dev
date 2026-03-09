from fastapi import APIRouter
from core.binance_vision_provider import BinanceVisionProvider

router = APIRouter()
provider = BinanceVisionProvider()


@router.get("/api/export/{symbol}")
def export_data(symbol: str, interval: str = "1h", market_type: str = "spot"):

    file_path = provider.export_to_csv(symbol.upper(), interval, market_type)

    return {
        "message": "Export successful",
        "file_path": file_path
    }