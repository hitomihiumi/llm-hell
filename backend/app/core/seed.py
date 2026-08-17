"""Startup seeding.

Mock vLLM endpoints are seeded only for local dev (USE_MOCK_VLLM=true).
There's no first-admin-user bootstrapping here: users are created via
`manage.py` (`create-user`, `set-password`), which talks to the database
directly and doesn't need an existing session to do so.

Source rows ARE seeded unconditionally, because they are configuration
rather than data - the four searchable surfaces are a property of the
application, not of a deployment, and an empty `sources` table would make
the search UI look broken rather than unconfigured. Whether a source can
actually answer is a separate question, settled by `POST
/api/sources/{key}/check`.
"""

import logging

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint
from app.models.source import (
    SOURCE_GITLAB,
    SOURCE_GOOGLE_DRIVE,
    SOURCE_GOOGLE_MAIL,
    SOURCE_POSTGRES_KB,
    Source,
)

logger = logging.getLogger("llmhell.seed")
settings = get_settings()

# key -> (kind, display_name, weight, secret_ref)
#
# Weights are starting points, not measurements. Drive and the KB tables
# hold curated prose, so they tend to answer a question directly; code hits
# and email threads are more often supporting evidence.
_DEFAULT_SOURCES: dict[str, tuple[str, str, float, str | None]] = {
    SOURCE_GOOGLE_DRIVE: ("google_workspace", "Google Drive", 1.0, "google_client_secret"),
    SOURCE_GOOGLE_MAIL: ("google_workspace", "Gmail", 0.8, "google_client_secret"),
    SOURCE_GITLAB: ("gitlab", "GitLab", 1.0, "gitlab_personal_access_token"),
    SOURCE_POSTGRES_KB: ("postgres", "Knowledge base (Postgres)", 1.0, "kb_database_uri"),
}


async def run_seed() -> None:
    async with SessionLocal() as db:
        if settings.use_mock_vllm:
            await _seed_mock_endpoints(db)
        await _seed_sources(db)
        await db.commit()


def _is_configured(key: str) -> bool:
    """Whether this source has the credentials it needs to answer at all.

    A source seeded enabled but unreachable is not merely useless, it is a
    tax on the ones that work: the fan-out waits for the slowest source, and
    a DNS failure against a container that was never started costs several
    seconds on EVERY search. So an unconfigured source starts disabled and
    the setup docs say to switch it on.
    """
    if key in (SOURCE_GOOGLE_DRIVE, SOURCE_GOOGLE_MAIL):
        return bool(settings.google_client_id and settings.google_client_secret)
    if key == SOURCE_GITLAB:
        return bool(settings.gitlab_personal_access_token)
    if key == SOURCE_POSTGRES_KB:
        return bool(settings.kb_search_tables)
    return True


async def _seed_sources(db) -> None:
    """Insert any missing default source. Existing rows are left alone -
    an operator who disabled a source or retuned its weight must not have
    that reverted by a restart."""
    existing = set((await db.execute(select(Source.key))).scalars().all())
    added = [
        Source(
            key=key,
            kind=kind,
            display_name=display_name,
            weight=weight,
            secret_ref=secret_ref,
            enabled=_is_configured(key),
            config={},
        )
        for key, (kind, display_name, weight, secret_ref) in _DEFAULT_SOURCES.items()
        if key not in existing
    ]
    if added:
        db.add_all(added)
        for source in added:
            logger.info(
                "Seeded source %s (%s)",
                source.key,
                "enabled" if source.enabled else "disabled - no credentials configured",
            )


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
