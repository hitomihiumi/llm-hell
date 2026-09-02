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

Confirmation is on by default, and `knowledgeBase.coder.mode` decides what it
covers. The three modes are three different things, which they were not
always - `assisted` used to ask about exactly what `manual` did, and
`autonomous` still stopped before every terminal command, so the mode chosen
precisely to avoid interruption interrupted on the tool an agent reaches for
most:

| mode | asks before |
| --- | --- |
| `manual` | writing a file, running a command |
| `assisted` | running a command |
| `autonomous` | nothing |

`autonomous` is not unguarded: a file path outside the workspace is refused
before approval is even considered. Turn confirmation off entirely with
`knowledgeBase.coder.confirmTools`, knowing what that means.

The loop stops after `knowledgeBase.coder.maxAgentTurns` rounds of tool calls
(30 by default) and says so, rather than going quiet mid-task.

---

## The chat in the sidebar

`@kb` and `@coder` also live in VS Code's own chat panel, and that stays — it
needs no explaining and works the moment the extension is installed. The
**Knowledge Base** container in the Secondary Side Bar holds the other thing: a
chat this extension fully owns, built from scratch rather than borrowed from the
native chat renderer, with **Results** underneath it.

It is a second *renderer* for the identical backend events, not a second
implementation — the same `/api/search/stream` and `/api/chat/completions`
routes, the same tools, the same agent loop. Owning the surface is what buys
the four things the native renderer cannot be asked for.

**A code block is something you can act on.** Copy, insert at the cursor,
create a new file from it, and — for a shell fence only — send it to the
terminal. That last one *types* the command and stops; a chat surface putting
a shell command one click from running is a much larger promise than this one
is making.

**A tool asks in the transcript.** `write_file` and `run_terminal` still
confirm, but the question is a card where the work is, with **Allow**, **Allow
for this chat** and **Cancel**. The native `@coder` participant keeps the
modal, because it has no transcript to draw a card into; `executeTool` takes
the confirmer as an argument, so the two share one implementation and differ
only in how they ask.

**A card stays open when you open it.** The transcript is replaced wholesale
on every published update, and an update arrives per streamed token — so a
tool card expanded while the answer was still being written collapsed again
milliseconds later. `<details>` keeps its open state in the DOM, and the DOM
is what is being thrown away, so the state is harvested out of it immediately
before the replacement and put back after. Harvested rather than recorded from
`toggle` events, because `toggle` fires *asynchronously*: a card opened in the
same tick as an arriving token was still reported shut when the re-render ran.
Scroll position inside a diff or a tool result is kept the same way, and the
renderer's default — reasoning open while it is the only thing happening — is
applied once, so a block you deliberately closed stays closed.

**A write shows what it changed.** "Wrote 1240 bytes to sorting.ts" is the one
summary of a write that cannot be checked, so the card carries a real line
diff instead — `+2 −1` on the folded card, the changed lines with two lines of
context when it is open. It is computed against what was on disk immediately
before the tool ran, so it appears both on the approval card, where it is what
you are actually agreeing to, and on the finished card in every mode —
including `autonomous`, which is the run you most want to be able to read back
afterwards.

The diff is hand-written in `src/diff.ts` rather than pulled in, and it is
syntax-coloured by `src/highlight.ts`, which is lexical and single-line on
purpose: a diff shows one line cut out of its file, so there is no reliable way
to know whether it begins inside a block comment, and a highlighter that
guesses wrong paints half a file as a string.

**Every changed line is shown.** Runs of *unchanged* lines collapse into a gap
— hiding what did not change is not shortening the diff — and the pane
scrolls. The one limit left is a guard rather than a display choice: the LCS
table is quadratic, so a rewrite of more than 5000 changed lines is summarised
instead of compared, and says so. Line endings and a trailing newline are
normalised before anything else, or every file rewritten on the other platform
reports as changed top to bottom.

**The composer says what it is about to send.** The controls live inside the
input box — mode, agent policy, attach, send — and the chips above it show the
active file and selection that `agentContext.ts` has always put in the prompt,
plus any file the user pinned. Only a pinned chip has a remove button: the
other two are what the editor is doing, and not this panel's to take away.

**The filter is by document, not by provider.** "Show me the GitLab ones" is
not a question anyone has; "show me the ones from auth-service", or "just that
README", is. So the list is two levels — repositories and tables at the top,
the files and rows inside them below, both selectable — and a dot marks what
the answer actually cited, with those sorted first. The grouping comes from
the backend's `HitContainer`, never from parsing a repository name back out of
a title: a project path contains slashes of its own, so there is no prefix
rule that separates `group/project` from `group/project/src/main.ts`.

**A turn reads in the order it happened.** A message is a sequence of parts,
not prose with a footnote of tools, so a tool card sits between the sentence
that led to it and the sentence written once it came back. The model's own
thinking - `delta.reasoning`, a separate channel from `delta.content` that the
panel used to drop on the floor - is a folded **Thinking** block, open while
it is the only thing happening and shut once the answer arrives. It is never
sent back as history: reasoning is the model talking to itself, and replaying
it invites the next turn to answer the thinking rather than the question.

**A sentence the model abandoned is marked as abandoned.** The agent sometimes
starts narrating and stops mid-word to emit the tool call — "що виводить
приві", then `write_file`. Measured at the API boundary: every delta before
that point arrives and `finish_reason` is `tool_calls`, so nothing is lost in
transit or in rendering — the model simply stops. Left plain it reads as the
panel eating the end of a word, so a dimmed ellipsis is appended inside the
paragraph, with a tooltip saying where the text went.

Two fixes were measured before settling for marking it. Telling the model to
finish its sentences did nothing: 4 cuts in 5 with the instruction against 3
in 5 without. **Provider routing did.** Every request had been landing on one
OpenRouter provider, and moving off it cut the rate from 10 in 20 to 2 in 15 —
the prompt that truncated deterministically now finishes its sentence. That is
what `AGENT_EXTRA_BODY` in `.env` is for:

```
AGENT_EXTRA_BODY={"provider": {"ignore": ["Relace"]}}
```

It is merged into the upstream request last, so it can carry any routing or
sampling field the provider understands. The marker stays for the cases that
still get through.

**An empty chat says what it is for**, with per-mode suggestions, and finished
answers carry copy and retry on hover. **New Chat** archives the conversation
to a ten-deep ring buffer that **Earlier Conversations** reads back.

Nothing in it is a dependency — the icons are inline SVG rather than a bundled
codicon font. The Markdown it renders — bold, code spans, fenced code blocks,
links, the same `[1]` citation linking the answer document gets — is a small
hand-written renderer in `src/markdown.ts` rather than a bundled library: this
app's answers are prose, citations and the occasional code fence, and a
renderer built for exactly that is auditable in one read. That matters because
it is HTML being set on a page — every dynamic value is escaped before any
Markdown pattern is recognised, which is what the tests in
`test/markdown.test.ts` are really checking: that nothing a model writes can
become a tag, including through a fence's own language tag.

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
| `knowledgeBase.coder.maxAgentTurns` | `30` | Maximum tool rounds for `@coder` and the chat view. |

Needs VS Code 1.100 or newer for the chat API. Without a chat panel the
participant is simply not registered — the sidebar, the commands and the
keybindings still work, and `Ctrl+Alt+K` falls back to the search box.

---

## Development

```bash
pnpm typecheck    # tsc --noEmit
pnpm test         # node --test, no extension host needed
pnpm lint         # biome
pnpm build        # esbuild: dist/extension.js AND dist/webview.js
pnpm watch        # rebuild dist/extension.js on change
pnpm watch:webview # rebuild dist/webview.js on change - run alongside watch
```

The layering exists so the tests can run at all. `format.ts`, `http.ts`,
`sse.ts`, `markdown.ts`, `webviewProtocol.ts`, `toolCallAggregator.ts`,
`agentMode.ts`, `hitGroups.ts`, `diff.ts`, `highlight.ts`, `toolOutput.ts`,
`src/webview/render.ts` and
`client.ts`
import nothing
from `vscode`, so `node --test` loads them directly — 209 tests, including the
client driven against a stub server that enforces the same cookie and CSRF
rules as the real API, the SSE parser fed on chunk boundaries that fall in the
wrong places, and the chat view's own Markdown renderer checked against
prompt-injection-shaped input (`<script>`, an attribute-injection attempt
inside a link URL, a fence whose language tag tries to close the attribute it
lands in) with nothing standing between it and the page but that renderer's
own escaping. `chat.ts`, `chatView.ts`, `documents.ts`, `resultsView.ts`,
`src/webview/main.ts` and `extension.ts` do need the editor's or the browser's
own API and are exercised by running them — the chat view's rendering is
additionally checked by loading the actual bundled `dist/webview.js` in a
browser tab against a stand-in for VS Code's `acquireVsCodeApi`, **at a 300px
sidebar width**, which is what caught the two bugs no string-level test could
have: a `[hidden]` element that stayed visible because a class rule of equal
CSS specificity beat it, and a citation list double-numbered by combining its
own `${n}.` with an `<ol>`'s automatic one.

Two consequences of that split, both deliberate: the vscode-free files declare
and assign their fields instead of using constructor parameter properties, and
they import each other with an explicit `.ts`. Node strips types rather than
compiling them and rejects both shortcuts.

`src/types.ts` mirrors `backend/app/schemas/search.py` by hand, and carries
only the fields this extension reads: a backend that grows a field cannot
break the build, and one that removes a field fails where it is used rather
than at parse time.
