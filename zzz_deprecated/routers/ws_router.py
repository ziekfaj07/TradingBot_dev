from fastapi import APIRouter, WebSocket
import asyncio
from services.market_data_service import CoinGeckoService
from services.strategy_service import EMAStrategy
from services.paper_trading_service import PaperTrader

router = APIRouter()
trader = PaperTrader()

market_service = CoinGeckoService()
strategy = EMAStrategy()

# Store rolling price history in memory
price_history = []

@router.websocket("/ws/price/{symbol}")
async def price_stream(websocket: WebSocket, symbol: str):
    await websocket.accept()

    global price_history

    while True:

        data = market_service.get_price(symbol)

        if "price_usd" in data:
            price = data["price_usd"]
            price_history.append(price)
            price_history = price_history[-100:] # Keep only last 100 prices

            signal = strategy.generate_signal(price_history)

            trading_data = trader.update(price, signal)

        else:
            # If API fails, DO NOT override signal
            trading_data = {
                "balance_usd": trader.balance,
                "position_btc": trader.position,
                "total_equity": trader.balance + (trader.position * price if price else 0)
            }

        await websocket.send_json({
            "symbol": symbol.upper(),
            "price_usd": price,
            "signal": signal,
            "strategy": "EMA Cross (9/21)",
            "source": data.get("source"),
            "balance_usd": trading_data.get("balance_usd"),
            "position_btc": trading_data.get("position_btc"),
            "total_equity": trading_data.get("total_equity"),
        })

        await asyncio.sleep(2)