FROM python:3.12-slim

# docker CLI is not required inside the container: the worker talks to the
# host daemon over the mounted socket via the `docker` Python SDK.
# git is required: project import runs `git init`/`git commit` in each
# workspace so the frontend gets diffs and downloadable patches for free.
# ripgrep backs the agent's `grep` tool, which runs directly against the
# host-mounted workspace (read-only search doesn't need sandbox isolation).
RUN apt-get update && apt-get install -y --no-install-recommends git ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
