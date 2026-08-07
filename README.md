# LLM-Hell

An OpenAI-compatible metrics proxy that sits between opencode (run by ~20
testers on their own machines, against their own real repos) and a pair
of GLM 4.7 / GLM 4.7 Flash vLLM endpoints on RunPod. Testers point their
`opencode.json` at this service instead of RunPod directly; the agent
loop, tool-calling, and file editing all happen inside opencode, entirely
out of this service's view. What this service does:

- authenticates each tester by a personal API key,
- routes a requested `model` id to the right RunPod endpoint,
- forwards the request byte-for-byte (streaming or not) so nothing about
  opencode's expectations of the response shape gets lost in translation,
- and records technical metrics (TTFT, tokens, cost, tool-call counts,
  errors) into Postgres and Prometheus for the Grafana dashboards.

See `docker-compose.yml` for the service layout: `api` (FastAPI, the
proxy itself), `postgres`, `prometheus`, `grafana`, and an optional
`mock-vllm` service for local development without a GPU.

## Local development (no GPU required)

```bash
cp .env.example .env
docker compose --profile dev up --build
docker compose exec api alembic upgrade head
```

This starts the whole stack plus `mock-vllm`, an OpenAI/vLLM-compatible
stub (see `tools/mock_vllm.py`) that gets seeded as the backing endpoint
for both `glm-4.7*` model ids when `USE_MOCK_VLLM=true` in `.env`. It
emulates streaming, reasoning output (both `reasoning_content` and inline
`<think>` tag styles), tool calls, and a usage-bearing final SSE chunk, so
the proxy's passthrough and metrics recording can be exercised end to end
without a real endpoint.

- API: http://localhost:8000 (`GET /api/health`, `GET /v1/models`, `POST
  /v1/chat/completions`, `GET /metrics`)
- Grafana: http://localhost:3000
- Prometheus: http://localhost:9090

There's no web admin panel - create the first tester and hand them a key
via the management CLI:

```bash
docker compose exec api python manage.py create-user alice
docker compose exec api python manage.py issue-key alice --name laptop
```

`issue-key` prints the raw key exactly once; put it straight into the
tester's `opencode.json`. See `manage.py --help` (and each subcommand's
`--help`) for the rest: `revoke-key`, `list-keys`, `add-endpoint`,
`update-endpoint`, `list-endpoints`, `probe-endpoint`.

## Tester setup (opencode)

Add a custom provider to `opencode.json` (project-local or
`~/.config/opencode/opencode.json`):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "llmhell": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "LLM-Hell",
      "options": {
        "baseURL": "http://<host>:8000/v1",
        "apiKey": "{env:LLMHELL_API_KEY}"
      },
      "models": {
        "deepseek-v4-flash": { "name": "DeepSeek V4 Flash" },
        "deepseek-v4-flash-medium": { "name": "DeepSeek V4 Flash (reasoning: medium)" },
        "deepseek-v4-flash-high": { "name": "DeepSeek V4 Flash (reasoning: high)" }
      }
    }
  },
  "model": "llmhell/deepseek-v4-flash"
}
```

`GET /v1/models` reports exactly which model ids are currently valid for
a given key - it's driven by whatever endpoints `add-endpoint` has
registered and their `reasoning_profile`, not a fixed list, so check it if
a model id 404s.

### Context usage and auto-compaction

opencode's context-fill indicator and its auto-compaction both come from
`limit.context` **in opencode's own config** - it never asks the server how
big the window is. So the number has to be written down client-side, and it
has to match the `--max-model-len` vLLM was started with. A mismatch is
silent in both directions: too low and opencode compacts long before it
needs to, too high and it overruns the server's window mid-session.

Rather than copying numbers by hand, generate the block from the endpoints
that are actually registered:

```bash
docker compose exec api python manage.py opencode-config --base-url http://<host>:8000/v1
```

That emits every published model id (one per reasoning level) with its
`limit.context` taken from the endpoint's `ctx_window`, plus:

```json
"compaction": { "auto": true, "prune": true, "reserved": 8192 }
```

`auto` is opencode's default already - it is stated explicitly so the
behaviour is visibly intended. `prune` drops old tool outputs first, which
in an agentic session are the bulk of the context and the least useful to
keep verbatim. `reserved` is the headroom opencode keeps free to do the
compaction itself.

Write the result to `~/.config/opencode/opencode.json` and **both the CLI
and the desktop app pick it up** - they read the same file. Use
`./opencode.json` instead to scope it to one project.

Keep `ctx_window` in step with the server whenever `--max-model-len`
changes:

```bash
docker compose exec api python manage.py update-endpoint <endpoint_id> --ctx-window 343296
```

### Reasoning levels

opencode has no reasoning-effort setting - it only picks a model. So each
endpoint is published under one model id per configured level: the bare
`model_id` for "off", and `model_id-{level}` for the rest. Picking
`deepseek-v4-flash-high` in opencode makes the proxy send
`reasoning_effort: high` upstream and record `reasoning_level=high` on the
request row, so the Grafana dashboards can break cost and latency down by
level.

The level attached to the model id wins over any `reasoning_effort` a
client sends by hand - otherwise two different published ids could behave
identically and the recorded level would be a lie.

Levels live in the endpoint's `reasoning_profile` JSON, not in code. An
endpoint whose model misbehaves when asked for an effort level can have
its `levels` cleared, which collapses it back to a single bare model id
that sends no reasoning field at all. That is not hypothetical: GLM-4.7
emitted corrupted, looping output whenever `reasoning_effort` was set to
any value, and this is the per-endpoint escape hatch for that.

## Production (RunPod-backed)

```bash
cp .env.example .env
# set USE_MOCK_VLLM=false
docker compose up --build -d
docker compose exec api alembic upgrade head
```

Then register the two real RunPod vLLM endpoints and probe each one
before relying on it - the real reasoning delivery (`reasoning_content`
field vs. inline `<think>` tags) and native tool-call support are
endpoint-specific and unknown until checked:

```bash
docker compose exec api python manage.py add-endpoint \
  --name "GLM 4.7 planner" --base-url https://<runpod-host>/v1 --model-id glm-4.7 --role planner
docker compose exec api python manage.py probe-endpoint <endpoint-id>
```

`probe-endpoint` writes what it found to `model_endpoints.reasoning_profile`
so admins can adjust `tools_mode` there if the probe's reported handling
doesn't match; the proxy already forwards streaming and non-streaming
tool-calling requests as opencode sends them either way.

If RunPod isn't reachable directly (no public port, or you'd rather not
expose vLLM to the internet) and you're tunneling instead, bind the
tunnel on `0.0.0.0`, not the default loopback-only - a port only bound to
127.0.0.1 on this host is invisible from inside the `api` container on
native Linux Docker:

```bash
ssh -L 0.0.0.0:8000:127.0.0.1:8000 -L 0.0.0.0:8002:127.0.0.1:8002 <runpod-ssh-target>
```

then use `host.docker.internal` (routed in via `extra_hosts` in
`docker-compose.yml`) instead of `<runpod-host>` as the endpoint's host:

```bash
docker compose exec api python manage.py add-endpoint \
  --name "GLM 4.7 planner" --base-url http://host.docker.internal:8000/v1 --model-id glm-4.7 --role planner
docker compose exec api python manage.py add-endpoint \
  --name "GLM 4.7 Flash executor" --base-url http://host.docker.internal:8002/v1 --model-id glm-4.7-flash --role executor
```

## Backend development

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate  # .venv/bin/activate on Linux/macOS
pip install -r requirements-dev.txt
pytest
```

Database migrations:

```bash
alembic upgrade head
```

## Project layout

```
backend/
  manage.py       operator CLI: users, API keys, model endpoints, probing
  app/
    api/          FastAPI routers: openai_proxy (/v1/*), metrics (/metrics)
    core/         config, db, security (argon2), api_keys (bearer auth)
    models/       SQLAlchemy: User, ApiKey, ModelEndpoint, LlmRequest
    services/
      llm/        reasoning profiles, tokenizer, model routing, endpoint probe
      stats/      recorder (llm_requests + Prometheus), prom (metric defs)
    alembic/ tests/
tools/mock_vllm.py  local stand-in for the RunPod vLLM endpoints
grafana/ prometheus/  dashboards and scrape config
```

All code, comments, and developer docs are in English.
