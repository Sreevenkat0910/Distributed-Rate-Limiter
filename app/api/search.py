from fastapi import APIRouter, Query

router = APIRouter()


@router.get("/search")
async def search(
    user_id: str = Query(..., description="Identifies the caller for rate limiting"),
) -> dict[str, object]:
    """Simulated search. Rate limited to 100 requests/minute per `user_id`
    query param (the key chosen for this endpoint, over a header)."""
    return {
        "user_id": user_id,
        "query": "demo",
        "results": ["result-1", "result-2", "result-3"],
    }
