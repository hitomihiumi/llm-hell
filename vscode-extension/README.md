# Knowledge Base — VS Code extension

`@kb` in the chat panel. Ask across Google Workspace, GitLab and the internal
knowledge base; the answer streams in with the results it read attached, and a
follow-up knows what you were talking about.

```
@kb які розміри у t motor u7?
@kb а вага?
```

It is a thin client on purpose. The backend already plans the queries, fuses
the sources and writes the cited answer; nothing here re-implements any of
that. The editor supplies the two things the web app cannot: a conversation
for the query planner to read, and a real document to open a code hit into.

---

## Why a chat participant and not a panel

The backend plans a search **from the transcript**. Asked "чи присутній тут
гіроскоп" it finds nothing on its own — `тут` lives in the previous turn, and
the datasheet says `IMU: MPU6000`, not "гіроскоп". Given the turn that named
the board, the planner rewrites it as `F722 IMU`, `F722 gyro`, `F722 MPU6000`
and the answer comes back correct.

A sidebar has no conversation to hand it. A chat does, and the editor already
owns the transcript, the follow-up buttons, cancellation and the reference UI.
So `@kb` is a pipe between the two:

| the panel | the backend |
| --- | --- |
| transcript | `history`, which the planner reads |
| references | the hits, listed the way Copilot lists the files it opened |
| streamed Markdown | `token` events, straight through |
| `[1]` links | citations, resolved against the results that were really in the prompt |

Results appear about a second in and the answer is written underneath them,
because the server sends them in that order and nothing here buffers.

`/find` runs the same search with the model left out: the list, fast, nothing
billed.

---

## `@coder` — the agent

`@kb` answers from the corpus. `@coder` acts: it reads and writes files, runs
commands, and searches the knowledge base, looping until the job is done.

```
@coder add a --dry-run flag to the seed script and show me the diff
@coder what did we decide about the landing gear, and does the CAD match?
```

The model is **DeepSeek V4 Flash**, pinned with `AGENT_MODEL_ID`. It is a
different model from the one that writes `@kb`'s answers, and deliberately so:
`@kb` needs a reader, `@coder` needs a model that emits structured
`tool_calls`, and the endpoint is registered with `--tools-mode native`
because DeepSeek speaks the OpenAI tools API rather than the JSON-in-content
fallback.

| tool | |
| --- | --- |
| `read_file`, `list_directory` | capped at 24 000 characters, and the cut says how much is missing |
| `write_file` | asks first |
| `run_terminal` | asks first; killed after two minutes, so a dev server cannot hold the turn open |
| `search_knowledge_base` | the same federated search `@kb` runs, without the answer step |

Every tool runs in the extension host, including the search one. That is not
where the search happens — it reaches the backend from here — but keeping all
four on one side of the wire means the agent loop has one shape rather than
two.

Confirmation is on by default and covers the two tools that change something.
Turn it off with `knowledgeBase.coder.confirmTools` if you would rather not be
asked, knowing what that means.

The loop stops after ten rounds of tool calls and says so, rather than going
quiet mid-task.

---

## Also there

| | |
| --- | --- |
| `Ctrl+Alt+K` | opens the chat carrying the editor selection |
| `Ctrl+Alt+Shift+K` | opens the chat empty |
| **Knowledge Base** sidebar | every result the chat found, still browsable after the answer has scrolled away |
| Clicking a result | a read-only editor tab — a code hit arrives with its language, so find and go-to-line work |
| **Choose Sources** | a checklist, remembered per window, so one project can search GitLab only |

The sidebar is a companion, not the interface. It exists because a transcript
scrolls and results should not have to be found again three turns later.

---

## Setting it up

```bash
pnpm install
pnpm build
```

Then press <kbd>F5</kbd>, or install the folder as a development extension.

Point it at the **backend**, not the web app:

```jsonc
{
  "knowledgeBase.baseUrl": "http://localhost:8000",  // not :3001
  "knowledgeBase.limit": 20
}
```

Run **Knowledge Base: Sign In** once. The password goes into the editor's
secret storage — the OS keychain — and never into a settings file. The
username is written to settings only after the credentials are known to work,
so a typo does not become the saved username.

The state survives a restart: a session lives in memory and a reopened editor
has none, but the credential that establishes one is still in the keychain, so
the first request signs in without asking. Asking while signed out puts a
**Sign in** button in the chat turn.

### Connecting Google and GitLab

Signing in says who you are. It does not say what you may read — Drive belongs
to Google, the repositories to a GitLab instance — so **Knowledge Base:
Connect Accounts** is the second half of it. Connect them and your searches
run as you; connect nothing and they run on the server's own access, which for
a one-person install is exactly right.

GitLab takes a personal access token with `read_api`. It is verified against
the instance before it is stored, so a token with the wrong scopes is refused
here with a reason rather than surfacing next week as a source that returns
nothing.

Google is addressed rather than authorised from here: the Workspace server is
multi-account, and connecting picks which address your searches use. It cannot
run consent itself — that operation opens a browser and waits for a local
callback, which a container has neither of — so an account not yet known to
the server has to be added where a browser exists. The extension shows the
server's own instructions when that is the case.

**No token is ever stored on this side.** The editor's secret storage holds
your knowledge-base password and nothing else; a GitLab token is posted once,
encrypted server-side, and never read back. See `docs/per-user-credentials.md`.

### Why a password and not an API key

The backend has two authentication mechanisms and keeps them deliberately
apart: `/v1/*` takes a bearer API key for the OpenAI-compatible proxy, and
`/api/*` — search, content, sources — takes a cookie session. This extension
needs `/api/*`, so it signs in the way the web app does rather than asking the
backend to grow a third way in. **No server change is needed to run it.**

That costs two things a browser would do by itself, and `src/http.ts` does
both: cookies are kept by hand, because `fetch` in the extension host has no
jar, and the CSRF token is echoed back in a header, because the API's
double-submit check requires it on anything that is not a GET. An expired
session is renewed and the request retried once.

---

## Settings

| setting | default | |
| --- | --- | --- |
| `knowledgeBase.baseUrl` | `http://localhost:8000` | Origin of the API. A bare `localhost:8000` is accepted. |
| `knowledgeBase.username` | — | Filled in by **Sign In**. |
| `knowledgeBase.sources` | `[]` | Source keys to search. Empty means every enabled source. |
| `knowledgeBase.limit` | `20` | Results per search, across all sources. |
| `knowledgeBase.answer` | `true` | Applies to the sidebar search; in chat, use `/find`. |
| `knowledgeBase.searchOnSelection` | `true` | Pre-fill from the editor. |

Needs VS Code 1.100 or newer for the chat API. Without a chat panel the
participant is simply not registered — the sidebar, the commands and the
keybindings still work, and `Ctrl+Alt+K` falls back to the search box.

---

## Development

```bash
pnpm typecheck   # tsc --noEmit
pnpm test        # node --test, no extension host needed
pnpm lint        # biome
pnpm build       # esbuild bundle into dist/
```

The layering exists so the tests can run at all. `format.ts`, `http.ts`,
`sse.ts` and `client.ts` import nothing from `vscode`, so `node --test` loads
them directly — 49 tests, including the client driven against a stub server
that enforces the same cookie and CSRF rules as the real API, and the SSE
parser fed on chunk boundaries that fall in the wrong places. `chat.ts`,
`documents.ts`, `resultsView.ts` and `extension.ts` do need the editor's API
and are exercised by running it.

Two consequences of that split, both deliberate: the vscode-free files declare
and assign their fields instead of using constructor parameter properties, and
they import each other with an explicit `.ts`. Node strips types rather than
compiling them and rejects both shortcuts.

`src/types.ts` mirrors `backend/app/schemas/search.py` by hand, and carries
only the fields this extension reads: a backend that grows a field cannot
break the build, and one that removes a field fails where it is used rather
than at parse time.
