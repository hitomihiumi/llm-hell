import pytest

from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint


@pytest.mark.asyncio
async def test_list_active_endpoints_excludes_disabled_and_sensitive_fields(
    logged_in_client, test_db_engine
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        session.add_all(
            [
                ModelEndpoint(
                    name="planner-on",
                    base_url="http://mock-vllm/v1",
                    api_key="secret-key",
                    model_id="glm-4.7",
                    role="planner",
                    ctx_window=32768,
                    enabled=True,
                    reasoning_profile=DEFAULT_REASONING_PROFILE,
                ),
                ModelEndpoint(
                    name="executor-off",
                    base_url="http://mock-vllm/v1",
                    model_id="glm-4.7-flash",
                    role="executor",
                    ctx_window=32768,
                    enabled=False,
                    reasoning_profile=DEFAULT_REASONING_PROFILE,
                ),
            ]
        )
        await session.commit()

    response = await logged_in_client.get("/api/endpoints")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "planner-on"
    assert "api_key" not in body[0]
    assert "base_url" not in body[0]
