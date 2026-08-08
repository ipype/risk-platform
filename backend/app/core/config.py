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

    # -- the model seam ------------------------------------------------------------
    #: ``anthropic`` or ``fake``. **Deliberately empty by default.** Defaulting to the
    #: fake would let a real deployment fill an inbox with invented proposals that look
    #: exactly like real ones; defaulting to a live provider would let a misconfigured
    #: one start spending on first use. Neither is a default worth having.
    llm_provider: str = ""
    #: The model string is a deployment choice and appears in no other file. Empty by
    #: default for the same reason the provider is: a code constant here would be a fact
    #: about a vendor's catalogue frozen into our source, going stale without anything
    #: failing loudly, and every generation run would then be stamped with a model id
    #: nobody chose.
    llm_model: str = ""
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_api_version: str = "2023-06-01"
    llm_max_output_tokens: int = 4096
    #: Zero, always, for every generator. Not because it makes a run reproducible — it
    #: does not, and the run record says so — but because sampling variety in a stage
    #: whose output a human reviews one row at a time buys nothing and costs the ability
    #: to compare two runs over the same corpus.
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 120.0

    # -- generation runs -----------------------------------------------------------
    #: Run the generator inside the request instead of queueing it. Tests and development
    #: only, same as ``simulation_eager``.
    generation_eager: bool = False
    generation_time_limit_seconds: int = 1800
    generation_require_worker: bool = True
    generation_worker_ping_seconds: float = 1.0
    #: Ceiling on model calls per run. A three-hundred-page corpus is a hundred windows
    #: and a bill nobody approved; the run reports that it stopped short rather than
    #: pretending it read everything.
    generation_max_windows: int = 20
    #: Characters of extract per call. Comfortably inside any current context window —
    #: the binding constraint is attention rather than capacity, and a model asked to find
    #: risks in eighty pages at once finds the same four it would have found in the first
    #: ten.
    generation_window_chars: int = 12_000
    #: How much of one raw response is kept on the run transcript. The transcript is the
    #: audit answer to "what did the model actually say"; the cap is what stops a
    #: pathological response putting a megabyte in a JSON column.
    generation_transcript_chars: int = 20_000

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
