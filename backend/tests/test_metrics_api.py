import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.openai_proxy import get_http_client
from app.main import app as fastapi_app
from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint


@pytest.mark.asyncio
async def test_metrics_endpoint_exposes_known_metric_names(api_client) -> None:
    response = await api_client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")

    body = response.text
    for metric_name in (
        "llm_ttft_seconds",
        "llm_request_duration_seconds",
        "llm_output_tps",
        "llm_tokens_total",
        "llm_errors_total",
    ):
        assert metric_name in body, f"{metric_name} missing from /metrics output"


@pytest.mark.asyncio
async def test_metrics_endpoint_reflects_a_proxied_request(
    authed_client, test_db_engine, mock_vllm_http_client
) -> None:
    fastapi_app.dependency_overrides[get_http_client] = lambda: mock_vllm_http_client
    try:
        session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
        async with session_maker() as session:
            session.add(
                ModelEndpoint(
                    name="mock-glm",
                    base_url="http://mock-vllm/v1",
                    model_id="glm-4.7",
                    role="executor",
                    ctx_window=32768,
                    enabled=True,
                    reasoning_profile=DEFAULT_REASONING_PROFILE,
                )
            )
            await session.commit()

        chat_response = await authed_client.post(
            "/v1/chat/completions",
            json={"model": "glm-4.7", "stream": False, "messages": [{"role": "user", "content": "hi"}]},
        )
        assert chat_response.status_code == 200

        metrics_response = await authed_client.get("/metrics")
        body = metrics_response.text
        token_lines = [
            line
            for line in body.splitlines()
            if line.startswith("llm_tokens_total{") and 'model="glm-4.7"' in line and 'kind="prompt"' in line
        ]
        assert token_lines, f"no llm_tokens_total prompt-kind sample for glm-4.7 in:\n{body}"
    finally:
        fastapi_app.dependency_overrides.pop(get_http_client, None)
