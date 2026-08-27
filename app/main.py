import logging

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.search import router as search_router
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.limiter.middleware import RateLimitMiddleware

setup_logging()
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(title="Distributed Rate Limiter")

app.add_middleware(RateLimitMiddleware)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(search_router)


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("app_startup env=%s", settings.app_env)
