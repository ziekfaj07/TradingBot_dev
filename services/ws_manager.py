from typing import List
from fastapi import WebSocket
import asyncio
import json

class WSManager:
    def __init__(self):
        self.connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.connections:
            self.connections.remove(websocket)

    async def broadcast(self, data: dict):
        if not self.connections:
            return

        dead = []
        for ws in self.connections:
            try:
                await ws.send_text(json.dumps(data))
            except:
                dead.append(ws)

        # cleanup dead connections
        for ws in dead:
            self.disconnect(ws)


ws_manager = WSManager()