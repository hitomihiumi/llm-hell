#!/usr/bin/env python
"""Operator CLI for user/API-key/model-endpoint management.

There's no web admin anymore - opencode talks to `/v1/*` directly, and
the only "admin" surface that's left is provisioning testers and RunPod
endpoints, which this script does against the database directly (it
doesn't need an existing admin session/key to bootstrap the first user).

Run via `docker compose exec api python manage.py <command> ...` against
the deployed stack, or locally with DATABASE_URL pointed at a reachable
Postgres (see .env.example).
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core.api_keys import generate_api_key, hash_api_key
from app.core.db import SessionLocal
from app.models.api_key import ApiKey
from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint
from app.models.user import User
from app.services.llm.model_routing import published_model_ids
from app.services.llm.probe import check_endpoint


async def cmd_create_user(args: argparse.Namespace) -> None:
    async with SessionLocal() as db:
        existing = (
            await db.execute(select(User).where(User.username == args.username))
        ).scalar_one_or_none()
        if existing is not None:
            print(f"user {args.username!r} already exists (id={existing.id})", file=sys.stderr)
            raise SystemExit(1)

        user = User(username=args.username, role=args.role)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        print(f"created user {user.username!r} (id={user.id}, role={user.role})")


async def cmd_issue_key(args: argparse.Namespace) -> None:
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == args.username))).scalar_one_or_none()
        if user is None:
            print(f"no such user: {args.username!r}", file=sys.stderr)
            raise SystemExit(1)

        raw_key, prefix = generate_api_key()
        db.add(ApiKey(user_id=user.id, name=args.name, key_hash=hash_api_key(raw_key), key_prefix=prefix))
        await db.commit()

        print(f"issued key {args.name!r} for {user.username!r}:")
        print(raw_key)
        print("(shown once - store it now, e.g. in opencode.json's options.apiKey)")


async def cmd_revoke_key(args: argparse.Namespace) -> None:
    async with SessionLocal() as db:
        api_key = (
            await db.execute(select(ApiKey).where(ApiKey.key_prefix == args.key_prefix))
        ).scalar_one_or_none()
        if api_key is None:
            print(f"no key with prefix {args.key_prefix!r}", file=sys.stderr)
            raise SystemExit(1)

        api_key.revoked_at = datetime.now(timezone.utc)
        await db.commit()
        print(f"revoked key {api_key.name!r} (prefix={api_key.key_prefix})")


async def cmd_list_keys(args: argparse.Namespace) -> None:
    async with SessionLocal() as db:
        # LEFT JOIN, not an inner join: a user with zero keys issued yet
        # is exactly the kind of thing an operator needs to see here (e.g.
        # "member" right after `create-user` but before `issue-key`), not
        # something that should silently vanish from the listing.
        query = select(User, ApiKey).outerjoin(ApiKey, ApiKey.user_id == User.id)
        if args.username:
            query = query.where(User.username == args.username)
        rows = (await db.execute(query.order_by(User.username, ApiKey.created_at))).all()

        if not rows:
            print("no users found")
            return
        for user, api_key in rows:
            if api_key is None:
                print(f"{user.username:20} {'(no keys issued)':20}")
                continue
            status = "revoked" if api_key.revoked_at else "active"
            last_used = api_key.last_used_at.isoformat() if api_key.last_used_at else "never"
            print(f"{user.username:20} {api_key.name:20} {api_key.key_prefix:20} {status:8} last_used={last_used}")


async def cmd_add_endpoint(args: argparse.Namespace) -> None:
    async with SessionLocal() as db:
        endpoint = ModelEndpoint(
            name=args.name,
            base_url=args.base_url,
            api_key=args.api_key,
            model_id=args.model_id,
            role=args.role,
            ctx_window=args.ctx_window,
            price_per_mtok_in=args.price_in,
            price_per_mtok_out=args.price_out,
            tools_mode=args.tools_mode,
            enabled=not args.disabled,
            reasoning_profile=DEFAULT_REASONING_PROFILE,
        )
        db.add(endpoint)
        await db.commit()
        await db.refresh(endpoint)
        print(f"created endpoint {endpoint.name!r} (id={endpoint.id})")
        print("run `probe-endpoint` against it to find its real reasoning/tool-call behaviour.")


async def cmd_update_endpoint(args: argparse.Namespace) -> None:
    async with SessionLocal() as db:
        endpoint = await db.get(ModelEndpoint, args.endpoint_id)
        if endpoint is None:
            print(f"no such endpoint: {args.endpoint_id!r}", file=sys.stderr)
            raise SystemExit(1)

        for field, value in (
            ("name", args.name),
            ("base_url", args.base_url),
            ("api_key", args.api_key),
            ("model_id", args.model_id),
            ("role", args.role),
            ("ctx_window", args.ctx_window),
            ("price_per_mtok_in", args.price_in),
            ("price_per_mtok_out", args.price_out),
            ("tools_mode", args.tools_mode),
        ):
            if value is not None:
                setattr(endpoint, field, value)

        if args.enable:
            endpoint.enabled = True
        if args.disable:
            endpoint.enabled = False

        if args.reset_reasoning_profile:
            # reasoning_profile is only written at creation time, so an
            # endpoint registered before a change to DEFAULT_REASONING_PROFILE
            # keeps the old shape forever - which shows up as
            # "unknown model: '<model>-high'" when the new levels aren't in
            # its stored profile. This re-stamps it from the current default.
            endpoint.reasoning_profile = dict(DEFAULT_REASONING_PROFILE)
            print("reset reasoning_profile to the current default")
        if args.disable_reasoning_levels:
            # Keep `parse` (how to read reasoning back) but drop `levels`
            # (asking for it), collapsing this endpoint to a single bare
            # model id that sends no reasoning_effort at all.
            profile = dict(endpoint.reasoning_profile or {})
            profile["levels"] = {}
            endpoint.reasoning_profile = profile
            print("cleared reasoning levels - this endpoint now publishes only its bare model id")

        await db.commit()
        print(f"updated endpoint {endpoint.name!r} (id={endpoint.id})")
        if args.reset_reasoning_profile or args.disable_reasoning_levels:
            published = ", ".join(mid for mid, _ in published_model_ids(endpoint))
            print(f"now publishing: {published}")


async def cmd_opencode_config(args: argparse.Namespace) -> None:
    """Emit a ready-to-paste opencode provider block.

    opencode shows a context-fill indicator and decides when to auto-compact
    from `limit.context` in ITS OWN config - it never asks the server. So the
    number has to be written down client-side, and it has to match what vLLM
    actually serves (`--max-model-len`). Getting it wrong is silent in both
    directions: too low and opencode compacts long before it needs to, too
    high and it overflows the server's window mid-session.

    Generating it from the endpoints registered here keeps the two in step
    instead of relying on someone copying numbers by hand.
    """
    async with SessionLocal() as db:
        endpoints = (
            await db.execute(
                select(ModelEndpoint).where(ModelEndpoint.enabled.is_(True)).order_by(ModelEndpoint.name)
            )
        ).scalars().all()

    if not endpoints:
        print("no enabled endpoints - nothing to generate", file=sys.stderr)
        raise SystemExit(1)

    models: dict[str, Any] = {}
    for endpoint in endpoints:
        for model_id, level in published_model_ids(endpoint):
            label = endpoint.name if level == "off" else f"{endpoint.name} (reasoning: {level})"
            models[model_id] = {
                "name": label,
                "limit": {
                    # Both are required by opencode's schema. `context` is the
                    # server's --max-model-len; `output` is carved out of it,
                    # not additional, so it is capped at a quarter of the
                    # window here rather than being a second budget.
                    "context": endpoint.ctx_window,
                    "output": min(args.max_output, endpoint.ctx_window // 4),
                },
            }

    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "llmhell": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "LLM-Hell",
                "options": {"baseURL": args.base_url, "apiKey": "{env:LLMHELL_API_KEY}"},
                "models": models,
            }
        },
        "model": f"llmhell/{next(iter(models))}",
        "compaction": {
            # auto is opencode's default already; stated explicitly so it is
            # obvious the behaviour is intended rather than inherited.
            "auto": True,
            # Drop old tool outputs first - in an agentic session those are
            # the bulk of the context and the least useful to keep verbatim.
            "prune": True,
            "reserved": args.reserved,
        },
    }

    print(json.dumps(config, indent=2, ensure_ascii=False))
    print(
        "\n# Write to ~/.config/opencode/opencode.json for both the CLI and the\n"
        "# desktop app (they read the same file), or ./opencode.json for one project.\n"
        "# limit.context values come from each endpoint's ctx_window - keep those in\n"
        "# sync with the server's --max-model-len.",
        file=sys.stderr,
    )


async def cmd_list_endpoints(args: argparse.Namespace) -> None:
    async with SessionLocal() as db:
        endpoints = (await db.execute(select(ModelEndpoint).order_by(ModelEndpoint.name))).scalars().all()
        if not endpoints:
            print("no endpoints configured")
            return
        for endpoint in endpoints:
            status = "enabled" if endpoint.enabled else "disabled"
            print(
                f"{endpoint.id}  {endpoint.name:24} model_id={endpoint.model_id:24} "
                f"role={endpoint.role:10} {status:8} tools_mode={endpoint.tools_mode}"
            )


async def cmd_probe_endpoint(args: argparse.Namespace) -> None:
    async with SessionLocal() as db:
        endpoint = await db.get(ModelEndpoint, args.endpoint_id)
        if endpoint is None:
            print(f"no such endpoint: {args.endpoint_id!r}", file=sys.stderr)
            raise SystemExit(1)

        report = await check_endpoint(endpoint)

        endpoint.last_checked_at = datetime.now(timezone.utc)
        endpoint.last_check_result = report.to_dict()
        await db.commit()

    print(f"models:   {'ok' if report.models_ok else f'FAILED ({report.models_error})'}")
    print(f"tokenize: {'ok' if report.tokenize_ok else f'FAILED ({report.tokenize_error})'}")
    tool_status = "supported" if report.native_tools_supported else f"not supported ({report.native_tools_error})"
    print(f"native tool calls: {tool_status}")
    for level in report.levels:
        if not level.ok:
            print(f"  {level.level}: FAILED ({level.error})")
            continue
        if level.reasoning_content_present:
            mode = "reasoning_content field"
        elif level.inline_tags_present:
            mode = "inline <think> tags"
        else:
            mode = "no reasoning seen"
        print(f"  {level.level}: {mode} - sample: {level.content_sample[:80]!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="manage.py", description="LLM-Hell operator CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("create-user", help="Create a new tester/admin account")
    p.add_argument("username")
    p.add_argument("--role", choices=["user", "admin"], default="user")
    p.set_defaults(func=cmd_create_user)

    p = subparsers.add_parser("issue-key", help="Issue a new API key for a user")
    p.add_argument("username")
    p.add_argument("--name", default="default")
    p.set_defaults(func=cmd_issue_key)

    p = subparsers.add_parser("revoke-key", help="Revoke an API key by its prefix")
    p.add_argument("key_prefix", help="The prefix printed by `list-keys` (e.g. llmhell_Ab3dEf12)")
    p.set_defaults(func=cmd_revoke_key)

    p = subparsers.add_parser("list-keys", help="List API keys, optionally filtered to one user")
    p.add_argument("username", nargs="?")
    p.set_defaults(func=cmd_list_keys)

    p = subparsers.add_parser("add-endpoint", help="Register a new model endpoint")
    p.add_argument("--name", required=True)
    p.add_argument("--base-url", required=True, help='e.g. "https://<runpod-host>/v1"')
    p.add_argument("--model-id", required=True, help="The model id the upstream vLLM server expects")
    p.add_argument("--role", default="executor", help="Free-form label, e.g. planner/executor/fast")
    p.add_argument("--api-key", default=None, help="Upstream vLLM API key, if it requires one")
    p.add_argument("--ctx-window", type=int, default=32768)
    p.add_argument("--price-in", type=float, default=0.0, help="USD per million prompt tokens")
    p.add_argument("--price-out", type=float, default=0.0, help="USD per million completion tokens")
    p.add_argument("--tools-mode", choices=["native", "json_protocol"], default="json_protocol")
    p.add_argument("--disabled", action="store_true", help="Register but leave disabled")
    p.set_defaults(func=cmd_add_endpoint)

    p = subparsers.add_parser("update-endpoint", help="Update an existing model endpoint")
    p.add_argument("endpoint_id")
    p.add_argument("--name", default=None)
    p.add_argument("--base-url", default=None)
    p.add_argument("--model-id", default=None)
    p.add_argument("--role", default=None)
    p.add_argument("--api-key", default=None)
    p.add_argument("--ctx-window", type=int, default=None)
    p.add_argument("--price-in", type=float, default=None, dest="price_in")
    p.add_argument("--price-out", type=float, default=None, dest="price_out")
    p.add_argument("--tools-mode", choices=["native", "json_protocol"], default=None)
    p.add_argument("--enable", action="store_true")
    p.add_argument("--disable", action="store_true")
    p.add_argument(
        "--reset-reasoning-profile",
        action="store_true",
        help="Re-stamp reasoning_profile from the current default. Needed for endpoints "
        "registered before the reasoning levels existed, which otherwise 404 on "
        "'<model>-high' and friends.",
    )
    p.add_argument(
        "--disable-reasoning-levels",
        action="store_true",
        help="Clear this endpoint's reasoning levels so it publishes only its bare model id "
        "and never sends reasoning_effort (for models that misbehave when asked for one).",
    )
    p.set_defaults(func=cmd_update_endpoint)

    p = subparsers.add_parser("list-endpoints", help="List all configured model endpoints")
    p.set_defaults(func=cmd_list_endpoints)

    p = subparsers.add_parser(
        "opencode-config",
        help="Print an opencode provider config with correct per-model context limits",
    )
    p.add_argument(
        "--base-url",
        default="http://localhost:8000/v1",
        help="What testers' opencode should call - this proxy's address, not the vLLM pod's",
    )
    p.add_argument(
        "--reserved",
        type=int,
        default=8192,
        help="Tokens opencode keeps free as compaction headroom (default: 8192)",
    )
    p.add_argument(
        "--max-output",
        type=int,
        default=65536,
        help="Cap for limit.output; also capped at a quarter of each model's context",
    )
    p.set_defaults(func=cmd_opencode_config)

    p = subparsers.add_parser(
        "probe-endpoint", help="Probe a real vLLM endpoint's reasoning/tool-call behaviour"
    )
    p.add_argument("endpoint_id")
    p.set_defaults(func=cmd_probe_endpoint)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
