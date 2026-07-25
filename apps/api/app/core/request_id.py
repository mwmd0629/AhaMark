import contextvars
import uuid
from collections.abc import Mapping
from typing import Any, TypeGuard

REQUEST_ID_HEADER = "request_id"
MAX_REQUEST_ID_LENGTH = 64
_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def valid_request_id(value: object) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_REQUEST_ID_LENGTH
        and value.isascii()
        and all(character.isalnum() or character in "-_." for character in value)
    )


def safe_request_id(value: object = None) -> str:
    return value if valid_request_id(value) else str(uuid.uuid4())


def bind_request_id(value: object = None) -> contextvars.Token[str | None]:
    return _request_id.set(safe_request_id(value))


def reset_request_id(token: contextvars.Token[str | None]) -> None:
    _request_id.reset(token)


def current_request_id() -> str:
    value = _request_id.get()
    if valid_request_id(value):
        return value
    generated = safe_request_id()
    _request_id.set(generated)
    return generated


def celery_request_headers() -> dict[str, str]:
    """Return the only HTTP context allowed onto the task message."""
    return {REQUEST_ID_HEADER: current_request_id()}


def request_id_from_task_headers(headers: Mapping[str, Any] | None) -> str:
    return safe_request_id((headers or {}).get(REQUEST_ID_HEADER))
