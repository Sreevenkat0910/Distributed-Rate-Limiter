from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from starlette.requests import Request

DegradedMode = Literal["fail_open", "fail_closed"]


@dataclass(frozen=True)
class RateLimitPolicy:
    name: str
    limit: int
    window_seconds: int
    key_func: Callable[[Request], str]
    # What to do when the breaker is open or the Redis call times out.
    # Set per-policy (not a global flag) so /login and /search can behave
    # independently.
    degraded_mode: DegradedMode = "fail_closed"


def client_ip_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def user_id_key(request: Request) -> str:
    return request.query_params.get("user_id") or "anonymous"
