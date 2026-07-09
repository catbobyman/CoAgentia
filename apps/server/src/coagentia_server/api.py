"""ApiError → ErrorResponse 形状（契约 B §1/§3）与异常处理器注册。

错误形状零偏差：`{"error": {"code, message, rule, details}}`（rest.ErrorResponse）。
"""

from __future__ import annotations

from typing import Any

from coagentia_contracts import rest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """带契约错误码的业务异常（契约 B §3 目录）。"""

    def __init__(
        self,
        status: int,
        code: rest.ErrorCode,
        message: str,
        *,
        rule: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = rest.ErrorBody(code=code, message=message, rule=rule, details=details)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content=rest.ErrorResponse(error=exc.body).model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_req: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic/FastAPI 校验失败 → 统一 VALIDATION_FAILED 形状（契约 B §3；details 含字段路径）。
        body = rest.ErrorBody(
            code=rest.ErrorCode.VALIDATION_FAILED,
            message="请求校验失败",
            rule=None,
            details={"errors": _jsonable(exc.errors())},
        )
        return JSONResponse(status_code=422, content=rest.ErrorResponse(error=body).model_dump())


def _jsonable(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """剥掉 pydantic error 里不可 JSON 序列化的 ctx（如异常对象）。"""
    out: list[dict[str, Any]] = []
    for e in errors:
        clean = {k: v for k, v in e.items() if k != "ctx"}
        out.append(clean)
    return out
