from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/admin/limiter-status")
async def limiter_status(request: Request) -> dict[str, object]:
    """Live circuit breaker / degraded-mode status. Reads straight off the
    shared store's breaker state and last-observed latency -- not a cached
    or mocked snapshot, so it reflects whatever real traffic just did."""
    store = request.app.state.rate_limit_store
    breaker_state = store.circuit_breaker_state
    return {
        "breaker_state": breaker_state,
        "last_latency_ms": store.last_latency_ms,
        "degraded": breaker_state != "closed",
    }
