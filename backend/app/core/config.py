from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://llmhell:llmhell@localhost:5432/llmhell"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 14
    cookie_secure: bool = False

    seed_admin_username: str = "admin"
    seed_admin_password: str = "change_me"
    seed_admin_invite_code: str = "ADMIN-SEED"

    projects_dir: str = "/data/projects"
    projects_host_dir: str = "./data/projects"
    max_project_files: int = 3000
    max_project_total_bytes: int = 50_000_000
    max_project_file_bytes: int = 1_000_000

    sandbox_image: str = "llmhell-runner:latest"
    sandbox_cpus: float = 2.0
    sandbox_mem_limit: str = "2g"
    sandbox_pids_limit: int = 256
    sandbox_idle_timeout_seconds: int = 600
    sandbox_command_timeout_seconds: int = 120

    max_concurrent_runs: int = 4
    max_iterations_per_run: int = 25
    max_wall_time_seconds: int = 1200
    max_tokens_per_run: int = 400_000

    context_compaction_threshold: float = 0.75

    use_mock_vllm: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
