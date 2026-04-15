from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

logger = logging.getLogger(__name__)


class GateFuturesPrivateStream:
    def __init__(
        self,
        *,
        ws_url: str,
        api_key: str,
        api_secret: str,
        user_id: str,
        contract: str,
        on_order_update: Callable[[dict[str, Any]], Awaitable[None]],
        on_trade_update: Callable[[dict[str, Any]], Awaitable[None]],
        on_status_change: Callable[[bool, str | None], Awaitable[None]],
        ping_interval: float = 15.0,
    ) -> None:
        self.ws_url = str(ws_url)
        self.api_key = str(api_key)
        self.api_secret = str(api_secret)
        self.user_id = str(user_id)
        self.contract = str(contract)
        self.on_order_update = on_order_update
        self.on_trade_update = on_trade_update
        self.on_status_change = on_status_change
        self.ping_interval = max(5.0, float(ping_interval))
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def _sign(self, *, channel: str, event: str, ts_sec: int) -> str:
        message = f"channel={channel}&event={event}&time={ts_sec}"
        return hmac.new(self.api_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha512).hexdigest()

    async def _send(self, ws: websockets.WebSocketClientProtocol, *, channel: str, event: str, payload: list[Any], auth_required: bool = True) -> None:
        ts_sec = int(time.time())
        message: dict[str, Any] = {
            "time": ts_sec,
            "channel": channel,
            "event": event,
            "payload": payload,
        }
        if auth_required:
            message["auth"] = {
                "method": "api_key",
                "KEY": self.api_key,
                "SIGN": self._sign(channel=channel, event=event, ts_sec=ts_sec),
            }
        await ws.send(json.dumps(message))

    async def _subscribe(self, ws: websockets.WebSocketClientProtocol) -> None:
        payload = [self.user_id, self.contract]
        await self._send(ws, channel="futures.orders", event="subscribe", payload=payload)
        await self._send(ws, channel="futures.usertrades", event="subscribe", payload=payload)

    async def _ping_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(self.ping_interval)
                if self._stop_event.is_set():
                    return
                await self._send(ws, channel="futures.ping", event="subscribe", payload=[], auth_required=False)
            except Exception:
                return

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(self.ws_url, ping_interval=None, close_timeout=5) as ws:
                    await self._subscribe(ws)
                    await self.on_status_change(True, None)
                    backoff = 1.0
                    ping_task = asyncio.create_task(self._ping_loop(ws))
                    try:
                        async for raw_message in ws:
                            if self._stop_event.is_set():
                                break
                            try:
                                message = json.loads(raw_message)
                            except Exception:
                                continue
                            event = str(message.get("event") or "").lower()
                            channel = str(message.get("channel") or "")
                            if event == "subscribe":
                                continue
                            if event == "update":
                                result = message.get("result") or []
                                if not isinstance(result, list):
                                    continue
                                if channel == "futures.orders":
                                    for row in result:
                                        if isinstance(row, dict):
                                            await self.on_order_update(dict(row))
                                elif channel == "futures.usertrades":
                                    for row in result:
                                        if isinstance(row, dict):
                                            await self.on_trade_update(dict(row))
                    finally:
                        ping_task.cancel()
                        await self.on_status_change(False, None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Gate private stream reconnecting: %s", exc)
                await self.on_status_change(False, str(exc))
                await asyncio.sleep(backoff)
                backoff = min(15.0, backoff * 1.8)
