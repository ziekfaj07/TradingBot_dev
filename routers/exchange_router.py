from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from market.exceptions import ExchangeAdapterError
from services.exchange_service import (
    connect_exchange,
    get_exchange_capabilities,
    validate_live_config,
)

router = APIRouter(prefix="/api/exchange", tags=["exchange"])


class LiveConfigCheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exchange_name: str = "gateio"
    market_type: str = "spot"
    symbol: str = "BTCUSDT"
    enable_live_trading: bool = False
    dry_run_live: bool = True
    api_key_env: str = "GATEIO_API_KEY"
    api_secret_env: str = "GATEIO_API_SECRET"
    api_passphrase_env: str | None = "GATEIO_API_PASSPHRASE"


@router.get("/health/{exchange_name}")
def exchange_health(
    exchange_name: str,
    market_type: str = Query(default="spot"),
    testnet: bool = Query(default=False),
):
    try:
        return connect_exchange(
            exchange_name,
            market_type=market_type,
            testnet=testnet,
        )
    except ExchangeAdapterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/capabilities/{exchange_name}")
def exchange_capabilities(
    exchange_name: str,
    market_type: str = Query(default="spot"),
    testnet: bool = Query(default=False),
):
    try:
        return get_exchange_capabilities(
            exchange_name=exchange_name,
            market_type=market_type,
            testnet=testnet,
        )
    except ExchangeAdapterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/validate-live-config")
def check_live_config(body: LiveConfigCheckBody):
    try:
        return validate_live_config(**body.model_dump())
    except ExchangeAdapterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc