import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.emails import router as emails_router
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.logging import configure_logging
from app.db.session import ping_database

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()  # fail fast on missing/invalid configuration
    configure_logging(settings.log_level)
    yield


def _validation_details(exc: RequestValidationError) -> list[dict]:
    # Drop `input`: it may echo the email body or other sensitive request content.
    return [
        {"loc": list(error.get("loc", ())), "msg": error.get("msg"), "type": error.get("type")}
        for error in exc.errors()
    ]


def create_app() -> FastAPI:
    app = FastAPI(title="Email Automation Backend", version="1.0.0", lifespan=lifespan)

    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_body())

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        body = ApiError(422, "VALIDATION_ERROR", "Request payload is invalid").to_body()
        body["error"]["details"] = _validation_details(exc)
        return JSONResponse(status_code=422, content=body)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        error = ApiError(500, "INTERNAL_ERROR", "Internal server error", retryable=True)
        return JSONResponse(status_code=500, content=error.to_body())

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        logger.info(
            "Request handled",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response

    @app.get("/health", tags=["health"])
    def health() -> JSONResponse:
        db_ok = ping_database()
        return JSONResponse(
            status_code=200 if db_ok else 503,
            content={"status": "ok" if db_ok else "degraded", "db": "up" if db_ok else "down"},
        )

    app.include_router(emails_router)
    return app


app = create_app()
