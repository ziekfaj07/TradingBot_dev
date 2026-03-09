def connect_exchange(exchange_name: str):
    print(f"Accessing {exchange_name} exchange")
    return {
        "status": "success",
        "exchange": exchange_name,
        "message": f"Connected to {exchange_name} (mock)"
    }