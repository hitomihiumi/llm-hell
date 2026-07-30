from argparse import Namespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import manage
from app.core.api_keys import authenticate_api_key
from app.models.api_key import ApiKey
from app.models.endpoint import ModelEndpoint
from app.models.user import User
from app.services.llm.probe import EndpointProbeReport, LevelProbeResult


@pytest.fixture(autouse=True)
def _use_test_db(test_db_engine, monkeypatch):
    session_maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(manage, "SessionLocal", session_maker)
    return session_maker


@pytest.mark.asyncio
async def test_create_user_then_duplicate_fails(_use_test_db) -> None:
    await manage.cmd_create_user(Namespace(username="alice", role="admin"))

    async with _use_test_db() as db:
        user = (await db.execute(select(User).where(User.username == "alice"))).scalar_one()
        assert user.role == "admin"

    with pytest.raises(SystemExit):
        await manage.cmd_create_user(Namespace(username="alice", role="user"))


@pytest.mark.asyncio
async def test_issue_key_then_authenticate(_use_test_db, capsys) -> None:
    await manage.cmd_create_user(Namespace(username="bob", role="user"))
    capsys.readouterr()  # discard create-user's own output
    await manage.cmd_issue_key(Namespace(username="bob", name="laptop"))

    printed_lines = capsys.readouterr().out.splitlines()
    raw_key = printed_lines[1]
    assert raw_key.startswith("llmhell_")

    async with _use_test_db() as db:
        result = await authenticate_api_key(db, raw_key)
        assert result is not None
        user, api_key = result
        assert user.username == "bob"
        assert api_key.name == "laptop"


@pytest.mark.asyncio
async def test_issue_key_for_unknown_user_fails(_use_test_db) -> None:
    with pytest.raises(SystemExit):
        await manage.cmd_issue_key(Namespace(username="ghost", name="x"))


@pytest.mark.asyncio
async def test_revoke_key_disables_authentication(_use_test_db, capsys) -> None:
    await manage.cmd_create_user(Namespace(username="carol", role="user"))
    capsys.readouterr()  # discard create-user's own output
    await manage.cmd_issue_key(Namespace(username="carol", name="phone"))
    raw_key = capsys.readouterr().out.splitlines()[1]
    assert raw_key.startswith("llmhell_")

    async with _use_test_db() as db:
        prefix = (await db.execute(select(ApiKey.key_prefix))).scalar_one()
        # Sanity check the key actually works before revocation - otherwise
        # a broken raw_key extraction above would make the post-revoke
        # assertion trivially (and wrongly) pass.
        assert await authenticate_api_key(db, raw_key) is not None

    await manage.cmd_revoke_key(Namespace(key_prefix=prefix))

    async with _use_test_db() as db:
        assert await authenticate_api_key(db, raw_key) is None


@pytest.mark.asyncio
async def test_revoke_unknown_prefix_fails(_use_test_db) -> None:
    with pytest.raises(SystemExit):
        await manage.cmd_revoke_key(Namespace(key_prefix="llmhell_doesnotexist"))


@pytest.mark.asyncio
async def test_list_keys_reports_status(_use_test_db, capsys) -> None:
    await manage.cmd_create_user(Namespace(username="dana", role="user"))
    await manage.cmd_issue_key(Namespace(username="dana", name="ci"))
    capsys.readouterr()

    await manage.cmd_list_keys(Namespace(username=None))
    output = capsys.readouterr().out
    assert "dana" in output
    assert "ci" in output
    assert "active" in output


@pytest.mark.asyncio
async def test_list_keys_includes_users_with_no_keys_issued(_use_test_db, capsys) -> None:
    # A user right after `create-user` but before any `issue-key` must
    # still show up - otherwise there's no way to tell "no key yet" apart
    # from "user doesn't exist" just by looking at `list-keys`.
    await manage.cmd_create_user(Namespace(username="member", role="user"))
    capsys.readouterr()

    await manage.cmd_list_keys(Namespace(username=None))
    output = capsys.readouterr().out
    assert "member" in output
    assert "no keys issued" in output


@pytest.mark.asyncio
async def test_add_endpoint_then_list(_use_test_db, capsys) -> None:
    await manage.cmd_add_endpoint(
        Namespace(
            name="RunPod planner",
            base_url="https://runpod.example/v1",
            model_id="glm-4.7",
            role="planner",
            api_key=None,
            ctx_window=131072,
            price_in=0.5,
            price_out=1.5,
            tools_mode="json_protocol",
            disabled=False,
        )
    )
    capsys.readouterr()

    await manage.cmd_list_endpoints(Namespace())
    output = capsys.readouterr().out
    assert "RunPod planner" in output
    assert "model_id=glm-4.7" in output
    assert "enabled" in output


@pytest.mark.asyncio
async def test_update_endpoint_applies_only_given_fields(_use_test_db) -> None:
    async with _use_test_db() as db:
        endpoint = ModelEndpoint(
            name="original",
            base_url="https://a.example/v1",
            model_id="glm-4.7",
            role="executor",
            ctx_window=32768,
        )
        db.add(endpoint)
        await db.commit()
        await db.refresh(endpoint)
        endpoint_id = endpoint.id

    await manage.cmd_update_endpoint(
        Namespace(
            endpoint_id=endpoint_id,
            name="renamed",
            base_url=None,
            model_id=None,
            role=None,
            api_key=None,
            ctx_window=None,
            price_in=None,
            price_out=None,
            tools_mode=None,
            enable=False,
            disable=True,
        )
    )

    async with _use_test_db() as db:
        endpoint = await db.get(ModelEndpoint, endpoint_id)
        assert endpoint.name == "renamed"
        assert endpoint.base_url == "https://a.example/v1"  # untouched
        assert endpoint.enabled is False


@pytest.mark.asyncio
async def test_update_unknown_endpoint_fails(_use_test_db) -> None:
    with pytest.raises(SystemExit):
        await manage.cmd_update_endpoint(
            Namespace(
                endpoint_id="does-not-exist",
                name=None,
                base_url=None,
                model_id=None,
                role=None,
                api_key=None,
                ctx_window=None,
                price_in=None,
                price_out=None,
                tools_mode=None,
                enable=False,
                disable=False,
            )
        )


@pytest.mark.asyncio
async def test_probe_endpoint_persists_report(_use_test_db, monkeypatch, capsys) -> None:
    async with _use_test_db() as db:
        endpoint = ModelEndpoint(
            name="probe-me", base_url="https://a.example/v1", model_id="glm-4.7", role="executor", ctx_window=32768
        )
        db.add(endpoint)
        await db.commit()
        await db.refresh(endpoint)
        endpoint_id = endpoint.id

    fake_report = EndpointProbeReport(
        models_ok=True,
        models_error=None,
        tokenize_ok=True,
        tokenize_error=None,
        levels=[
            LevelProbeResult(
                level="medium", ok=True, reasoning_content_present=True, inline_tags_present=False, content_sample="ok"
            )
        ],
        native_tools_supported=True,
        native_tools_error=None,
    )

    async def fake_check_endpoint(endpoint):
        return fake_report

    monkeypatch.setattr(manage, "check_endpoint", fake_check_endpoint)

    await manage.cmd_probe_endpoint(Namespace(endpoint_id=endpoint_id))

    output = capsys.readouterr().out
    assert "native tool calls: supported" in output
    assert "reasoning_content field" in output

    async with _use_test_db() as db:
        endpoint = await db.get(ModelEndpoint, endpoint_id)
        assert endpoint.last_checked_at is not None
        assert endpoint.last_check_result["native_tools_supported"] is True
