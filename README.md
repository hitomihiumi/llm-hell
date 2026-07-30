# LLM-Hell

A test bench for a planner/executor LLM pair (GLM 4.7 as planner, GLM 4.7
Flash as executor) served via vLLM on RunPod. Lets ~20 testers run the pair
against their own real code and real tasks through an agentic loop with
sandboxed execution, and collects both technical and quality metrics in
Grafana.

See `docker-compose.yml` for the full service layout: `web` (React), `api`
(FastAPI), `worker` (agent loop + sandbox orchestration), `postgres`,
`redis`, `prometheus`, `grafana`, and an optional `mock-vllm` service for
local development without a GPU.

## Local development (no GPU required)

```bash
cp .env.example .env
docker compose --profile dev up --build
```

This starts the whole stack plus `mock-vllm`, an OpenAI/vLLM-compatible
stub (see `tools/mock_vllm.py`) that the backend is seeded to point at
when `USE_MOCK_VLLM=true` in `.env`. It emulates streaming, reasoning
output (both `reasoning_content` and inline `<think>` tag styles), and
tool calls, so the agent loop, reasoning parser and context counters can
all be exercised without a real endpoint.

- Frontend: http://localhost:3080
- API docs: http://localhost:8000/docs
- Grafana: http://localhost:3000
- Prometheus: http://localhost:9090

The first admin account is created automatically on boot from
`SEED_ADMIN_USERNAME` / `SEED_ADMIN_PASSWORD` in `.env`. Additional users
join via invite codes generated from the admin panel (`/admin/invites`).

## Production (RunPod-backed)

```bash
cp .env.example .env
# set USE_MOCK_VLLM=false and fill in real credentials
docker compose up --build -d
```

Then, as an admin, add the real RunPod vLLM endpoints under model
endpoints and use "Check endpoint" to probe how that pod actually reports
reasoning (`reasoning_content` field vs inline tags) and whether it
supports native tool calls, before running it against real projects.

Build the sandbox image the worker runs agent commands in (only needs
rebuilding when `runner/Dockerfile` changes):

```bash
docker compose build runner
```

`PROJECTS_HOST_DIR` in `.env` must point at the same directory on the host
that `PROJECTS_DIR` names inside the `api`/`worker` containers - the worker
asks the *host* Docker daemon (over the mounted socket) to bind-mount a
project's workspace into a sandbox container, so that path has to resolve
on the host, not just inside the worker container. The default
(`./data/projects`, a bind mount rather than a named volume) already
satisfies this; only change it if you relocate project storage.

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

## Frontend development

```bash
cd frontend
npm install
npm run dev
```

## Project layout

```
backend/app/
  api/            FastAPI routers
  core/           config, db, security, auth deps, seed data
  models/         SQLAlchemy models
  schemas/        Pydantic schemas
  services/       llm client, context assembly, agent loop, sandbox, projects, stats
  workers/        arq background worker
frontend/src/
  api/ pages/ store/
runner/           sandbox container image (multi-language toolchain)
tools/mock_vllm.py  local stand-in for the RunPod vLLM endpoints
grafana/ prometheus/  dashboards and scrape config
```

Interface copy is in Ukrainian; all code, comments, and developer docs are
in English.
