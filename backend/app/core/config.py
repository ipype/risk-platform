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

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
