from fastapi import APIRouter
from services.market_data_service import GateIOService
from services.strategy_service import EMAStrategy

router = APIRouter()

market_service = GateIOService()
strategy = EMAStrategy()

@router.get("/strategy/{symbol}")
def run_strategy(symbol: str):

    prices = market_service.get_candles(symbol)

    signal = strategy.generate_signal(prices)

    return {
        "symbol": symbol.upper(),
        "signal": signal,
        "strategy": "EMA Crossover 9/21"
    }