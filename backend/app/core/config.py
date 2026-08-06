from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Risk Platform API"
    environment: str = "development"

    database_url: str = "postgresql+asyncpg://risk:risk@localhost:5432/riskdb"
    redis_url: str = "redis://localhost:6379/0"

    backend_cors_origins: str = "http://localhost:5173"

    #: Run the engine inside the request instead of queueing it. Development and tests
    #: only — a real network at ten thousand iterations holds the connection for minutes.
    simulation_eager: bool = False
    #: Celery hard time limit for one run, in seconds.
    simulation_time_limit_seconds: int = 3600
    #: Refuse to queue a run when no worker answers the broker. A run queued into an
    #: empty cluster is indistinguishable from one that is merely slow, and the analyst
    #: finds out by waiting. Turn off only if the control channel is unreliable in a
    #: deployment where the workers themselves are not.
    simulation_require_worker: bool = True
    #: How long to wait for a worker to answer the preflight ping.
    simulation_worker_ping_seconds: float = 1.0

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
