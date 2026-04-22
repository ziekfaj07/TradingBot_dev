from __future__ import annotations

from core.ssl_config import configure_ssl

configure_ssl()

import os
import secrets

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.utils import get_openapi

load_dotenv()

try:
    import certifi

    cert_path = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", cert_path)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", cert_path)
except Exception:
    pass

from routers.dashboard_router import router as dashboard_router
from routers.exchange_router import router as exchange_router
from routers.run_router import router as run_router
from routers.strategy_router import router as strategy_router
from routers.ws_router import router as ws_router

try:
    from routers.export_router import router as export_router
except Exception:
    export_router = None


def _cors_origins() -> list[str]:
    raw = os.getenv("TRADINGBOT_CORS_ORIGINS", "").strip()
    if raw:
        return [item.strip() for item in raw.split(",") if item.strip()]

    return [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:9000",
        "http://localhost:9000",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ]


app = FastAPI(title="TradingBot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-API-Key"],
)


_API_KEY = os.getenv("TRADINGBOT_API_KEY", "").strip() or None
_PROTECTED_PREFIXES = (
    "/api/run",
    "/api/exchange",
)
_PROTECTED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

if _API_KEY is None:
    print(
        "[WARNING] TRADINGBOT_API_KEY is not set. "
        "Mutating live/run/exchange routes are currently unprotected. "
        "Set TRADINGBOT_API_KEY before exposing this service outside local dev."
    )


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    if _API_KEY is None:
        return await call_next(request)

    path = request.url.path
    method = request.method.upper()

    needs_auth = (
        method in _PROTECTED_METHODS
        and any(path.startswith(prefix) for prefix in _PROTECTED_PREFIXES)
    )

    if not needs_auth:
        return await call_next(request)

    provided = request.headers.get("X-API-Key", "").strip()

    if not provided:
        auth_header = request.headers.get("Authorization", "").strip()
        if auth_header.lower().startswith("bearer "):
            provided = auth_header[7:].strip()

    if not provided or not secrets.compare_digest(provided, _API_KEY):
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key."},
        )

    return await call_next(request)


app.include_router(dashboard_router)
app.include_router(exchange_router)

if export_router is not None:
    app.include_router(export_router)

app.include_router(run_router)
app.include_router(strategy_router)
app.include_router(ws_router)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "message": "Trading Lab backend running",
        "api_auth_enabled": _API_KEY is not None,
    }


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title="TradingBot API",
        version="0.1.0",
        description="TradingBot backend API",
        routes=app.routes,
    )

    openapi_schema.setdefault("components", {}).setdefault("securitySchemes", {})
    openapi_schema["components"]["securitySchemes"]["ApiKeyAuth"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
    }

    for path, path_item in openapi_schema.get("paths", {}).items():
        if path.startswith("/api/run") or path.startswith("/api/exchange"):
            for method, operation in path_item.items():
                if method.lower() in {"post", "put", "patch", "delete"}:
                    operation.setdefault("security", [{"ApiKeyAuth": []}])

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi