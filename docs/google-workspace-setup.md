# Connecting Google Workspace

This is the one source that cannot be set up entirely from the server. The
MCP server needs an OAuth consent that opens a browser, so the tokens are
produced **once on your own machine** and then mounted into the container.

Budget 15–20 minutes, most of it waiting on the Google Cloud console.

---

## Why it works this way

- The MCP server (`@aaronsb/google-workspace-mcp`) speaks **stdio only** — it
  has no HTTP mode. The `google-mcp` container wraps it with `supergateway`
  so the API can reach it over HTTP like the other two sources.
- It **shells out to `gws`**, Google's Workspace CLI, which is a separate npm
  package (`@googleworkspace/cli`) and not one of its dependencies. Both are
  installed in the image.
- The consent screen is a browser flow. There is no headless equivalent, so
  step 3 below happens on your workstation.

---

## 1. Create OAuth credentials

1. Open <https://console.cloud.google.com/> and select or create a project.
2. **APIs & Services → Enabled APIs & services → + Enable APIs and services**,
   and enable the APIs for whatever you want searchable. For this demo:
   - Google Drive API
   - Gmail API
3. **APIs & Services → OAuth consent screen**:
   - User type **External** is fine for a personal account; choose
     **Internal** if this is a Workspace organisation and you only need your
     own users.
   - Fill in the app name and your support email.
   - Under **Test users**, add the Google account you are going to search.
     Without this, consent fails with `access_blocked` while the app is
     unpublished.
4. **APIs & Services → Credentials → + Create credentials → OAuth client ID**:
   - Application type: **Desktop app**. This matters — the server expects a
     desktop client, and a "Web application" client will be rejected at
     consent because the redirect URI will not match.
   - Copy the **client ID** and **client secret**.

## 2. Put the credentials in `.env`

```bash
GOOGLE_CLIENT_ID=<your client id>
GOOGLE_CLIENT_SECRET=<your client secret>
# The account whose Drive and Gmail get searched. The server is
# multi-account; this picks which one.
GOOGLE_ACCOUNT_EMAIL=you@example.com
```

`.env` is gitignored. Do not put these in `.env.example`.

## 3. Authenticate on your workstation

Requires **Node.js ≥ 22.12**.

```bash
npm install -g @aaronsb/google-workspace-mcp @googleworkspace/cli
```

Confirm the CLI landed on your PATH — the MCP server's failure mode without
it is an empty result set, which is indistinguishable from "nothing matched":

```bash
gws --version
```

Now run the server and trigger the consent flow. The server speaks MCP over
stdin, so drive it with the inspector rather than by hand:

```bash
GOOGLE_CLIENT_ID=<id> GOOGLE_CLIENT_SECRET=<secret> \
  npx -y @modelcontextprotocol/inspector google-workspace-mcp
```

In the inspector, call the `manage_accounts` tool with:

```json
{ "operation": "authenticate", "email": "you@example.com" }
```

A browser window opens. Grant the requested scopes. The server writes:

| what | where (Linux/macOS) |
| --- | --- |
| account registry | `~/.config/google-workspace-mcp/accounts.json` |
| OAuth tokens | `~/.local/share/google-workspace-mcp/credentials/` |

On Windows these live under `%APPDATA%` and `%LOCALAPPDATA%` respectively.

Verify it worked before moving on — call `manage_drive` with
`{"operation": "search", "query": "test", "email": "you@example.com"}` and
confirm you get files back.

## 4. Copy the tokens into the repo

```bash
mkdir -p secrets/google-workspace/config secrets/google-workspace/share
cp -r ~/.config/google-workspace-mcp/.       secrets/google-workspace/config/
cp -r ~/.local/share/google-workspace-mcp/.  secrets/google-workspace/share/
```

`secrets/` is gitignored. These are live credentials — treat the directory
the way you would treat a private key.

## 5. Start the container

The service is behind a compose profile, because without credentials it
starts fine and then fails every call:

```bash
docker compose --profile google up -d --build google-mcp
```

Check it:

```bash
docker compose logs google-mcp --tail 20
```

## 6. Confirm from the application

```bash
docker compose exec api python manage.py list-sources
```

then, logged in as an admin in the web UI, open **Sources** and press
**Check** on Google Drive. It should report reachable with a tool list
including `manage_drive` and `manage_email`.

Or probe it directly and capture the response shape:

```bash
python tools/mcp_probe.py --url http://localhost:3103/mcp \
  call manage_drive '{"operation":"search","query":"onboarding"}' \
  --save backend/tests/fixtures/mcp/google_drive_search.json
```

**Please do this last step.** The Drive and Gmail result adapters in
`backend/app/services/mcp/google.py` were written without a live server to
probe — unlike the GitLab and Postgres ones — so their field names come from
the underlying REST APIs rather than from an observed response. Saving a real
response as a fixture is what turns them from educated guesses into
tested code. If `manage_drive` returns results but the UI shows none, the
source badge will say *"results were returned but none could be mapped"*,
which is that guess being wrong.

---

## Troubleshooting

**`access_blocked` during consent.** The OAuth app is unpublished and your
account is not in **Test users**. Add it in the consent screen settings.

**`gws: not found` in the container logs.** The `@googleworkspace/cli`
postinstall failed to fetch its binary at build time. Rebuild with
`docker compose build --no-cache google-mcp` and watch for a download error;
the image has a build-time check that should catch this.

**Searches work for a while and then start failing.** The token store is
mounted read-write for a reason — the server refreshes tokens and rewrites
those files. Check the mounts in `docker-compose.yml` are not `:ro`.

**Do not run the API with multiple workers in `stdio` mode.**
`GOOGLE_MCP_MODE=stdio` makes the API spawn the server as a child process;
N workers means N processes refreshing and rewriting the same token file,
which can race and invalidate the refresh token. The default `http` mode has
no such limit. The API logs a warning if it detects `WEB_CONCURRENCY`.
