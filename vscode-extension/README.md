# Knowledge Base — VS Code extension

Federated search over Google Workspace, GitLab and the internal knowledge
base, without leaving the editor. Answers come back cited, and a result opens
as a real document rather than a card.

It is a thin client on purpose. The backend already plans the queries, fuses
the sources and writes the answer; nothing here re-implements any of that.
What the editor adds is the part a browser is bad at: searching for what is
already under the cursor, and reading a code hit with syntax highlighting,
find, and copy that produces the file.

---

## What it does

| | |
| --- | --- |
| **Search** (`Knowledge Base: Search`) | A box, pre-filled with the selection or the symbol under the cursor. |
| **Search for Selection** (`Ctrl+Alt+K`) | Skips the box. Select `RRF_K`, press the key, read the answer. |
| **Results** | A sidebar grouped by source, with each source's timing and — when it failed — why. |
| **Open a result** | A read-only editor tab. Code hits get their language, spreadsheets keep their rows. |
| **Answer** | A Markdown tab whose `[1]` citations are links to the results they came from. |
| **Choose Sources** | A checklist, remembered per window, so one project can search GitLab only. |

Results are grouped by source rather than shown in fused rank, which is the
opposite of what the web app does. A sidebar is narrow: knowing *where* a
result lives is what tells you whether to open it. The fused rank survives
inside each group.

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
  "knowledgeBase.limit": 20,
  "knowledgeBase.answer": true
}
```

Run **Knowledge Base: Sign In** once. The password goes into the editor's
secret storage — the OS keychain — and never into a settings file. The
username is written to settings only after the credentials are known to work,
so a typo does not become the saved username.

### Why a password and not an API key

The backend has two authentication mechanisms and keeps them deliberately
apart: `/v1/*` takes a bearer API key for the OpenAI-compatible proxy, and
`/api/*` — search, content, sources — takes a cookie session. This extension
needs `/api/*`, so it signs in the way the web app does rather than asking the
backend to grow a third way in. **No server change is needed to run it.**

That costs two things a browser would do by itself, and `src/http.ts` does
both: cookies are kept by hand, because `fetch` in the extension host has no
jar, and the CSRF token is echoed back in a header, because the API's
double-submit check requires it on anything that is not a GET.

Sessions last a week. When one expires the client signs in again and retries
the request once, so an editor left open over a weekend does not answer a
search with "not authenticated".

---

## Settings

| setting | default | |
| --- | --- | --- |
| `knowledgeBase.baseUrl` | `http://localhost:8000` | Origin of the API. A bare `localhost:8000` is accepted. |
| `knowledgeBase.username` | — | Filled in by **Sign In**. |
| `knowledgeBase.sources` | `[]` | Source keys to search. Empty means every enabled source. |
| `knowledgeBase.limit` | `20` | Results per search, across all sources. |
| `knowledgeBase.answer` | `true` | Off makes a search fast and costs no tokens. |
| `knowledgeBase.searchOnSelection` | `true` | Pre-fill from the editor. |

---

## Development

```bash
pnpm typecheck   # tsc --noEmit
pnpm test        # node --test, no extension host needed
pnpm lint        # biome
pnpm build       # esbuild bundle into dist/
```

The layering exists so the tests can run at all. `format.ts`, `http.ts` and
`client.ts` import nothing from `vscode`, so `node --test` loads them
directly — `client.ts` is driven against a stub HTTP server that enforces what
the real API enforces, which is how the cookie and CSRF handling is checked
without an extension host. `documents.ts`, `resultsView.ts` and
`extension.ts` do need the editor's API and are exercised by running it.

Two consequences of that, both deliberate: those three files declare and
assign their fields rather than using constructor parameter properties, and
they import each other with an explicit `.ts`. Node strips types rather than
compiling them, and it rejects both shortcuts.

`src/types.ts` mirrors `backend/app/schemas/search.py` by hand, and carries
only the fields this extension reads: a backend that grows a field cannot
break the build, and one that removes a field fails where it is used rather
than at parse time.
