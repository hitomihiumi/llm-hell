import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin_endpoints import router as admin_endpoints_router
from app.api.admin_invites import router as admin_invites_router
from app.api.auth import router as auth_router
from app.api.endpoints_public import router as endpoints_public_router
from app.api.projects import router as projects_router
from app.api.runs import router as runs_router
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

app.include_router(auth_router)
app.include_router(admin_invites_router)
app.include_router(admin_endpoints_router)
app.include_router(projects_router)
app.include_router(runs_router)
app.include_router(endpoints_public_router)


@app.on_event("startup")
async def on_startup() -> None:
    await run_seed()


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
