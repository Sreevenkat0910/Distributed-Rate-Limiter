from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


class ReplicaIdMiddleware(BaseHTTPMiddleware):
    """Stamps X-Replica-Id on every response so load balancing across
    containers can be verified from the outside. Kept separate from
    RateLimitMiddleware since it applies to every route, including /health."""

    def __init__(self, app: ASGIApp, replica_id: str) -> None:
        super().__init__(app)
        self.replica_id = replica_id

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Replica-Id"] = self.replica_id
        return response
