from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"

    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 50
    redis_call_timeout_ms: int = 75

    breaker_fail_max: int = 5
    breaker_reset_timeout_seconds: float = 30

    host: str = "0.0.0.0"
    port: int = 8000

    replica_id: str = "unknown"

    # Benchmarking-only kill switch: when false, RateLimitMiddleware isn't
    # added at all, so requests never touch Redis/the breaker. Lets a load
    # test isolate the limiter's actual added latency, rather than the
    # weaker proxy of "how much does an occasional 429 cost."
    rate_limiter_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
