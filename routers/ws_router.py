from fastapi import APIRouter, WebSocket
import asyncio

from services.controller_singleton import mode_controller

router = APIRouter()


@router.websocket("/ws/run")
async def ws_run(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(mode_controller.status())
            await asyncio.sleep(1)
    except Exception:
        return


@router.websocket("/ws/price/{symbol}")
async def ws_price(websocket: WebSocket, symbol: str):
    await websocket.accept()
    try:
        while True:
            # preferred if you added mode_controller.latest_bar()
            bar = await mode_controller.latest_bar(symbol)

            await websocket.send_json(
                {
                    "symbol": symbol.upper(),
                    "timestamp": bar["timestamp"],
                    "price_usd": bar["close"],
                    "run": mode_controller.status(),
                }
            )
            await asyncio.sleep(2)
    except Exception:
        return