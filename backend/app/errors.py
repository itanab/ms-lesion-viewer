"""Uniform JSON error schema and exception handlers."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("mslv")


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def not_found(what: str = "Resource") -> AppError:
    return AppError(404, "NOT_FOUND", f"{what} not found.")


def _body(code: str, message: str, request: Request, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {},
                      "request_id": getattr(request.state, "request_id", "unknown")}}


def install_error_handling(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_and_headers(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex[:16]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        if request.url.path.startswith("/api/") and request.url.path != "/api/v1/health":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        log.info("error code=%s status=%s rid=%s", exc.code, exc.status, request.state.request_id)
        return JSONResponse(_body(exc.code, exc.message, request, exc.details), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        fields = [{"loc": [str(x) for x in e.get("loc", [])], "msg": str(e.get("msg", ""))} for e in exc.errors()]
        return JSONResponse(_body("VALIDATION_ERROR", "Request validation failed.", request, {"fields": fields}), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 413: "PAYLOAD_TOO_LARGE"}.get(exc.status_code, "HTTP_ERROR")
        msg = "Resource not found." if exc.status_code == 404 else str(exc.detail)
        return JSONResponse(_body(code, msg, request), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.error("unhandled %s rid=%s", type(exc).__name__, request.state.request_id)
        return JSONResponse(_body("INTERNAL_ERROR", "Unexpected server error.", request), status_code=500)
