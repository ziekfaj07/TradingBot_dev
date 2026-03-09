from fastapi import APIRouter
from services.exchange_service import connect_exchange

router = APIRouter()

@router.get("/connect/{exchange_name}")
def connect(exchange_name: str):
    return connect_exchange(exchange_name)