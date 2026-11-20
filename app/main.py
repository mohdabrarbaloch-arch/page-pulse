"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.database import Base, engine
from app.routers import auth, pages
from app.schemas import HealthOut
from app.security import SlidingWindowLimiter
from app.services import graph_api

VERSION = "1.0.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("pagepulse")

_settings = get_settings()
STATIC_DIR = Path(__file__).resolve().parent / "static"

# One limiter for the whole API. Auth endpoints have their own, tighter one.
_api_limiter = SlidingWindowLimiter(
    limit=_settings.rate_limit_per_minute,
    enabled=_settings.rate_limit_enabled,
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Create tables on boot and report the data mode honestly."""
    Base.metadata.create_all(bind=engine)

    if graph_api.is_configured():
        logger.info("Facebook Graph API token detected: live page data is available.")
    else:
        logger.info(
            "No FB_GRAPH_TOKEN set. Live Facebook data is unavailable; the demo "
            "dataset is available at POST /api/pages/demo."
        )

    if _settings.is_production and not _settings.secret_key:
        raise RuntimeError(
            "Refusing to start in production without SECRET_KEY. Sessions would be "
            "invalidated on every restart."
        )

    yield


app = FastAPI(
    title="Page Pulse",
    description=(
        "A Facebook page analytics dashboard. Connect a page with a Graph API "
        "token, or explore the built-in sample dataset. Every metric states what "
        "it measured and over how many posts."
    ),
    version=VERSION,
    lifespan=lifespan,
)

# CORS is off unless origins are named explicitly. A wildcard with credentials
# is the classic way to hand a token to any site that asks.
if _settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Add security headers and enforce the API rate limit."""
    if request.url.path.startswith("/api/"):
        forwarded = request.headers.get("x-forwarded-for", "")
        client = (
            forwarded.split(",")[0].strip()
            if forwarded
            else (request.client.host if request.client else "unknown")
        )
        allowed, retry_after = _api_limiter.check(client)
        if not allowed:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": f"Rate limit exceeded. Try again in {retry_after} seconds."},
                headers={"Retry-After": str(retry_after)},
            )

    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"

    # The dashboard loads Chart.js from a CDN, so the CSP has to allow that one
    # origin. Everything else stays locked to self.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "font-src 'self' data:; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return 422 with the field-level detail, not a bare string."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "The request body failed validation.",
            "errors": [
                {
                    "field": ".".join(str(part) for part in error.get("loc", [])),
                    "message": error.get("msg", ""),
                    "type": error.get("type", ""),
                }
                for error in exc.errors()
            ],
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Keep HTTP errors in one consistent JSON shape."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(Exception)
async def unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Log the traceback, return a generic message.

    The detail stays in the server log: leaking a stack trace to a client is how
    internal paths and query fragments end up in someone's browser console.
    """
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An unexpected server error occurred. Check the server log."},
    )


@app.get("/health", response_model=HealthOut, tags=["system"])
def health() -> HealthOut:
    """Liveness plus which data mode the server is in."""
    return HealthOut(
        status="ok",
        version=VERSION,
        graph_configured=graph_api.is_configured(),
        environment=_settings.environment,
    )


app.include_router(auth.router)
app.include_router(pages.router)


# --- Static dashboard --------------------------------------------------------
# Mounted last so it never shadows an /api route.
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        """Serve the dashboard shell."""
        return FileResponse(str(STATIC_DIR / "index.html"))
