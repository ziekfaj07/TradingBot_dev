import requests

BASE_URL = "https://api.bybit.com"

response = requests.get(
    BASE_URL + "/v5/market/kline",
    params={
        "category": "linear",
        "symbol": "BTCUSDT",
        "interval": "3",
        "limit": 5
    },
    verify=False  # TEMPORARY
)

print(response.status_code)
print(response.text)