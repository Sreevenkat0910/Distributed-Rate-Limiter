import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.search import router as search_router
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.replica_middleware import ReplicaIdMiddleware
from app.limiter.middleware import RateLimitMiddleware
from app.limiter.redis_store import RedisSlidingWindowStore

setup_logging()
logger = logging.getLogger(__name__)

settings = get_settings()

rate_limit_store = RedisSlidingWindowStore(
    settings.redis_url,
    max_connections=settings.redis_max_connections,
    call_timeout_ms=settings.redis_call_timeout_ms,
    breaker_fail_max=settings.breaker_fail_max,
    breaker_reset_timeout_seconds=settings.breaker_reset_timeout_seconds,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await rate_limit_store.connect()
    logger.info("app_startup env=%s", settings.app_env)
    yield
    await rate_limit_store.close()


app = FastAPI(title="Distributed Rate Limiter", lifespan=lifespan)

app.add_middleware(RateLimitMiddleware, store=rate_limit_store)
app.add_middleware(ReplicaIdMiddleware, replica_id=settings.replica_id)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(search_router)
