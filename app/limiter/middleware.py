import logging
import math

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.limiter.policies import ROUTE_POLICIES
from app.limiter.policy import RateLimitPolicy
from app.limiter.redis_store import RateLimiterUnavailableError, RedisSlidingWindowStore

logger = logging.getLogger(__name__)


def _log_decision(endpoint: str, key: str, decision: str, reason: str, breaker_state: str) -> None:
    logger.info(
        "rate_limit_decision",
        extra={
            "structured": {
                "endpoint": endpoint,
                "key": key,
                "decision": decision,
                "reason": reason,
                "breaker_state": breaker_state,
            }
        },
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        store: RedisSlidingWindowStore,
        policies: dict[tuple[str, str], RateLimitPolicy] | None = None,
    ) -> None:
        super().__init__(app)
        self.store = store
        # Overridable so tests can swap in a short-window policy without
        # touching the real route table; defaults to the app's real policies.
        self.policies = ROUTE_POLICIES if policies is None else policies

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        policy = self.policies.get((request.method, request.url.path))
        if policy is None:
            return await call_next(request)

        key = policy.key_func(request)
        try:
            decision = await self.store.check(policy.name, key, policy.limit, policy.window_seconds)
        except RateLimiterUnavailableError:
            return await self._handle_degraded(request, call_next, policy, key)

        breaker_state = self.store.circuit_breaker_state
        headers = {
            "X-RateLimit-Limit": str(decision.limit),
            "X-RateLimit-Remaining": str(decision.remaining),
            "X-RateLimit-Reset": str(math.ceil(decision.reset_at)),
        }

        if not decision.allowed:
            _log_decision(request.url.path, key, "deny", "over_limit", breaker_state)
            headers["Retry-After"] = str(math.ceil(decision.retry_after or 0))
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"Rate limit exceeded for '{policy.name}' "
                        f"({policy.limit} requests per {policy.window_seconds}s). "
                        "Try again later."
                    )
                },
                headers=headers,
            )

        _log_decision(request.url.path, key, "allow", "under_limit", breaker_state)
        response = await call_next(request)
        response.headers.update(headers)
        return response

    async def _handle_degraded(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
        policy: RateLimitPolicy,
        key: str,
    ) -> Response:
        breaker_state = self.store.circuit_breaker_state

        if policy.degraded_mode == "fail_open":
            _log_decision(request.url.path, key, "allow", "breaker_open_fail_open", breaker_state)
            response = await call_next(request)
            response.headers["X-RateLimiter-Degraded"] = "fail-open"
            return response

        _log_decision(request.url.path, key, "deny", "breaker_open_fail_closed", breaker_state)
        return JSONResponse(
            status_code=503,
            content={
                "error": "limiter_degraded",
                "detail": (
                    "Rate limiter is degraded and this endpoint fails closed for "
                    "safety: denying the request rather than risking an "
                    "unenforced limit. This is not a rate-limit rejection."
                ),
            },
            headers={"X-RateLimiter-Degraded": "fail-closed"},
        )
