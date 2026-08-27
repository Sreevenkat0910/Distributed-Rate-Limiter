import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.search import router as search_router
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.limiter.middleware import RateLimitMiddleware
from app.limiter.redis_store import RedisSlidingWindowStore

setup_logging()
logger = logging.getLogger(__name__)

settings = get_settings()

rate_limit_store = RedisSlidingWindowStore(settings.redis_url, max_connections=settings.redis_max_connections)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await rate_limit_store.connect()
    logger.info("app_startup env=%s", settings.app_env)
    yield
    await rate_limit_store.close()


app = FastAPI(title="Distributed Rate Limiter", lifespan=lifespan)

app.add_middleware(RateLimitMiddleware, store=rate_limit_store)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(search_router)
