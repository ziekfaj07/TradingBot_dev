def connect_exchange(name: str):
    print(f"Accessing {name} exchange...")
    return {
        "status": "connected",
        "exchange": name
    }