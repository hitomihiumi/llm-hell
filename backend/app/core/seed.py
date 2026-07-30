"""Only seeds the mock vLLM endpoints for local dev (USE_MOCK_VLLM=true) -
there's no first-admin-user bootstrapping here because there's no login to
bootstrap for: users and their API keys are created via `manage.py`
(`create-user`, `issue-key`), which talks to the database directly and
doesn't need an existing admin session to do so.
"""

import logging

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint

logger = logging.getLogger("llmhell.seed")
settings = get_settings()


async def run_seed() -> None:
    if not settings.use_mock_vllm:
        return
    async with SessionLocal() as db:
        await _seed_mock_endpoints(db)
        await db.commit()


async def _seed_mock_endpoints(db) -> None:
    existing = (await db.execute(select(func.count()).select_from(ModelEndpoint))).scalar_one()
    if existing > 0:
        return

    planner = ModelEndpoint(
        name="GLM 4.7 (mock planner)",
        base_url="http://mock-vllm:8000/v1",
        model_id="glm-4.7",
        role="planner",
        ctx_window=131072,
        tools_mode="json_protocol",
        reasoning_profile=DEFAULT_REASONING_PROFILE,
    )
    executor = ModelEndpoint(
        name="GLM 4.7 Flash (mock executor)",
        base_url="http://mock-vllm:8000/v1",
        model_id="glm-4.7-flash",
        role="executor",
        ctx_window=131072,
        tools_mode="json_protocol",
        reasoning_profile=DEFAULT_REASONING_PROFILE,
    )
    db.add_all([planner, executor])
    logger.info("Seeded mock vLLM endpoints for local development")
