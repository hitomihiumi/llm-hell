import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.metrics import router as metrics_router
from app.api.openai_proxy import router as openai_proxy_router
from app.core.seed import run_seed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("llmhell")

app = FastAPI(title="LLM-Hell")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(openai_proxy_router)
app.include_router(metrics_router)


@app.on_event("startup")
async def on_startup() -> None:
    await run_seed()


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
