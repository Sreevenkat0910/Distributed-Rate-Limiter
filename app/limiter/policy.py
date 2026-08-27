from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from starlette.requests import Request


@dataclass(frozen=True)
class RateLimitPolicy:
    name: str
    limit: int
    window_seconds: int
    key_func: Callable[[Request], str]


def client_ip_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def user_id_key(request: Request) -> str:
    return request.query_params.get("user_id") or "anonymous"
