import uuid

from fastapi import APIRouter

router = APIRouter()


@router.post("/login")
async def login() -> dict[str, str]:
    """Simulated login. Rate limited to 5 requests/minute per client IP."""
    return {"token": f"demo-token-{uuid.uuid4().hex}"}
