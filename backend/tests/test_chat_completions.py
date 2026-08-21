from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app as fastapi_app
from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint
from app.schemas.search import SearchHit
from app.services.llm import coding_provider
from app.services.mcp.registry import McpRegistry, get_mcp_registry
from app.services.search.service import FederatedSearch


async def _seed_endpoint(test_db_engine, **overrides) -> None:
    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        endpoint = ModelEndpoint(
            name='mock-glm',
            base_url='http://mock-vllm/v1',
            model_id='glm-4.7',
            role='executor',
            ctx_window=32768,
            enabled=True,
            reasoning_profile=DEFAULT_REASONING_PROFILE,
            **overrides,
        )
        session.add(endpoint)
        await session.commit()


@pytest.mark.asyncio
async def test_api_chat_completions_non_streaming(session_client, test_db_engine) -> None:
    await _seed_endpoint(test_db_engine)
    fastapi_app.dependency_overrides[get_mcp_registry] = lambda: MagicMock(spec=McpRegistry)

    federated = FederatedSearch(
        query_id='q-1',
        query='hello',
        hits=[SearchHit(id='kb:1', source='postgres_kb', title='doc', snippet='body', score=1.0)],
        source_status=[],
        duration_ms=10,
        queries=['hello'],
    )
    answer = coding_provider.answer_service.AnswerResult(
        text='The answer is 42.',
        model='glm-4.7',
        prompt_tokens=100,
        completion_tokens=10,
    )

    try:
        with (
            patch.object(coding_provider.planner, 'plan_queries', new=AsyncMock(return_value=['hello'])),
            patch.object(coding_provider, 'federated_search', new=AsyncMock(return_value=(federated, MagicMock()))),
            patch.object(coding_provider.answer_service, 'synthesize', new=AsyncMock(return_value=answer)),
        ):
            response = await session_client.post(
                '/api/chat/completions',
                json={
                    'model': 'llmhell/coder',
                    'stream': False,
                    'messages': [{'role': 'user', 'content': 'hello'}],
                },
            )
    finally:
        fastapi_app.dependency_overrides.pop(get_mcp_registry, None)

    assert response.status_code == 200
    body = response.json()
    assert body['model'] == 'llmhell/coder'
    assert body['choices'][0]['message']['content'] == 'The answer is 42.'
    assert body['usage']['prompt_tokens'] == 100
    assert body['usage']['completion_tokens'] == 10


@pytest.mark.asyncio
async def test_api_chat_completions_streaming(session_client, test_db_engine) -> None:
    await _seed_endpoint(test_db_engine)
    fastapi_app.dependency_overrides[get_mcp_registry] = lambda: MagicMock(spec=McpRegistry)

    federated = FederatedSearch(
        query_id='q-2',
        query='hi',
        hits=[],
        source_status=[],
        duration_ms=5,
        queries=['hi'],
    )
    final = coding_provider.answer_service.AnswerResult(
        text='hello world',
        model='glm-4.7',
        prompt_tokens=50,
        completion_tokens=5,
    )

    async def fake_stream(*args, **kwargs):
        yield 'token', {'text': 'hello '}
        yield 'token', {'text': 'world'}
        yield 'done', final

    try:
        with (
            patch.object(coding_provider.planner, 'plan_queries', new=AsyncMock(return_value=['hi'])),
            patch.object(coding_provider, 'federated_search', new=AsyncMock(return_value=(federated, MagicMock()))),
            patch.object(coding_provider.answer_service, 'synthesize_stream', side_effect=fake_stream),
        ):
            async with session_client.stream(
                'POST',
                '/api/chat/completions',
                json={
                    'model': 'llmhell/coder',
                    'stream': True,
                    'messages': [{'role': 'user', 'content': 'hi'}],
                },
            ) as response:
                assert response.status_code == 200
                raw = ''
                async for chunk in response.aiter_text():
                    raw += chunk
    finally:
        fastapi_app.dependency_overrides.pop(get_mcp_registry, None)

    assert 'data: ' in raw
    assert 'content' in raw and 'hello ' in raw
    assert 'content' in raw and 'world' in raw
    assert 'finish_reason' in raw and 'stop' in raw
    assert 'data: [DONE]' in raw


@pytest.mark.asyncio
async def test_api_chat_completions_tool_mode_non_streaming(session_client, test_db_engine) -> None:
    await _seed_endpoint(test_db_engine)

    result = coding_provider.llm_chat.ChatResult(
        content='',
        finish_reason='tool_calls',
        prompt_tokens=50,
        completion_tokens=20,
        tool_calls=[
            {
                'id': 'call_1',
                'type': 'function',
                'function': {
                    'name': 'read_file',
                    'arguments': '{"path":"x"}',
                },
            }
        ],
    )

    with patch.object(coding_provider.llm_chat, 'complete', new=AsyncMock(return_value=result)):
        response = await session_client.post(
            '/api/chat/completions',
            json={
                'model': 'llmhell/coder',
                'stream': False,
                'messages': [{'role': 'user', 'content': 'read x'}],
                'tools': [
                    {
                        'type': 'function',
                        'function': {
                            'name': 'read_file',
                            'description': 'read',
                            'parameters': {
                                'type': 'object',
                                'properties': {'path': {'type': 'string'}},
                                'required': ['path'],
                            },
                        },
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body['choices'][0]['finish_reason'] == 'tool_calls'
    assert body['choices'][0]['message']['tool_calls'][0]['function']['name'] == 'read_file'


@pytest.mark.asyncio
async def test_api_chat_completions_tool_mode_streaming(session_client, test_db_engine) -> None:
    await _seed_endpoint(test_db_engine)

    async def fake_deltas(*args, **kwargs):
        yield {'id': 'c1', 'model': 'glm-4.7', 'choices': [{'delta': {'content': 'Reading '}}]}
        yield {'id': 'c1', 'model': 'glm-4.7', 'choices': [{'delta': {'content': 'file'}}]}
        yield {
            'id': 'c1',
            'model': 'glm-4.7',
            'object': 'chat.completion.chunk',
            'choices': [{'delta': {}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5},
        }

    with patch.object(coding_provider.llm_chat, 'stream_deltas', side_effect=fake_deltas):
        async with session_client.stream(
            'POST',
            '/api/chat/completions',
            json={
                'model': 'llmhell/coder',
                'stream': True,
                'messages': [{'role': 'user', 'content': 'read x'}],
                'tools': [
                    {
                        'type': 'function',
                        'function': {
                            'name': 'read_file',
                            'description': 'read',
                            'parameters': {
                                'type': 'object',
                                'properties': {'path': {'type': 'string'}},
                                'required': ['path'],
                            },
                        },
                    }
                ],
            },
        ) as response:
            assert response.status_code == 200
            raw = ''
            async for chunk in response.aiter_text():
                raw += chunk

    assert 'Reading ' in raw
    assert 'file' in raw
    assert '"model": "llmhell/coder"' in raw
    assert 'data: [DONE]' in raw
