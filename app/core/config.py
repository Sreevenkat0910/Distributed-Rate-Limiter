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


@lru_cache
def get_settings() -> Settings:
    return Settings()
