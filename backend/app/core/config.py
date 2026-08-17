"""Every knob in one place, read from the environment (`.env` as fallback).

Two things bite people here and are worth knowing before editing:

1. **pydantic-settings parses `list[str]` from the environment as JSON.**
   `CORS_ORIGINS=http://localhost:3001` raises a validation error at import
   time; it has to be `CORS_ORIGINS=["http://localhost:3001"]`.

2. **`kb_database_uri` is libpq form, `database_url` is SQLAlchemy form.**
   They point at different databases on purpose (see below) *and* at
   different drivers: `database_url` is consumed by SQLAlchemy and needs
   `postgresql+asyncpg://`, while `kb_database_uri` is handed to the
   postgres-mcp sidecar, which is not SQLAlchemy and rejects the `+asyncpg`
   suffix.

`get_settings()` is `@lru_cache`d, so anything that needs different values
in a test must call `get_settings.cache_clear()` - the `settings_override`
fixture in `tests/conftest.py` does exactly that.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Application database (users, sessions, endpoints, telemetry) ---
    database_url: str = "postgresql+asyncpg://llmhell:llmhell@localhost:5432/llmhell"

    use_mock_vllm: bool = True

    # --- Web session auth -------------------------------------------------
    session_cookie_name: str = "kb_session"
    csrf_cookie_name: str = "kb_csrf"
    # False for plain-HTTP local development; must be True behind TLS, or the
    # cookie travels in the clear.
    session_cookie_secure: bool = False
    session_ttl_seconds: int = 60 * 60 * 24 * 7

    # --- CORS -------------------------------------------------------------
    # The frontend normally reaches the API through Next's own rewrite proxy,
    # so the browser sees a single origin and CORS never comes into it. This
    # list exists for the case of a browser hitting :8000 directly.
    #
    # NOTE: the previous value here was effectively `["*"]` with
    # `allow_credentials=True`, which browsers reject outright - a wildcard
    # origin and credentialed requests are mutually exclusive per the CORS
    # spec, so cookie auth could never have worked against it.
    cors_origins: list[str] = ["http://localhost:3001", "http://127.0.0.1:3001"]

    # --- Answer engine (the vLLM endpoint that reads search results) ------
    # Empty means "the first enabled ModelEndpoint"; set it to a
    # ModelEndpoint.model_id to pin one.
    answer_model_id: str = ""
    answer_max_output_tokens: int = 1024
    # Per-hit snippet cap applied BEFORE token counting, so one enormous
    # document cannot eat the whole prompt budget on its own.
    answer_snippet_chars: int = 1200
    # Headroom left free inside ctx_window for the system prompt, the
    # question, and the chat template's own overhead.
    answer_ctx_reserve_tokens: int = 2048
    answer_temperature: float = 0.2

    # --- Search federation ------------------------------------------------
    # Wall clock for one source's entire search(), which may span several
    # tool calls. Must exceed mcp_call_timeout_seconds.
    search_timeout_seconds: float = 20.0
    search_per_source_limit: int = 10
    search_total_limit: int = 40
    # Reciprocal-rank-fusion constant. 60 is the value from the original RRF
    # paper and is deliberately large: it flattens the curve so rank 1 does
    # not dominate rank 3, which matters when fusing sources whose internal
    # relevance scores are not comparable.
    search_rrf_k: int = 60

    # --- MCP transport ----------------------------------------------------
    mcp_call_timeout_seconds: float = 25.0
    mcp_init_timeout_seconds: float = 30.0
    # Gates POST /api/debug/mcp/call, which returns raw MCP payloads. Off by
    # default: those payloads are unfiltered source data.
    enable_mcp_debug: bool = False

    # --- MCP: GitLab ------------------------------------------------------
    gitlab_mcp_url: str = "http://gitlab-mcp:3002/mcp"
    # Shared secret gating the gitlab-mcp endpoint itself, NOT a GitLab
    # credential. The server refuses to serve streamable HTTP with a
    # server-side PAT unless the endpoint is gated, on the reasoning that
    # anything able to reach the port would otherwise inherit the token.
    gitlab_mcp_auth_token: str = ""
    gitlab_personal_access_token: str = ""
    gitlab_api_url: str = "https://gitlab.com/api/v4"
    # Web base for synthesising blob permalinks, since the API url is not it.
    gitlab_web_url: str = "https://gitlab.com"
    # When empty, search the whole instance with `search_code`. That needs
    # GitLab advanced search (Elasticsearch, Premium/Ultimate on
    # self-managed); listing project ids here falls back to per-project
    # search, which works on any tier.
    gitlab_default_project_ids: list[str] = []

    # --- MCP: Postgres ----------------------------------------------------
    postgres_mcp_url: str = "http://postgres-mcp:8000/mcp"
    # libpq form, NOT +asyncpg. Points at the `kb` database as the read-only
    # `kb_ro` role - never at `database_url`'s database. Anything reachable
    # from here is reachable by generated SQL, and the application's own
    # users/sessions/api_keys must not be.
    kb_database_uri: str = ""
    # Whitelist. Generated SQL may only touch these, and GET /api/records
    # resolves table names against this list rather than trusting the URL.
    kb_search_tables: list[str] = []
    kb_row_link_template: str = "/records/{table}/{pk}"

    # --- MCP: Google Workspace --------------------------------------------
    # "http": talk to the google-mcp sidecar, which bridges the stdio server
    # to streamable HTTP. "stdio": spawn the server as a child process of
    # this API process (single worker only - see services/mcp/registry.py).
    google_mcp_mode: str = "http"
    google_mcp_url: str = "http://google-mcp:3003/mcp"
    google_mcp_command: str = "npx"
    google_mcp_args: list[str] = ["-y", "@aaronsb/google-workspace-mcp"]
    google_client_id: str = ""
    google_client_secret: str = ""
    # The account the fat `manage_*` tools route on. The server is
    # multi-account; this picks which one the demo searches.
    google_account_email: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
