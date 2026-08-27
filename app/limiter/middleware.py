import math

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.limiter import store
from app.limiter.policies import ROUTE_POLICIES


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        policy = ROUTE_POLICIES.get((request.method, request.url.path))
        if policy is None:
            return await call_next(request)

        key = policy.key_func(request)
        decision = store.check(policy.name, key, policy.limit, policy.window_seconds)

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
