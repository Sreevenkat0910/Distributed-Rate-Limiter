import math

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.limiter.policies import ROUTE_POLICIES
from app.limiter.policy import RateLimitPolicy
from app.limiter.redis_store import RedisSlidingWindowStore


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
        decision = await self.store.check(policy.name, key, policy.limit, policy.window_seconds)

        headers = {
            "X-RateLimit-Limit": str(decision.limit),
            "X-RateLimit-Remaining": str(decision.remaining),
            "X-RateLimit-Reset": str(math.ceil(decision.reset_at)),
        }

        if not decision.allowed:
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

        response = await call_next(request)
        response.headers.update(headers)
        return response
