import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """An expected failure with a fixed HTTP status and machine-readable code."""

    def __init__(self, status_code: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def error_response(
    status_code: int, code: str, message: str, details: Any = None, headers=None
) -> JSONResponse:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse({"error": error}, status_code=status_code, headers=headers)


# Specific codes for the fields clients most often get wrong, so they can branch
# on the code instead of parsing messages.
_FIELD_ERRORS = {
    "type": ("INVALID_TRANSACTION_TYPE", "type must be 'credit' or 'debit'."),
    "amount": (
        "INVALID_AMOUNT",
        "amount must be a number greater than 0 with at most 2 decimal places.",
    ),
    "page": ("INVALID_PAGINATION", "page must be an integer >= 1."),
    "limit": ("INVALID_PAGINATION", "limit must be an integer between 1 and 100."),
    "account_id": ("INVALID_ACCOUNT_ID", "account_id must be a valid UUID."),
    "transaction_id": ("INVALID_TRANSACTION_ID", "transaction_id must be a valid UUID."),
}

_HTTP_ERROR_CODES = {
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    status.HTTP_405_METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
}


def _field(error: dict) -> str:
    return ".".join(str(part) for part in error["loc"][1:]) or str(error["loc"][0])


async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    details = [{"field": _field(e), "message": e["msg"]} for e in errors]

    if any(e["type"] == "json_invalid" or tuple(e["loc"]) == ("body",) for e in errors):
        return error_response(
            status.HTTP_400_BAD_REQUEST,
            "INVALID_REQUEST_BODY",
            "Request body must be a valid JSON object.",
            details,
        )

    in_body = all(e["loc"][0] == "body" for e in errors)
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY if in_body else status.HTTP_400_BAD_REQUEST
    known = {_FIELD_ERRORS.get(_field(e)) for e in errors}
    if len(known) == 1 and None not in known:
        code, message = known.pop()
    elif in_body:
        code, message = "VALIDATION_ERROR", "Request body failed validation."
    else:
        code, message = "INVALID_PARAMETER", "Request parameters failed validation."
    return error_response(status_code, code, message, details)


async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return error_response(
        exc.status_code,
        _HTTP_ERROR_CODES.get(exc.status_code, "HTTP_ERROR"),
        str(exc.detail),
        headers=exc.headers,
    )


async def _unhandled_error(_request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled error", exc_info=exc)
    return error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", "An unexpected error occurred."
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled_error)
