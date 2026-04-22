from __future__ import annotations

import os
from urllib.parse import urlparse
from typing import Any

import requests
import urllib3
from requests.exceptions import SSLError

from core.ssl_config import configure_ssl, get_ssl_verify


configure_ssl()


PUBLIC_MARKETDATA_SSL_FALLBACK_HOSTS = {
    "api.binance.com",
    "api-gcp.binance.com",
    "api1.binance.com",
    "api2.binance.com",
    "api3.binance.com",
    "api4.binance.com",
    "data-api.binance.vision",
    "data.binance.vision",
    "fapi.binance.com",
    "dapi.binance.com",
    "api.coingecko.com",
    "api.gateio.ws",
}


BLOCKED_MARKETDATA_MARKERS = (
    "Prohibited Access",
    "pldtsmartlogo",
    "may contain harmful and malicious content",
    "violation of Philippine laws",
    "Access Denied",
    "This website is blocked",
)


def _truthy(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default

    text = str(value).strip().lower()

    if text in {"1", "true", "yes", "y", "on", "enable", "enabled"}:
        return True

    if text in {"0", "false", "no", "n", "off", "disable", "disabled"}:
        return False

    return default


def _host(url: str) -> str:
    try:
        return urlparse(str(url)).hostname or ""
    except Exception:
        return ""


def allow_public_marketdata_ssl_fallback(url: str) -> bool:
    enabled = _truthy(
        os.getenv("TRADINGBOT_PUBLIC_MARKETDATA_SSL_FALLBACK"),
        default=True,
    )

    return enabled and _host(url).lower() in PUBLIC_MARKETDATA_SSL_FALLBACK_HOSTS


def is_blocked_marketdata_response(response: requests.Response) -> bool:
    text = response.text or ""

    if response.headers.get("content-type", "").lower().startswith("text/html"):
        if any(marker.lower() in text.lower() for marker in BLOCKED_MARKETDATA_MARKERS):
            return True

    if any(marker.lower() in text.lower() for marker in BLOCKED_MARKETDATA_MARKERS):
        return True

    return False


def response_json(response: requests.Response) -> Any:
    """
    Safer JSON helper for public market-data calls.

    This prevents the bot from throwing vague:
        Expecting value: line 1 column 1

    when the ISP returns an HTML block page instead of exchange JSON.
    """
    if is_blocked_marketdata_response(response):
        host = _host(response.url)
        raise RuntimeError(
            f"Public market-data request blocked by ISP/network filter. "
            f"host={host} url={response.url}"
        )

    try:
        return response.json()
    except ValueError as exc:
        preview = (response.text or "")[:240].replace("\n", " ").replace("\r", " ")
        raise RuntimeError(
            f"Expected JSON market-data response but got non-JSON content. "
            f"status={response.status_code} url={response.url} preview={preview!r}"
        ) from exc


def request_get(url: str, **kwargs: Any) -> requests.Response:
    kwargs.setdefault("timeout", 20)
    kwargs.setdefault("verify", get_ssl_verify())

    try:
        response = requests.get(url, **kwargs)
    except SSLError:
        if not allow_public_marketdata_ssl_fallback(url):
            raise

        fallback_kwargs = dict(kwargs)
        fallback_kwargs["verify"] = False

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        response = requests.get(url, **fallback_kwargs)

    if is_blocked_marketdata_response(response):
        host = _host(response.url)
        raise RuntimeError(
            f"Public market-data request blocked by ISP/network filter. "
            f"host={host} url={response.url}"
        )

    return response


class MarketDataSession(requests.Session):
    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("verify", get_ssl_verify())

        try:
            response = super().request(method, url, **kwargs)
        except SSLError:
            if not allow_public_marketdata_ssl_fallback(url):
                raise

            kwargs["verify"] = False
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            response = super().request(method, url, **kwargs)

        if is_blocked_marketdata_response(response):
            host = _host(response.url)
            raise RuntimeError(
                f"Public market-data request blocked by ISP/network filter. "
                f"host={host} url={response.url}"
            )

        return response


def request_session() -> requests.Session:
    session = MarketDataSession()
    session.verify = get_ssl_verify()
    return session