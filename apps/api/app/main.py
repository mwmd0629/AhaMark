from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from app.api.domain import ApiProblem
from app.api.routes import router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.request_id import bind_request_id, reset_request_id, safe_request_id

s = get_settings()
configure_logging(s.log_level)
log = structlog.get_logger()
app = FastAPI(title="AhaMark API", version="0.1.0")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=s.trusted_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=s.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    supplied = request.headers.get("x-request-id", "")
    request_id = safe_request_id(supplied)
    token = bind_request_id(request_id)
    request.state.request_id = request_id
    structlog.contextvars.bind_contextvars(request_id=request_id)
    try:
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith(("/api/", "/auth/", "/files/")):
            response.headers["Cache-Control"] = "no-store"
        log.info(
            "http_request",
            service="api",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
        )
        return response
    finally:
        structlog.contextvars.clear_contextvars()
        reset_request_id(token)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        {
            "code": f"HTTP_{exc.status_code}",
            "message": str(exc.detail),
            "details": {},
            "request_id": request.state.request_id,
        },
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled_error")
    return JSONResponse(
        {
            "code": "INTERNAL_ERROR",
            "message": "服务暂时不可用",
            "details": {},
            "request_id": request.state.request_id,
        },
        status_code=500,
    )


app.include_router(router)


@app.exception_handler(ApiProblem)
async def api_problem(request: Request, exc: ApiProblem) -> JSONResponse:
    return JSONResponse(
        {
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
            "request_id": request.state.request_id,
        },
        status_code=exc.status,
    )
