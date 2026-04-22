from __future__ import annotations

import os
from typing import Union

import certifi


VerifyArg = Union[bool, str]


def _truthy_false(value: str | None) -> bool:
    return str(value or "").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
        "disable",
        "disabled",
    }


def configure_ssl() -> None:
    """
    Configure SSL early.

    Priority:
    1. Use truststore if available so Python can use the Windows OS trust store.
    2. Fall back to certifi bundle.
    3. Allow explicit market-data-only disable through TRADINGBOT_SSL_VERIFY=false.
    """
    if _truthy_false(os.getenv("TRADINGBOT_SSL_VERIFY")):
        return

    try:
        import truststore

        truststore.inject_into_ssl()
        return
    except Exception:
        pass

    cert_path = (
        os.getenv("REQUESTS_CA_BUNDLE")
        or os.getenv("SSL_CERT_FILE")
        or certifi.where()
    )

    if cert_path and os.path.exists(cert_path):
        os.environ.setdefault("SSL_CERT_FILE", cert_path)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", cert_path)


def get_ssl_verify() -> VerifyArg:
    if _truthy_false(os.getenv("TRADINGBOT_SSL_VERIFY")):
        return False

    cert_path = (
        os.getenv("REQUESTS_CA_BUNDLE")
        or os.getenv("SSL_CERT_FILE")
        or certifi.where()
    )

    if cert_path and os.path.exists(cert_path):
        return cert_path

    return True