import requests


print(requests.get("https://api.binance.com/api/v3/time", verify=False).text)