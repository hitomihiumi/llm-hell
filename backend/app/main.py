import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.content import router as content_router
from app.api.credentials import router as credentials_router
from app.api.debug import router as debug_router
from app.api.metrics import router as metrics_router
from app.api.openai_proxy import close_http_client
from app.api.openai_proxy import router as openai_proxy_router
from app.api.preview import router as preview_router
from app.api.records import router as records_router
from app.api.search import router as search_router
from app.api.sources import router as sources_router
from app.core.config import get_settings
from app.core.seed import run_seed
from app.services.mcp.registry import close_mcp_registry, get_mcp_registry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("llmhell")

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await run_seed()
    # Builds the connectors and, when google_mcp_mode=stdio, starts the
    # supervisor task that owns that subprocess.
    await get_mcp_registry().start()
    try:
        yield
    finally:
        await close_mcp_registry()
        # The proxy's process-wide httpx client was never closed before,
        # which is harmless at process exit but leaks a connection pool per
        # app instance in tests.
        await close_http_client()


app = FastAPI(title="LLM-Hell Knowledge Base", lifespan=lifespan)

# `allow_origins=["*"]` with `allow_credentials=True` is rejected by every
# browser - the spec forbids a wildcard origin on credentialed requests - so
# the previous configuration could never have carried a session cookie. The
# frontend normally reaches the API through Next's rewrite proxy and is
# same-origin, but a developer hitting :8000 from a browser tab needs this.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(search_router)
app.include_router(sources_router)
app.include_router(content_router)
app.include_router(credentials_router)
app.include_router(preview_router)
app.include_router(records_router)
app.include_router(debug_router)
app.include_router(openai_proxy_router)
app.include_router(metrics_router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
