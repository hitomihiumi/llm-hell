import logging

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint
from app.models.user import User

logger = logging.getLogger("llmhell.seed")
settings = get_settings()


async def run_seed() -> None:
    async with SessionLocal() as db:
        await _seed_admin(db)
        if settings.use_mock_vllm:
            await _seed_mock_endpoints(db)
        await db.commit()


async def _seed_admin(db) -> None:
    user_count = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    if user_count > 0:
        return

    admin = User(
        username=settings.seed_admin_username,
        password_hash=hash_password(settings.seed_admin_password),
        role="admin",
    )
    db.add(admin)
    logger.info("Seeded first admin user %r", settings.seed_admin_username)


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
