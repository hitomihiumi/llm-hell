import { groupReferences, matchesFilter } from "../hitGroups.ts";
import type {
  AgentMode,
  ChatMessageView,
  ContextItemView,
  HostMessage,
  Mode,
} from "../webviewProtocol.ts";
import { renderContextItems, renderMessage, renderWelcome } from "./render.ts";
import { connectVsCodeApi } from "./vscodeApi.ts";

/**
 * The webview's own script - DOM glue only. Every decision about what a
 * message looks like lives in `render.ts`, which is plain enough to test
 * with `node --test`; everything below exists to get that HTML on screen,
 * wire clicks back to the extension host, and keep the composer usable.
 */

const vscode = connectVsCodeApi();

const MAX_INPUT_HEIGHT = 180;

let messages: ChatMessageView[] = [];
let contextItems: ContextItemView[] = [];
let mode: Mode = "kb";
let agentMode: AgentMode = "manual";
let sourceFilter: string | null = null;
let sending = false;

const root = document.getElementById("root");
if (!root) throw new Error("no #root element in the panel's own HTML");

root.innerHTML = `
  <div class="banner" id="signin-banner" hidden>
    <span>Not signed in.</span>
    <button type="button" id="signin-button">Sign in</button>
  </div>
  <div class="messages" id="messages"></div>
  <div class="composer">
    <div class="context-chips" id="context-chips"></div>
    <div class="composer-box">
      <textarea id="input" rows="1" placeholder="Ask a question…"></textarea>
      <div class="composer-toolbar">
        <button type="button" id="attach-button" class="icon-button" title="Add a file to the context" aria-label="Add context">
          <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M8 3.5v9M3.5 8h9" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
        </button>
        <select id="mode-select" class="toolbar-select" title="Which backend answers" aria-label="Mode">
          <option value="kb">@kb</option>
          <option value="coder">@coder</option>
        </select>
        <select id="agent-mode" class="toolbar-select" title="Tool approval policy" aria-label="Agent mode">
          <option value="manual">Manual</option>
          <option value="assisted">Assisted</option>
          <option value="autonomous">Autonomous</option>
        </select>
        <select id="source-filter" class="toolbar-select" title="Show only results from one repository or document" aria-label="Filter by document">
          <option value="">All results</option>
        </select>
        <span class="composer-spacer"></span>
        <button type="button" id="stop-button" class="icon-button icon-button-stop" hidden title="Stop" aria-label="Stop">
          <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><rect x="4" y="4" width="8" height="8" rx="1" fill="currentColor"/></svg>
        </button>
        <button type="button" id="send-button" class="icon-button icon-button-send" title="Send (Enter)" aria-label="Send">
          <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M8 13V3M4 7l4-4 4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
      </div>
    </div>
  </div>
`;

const messagesEl = requireEl<HTMLDivElement>("messages");
const inputEl = requireEl<HTMLTextAreaElement>("input");
const sendButton = requireEl<HTMLButtonElement>("send-button");
const stopButton = requireEl<HTMLButtonElement>("stop-button");
const banner = requireEl<HTMLDivElement>("signin-banner");
const attachButton = requireEl<HTMLButtonElement>("attach-button");
const modeSelectEl = requireEl<HTMLSelectElement>("mode-select");
const agentModeEl = requireEl<HTMLSelectElement>("agent-mode");
const sourceFilterEl = requireEl<HTMLSelectElement>("source-filter");
const contextChipsEl = requireEl<HTMLDivElement>("context-chips");

function requireEl<T extends HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing #${id} in the panel's own HTML`);
  return el as T;
}

// --- rendering -------------------------------------------------------------------

/**
 * What is open and where it is scrolled, so a re-render does not undo it.
 *
 * The transcript is replaced wholesale on every published update, and an
 * update arrives per streamed token - so a tool card expanded while the answer
 * was still being written collapsed again milliseconds later, over and over.
 * `<details>` keeps its open state in the DOM, and the DOM is exactly what is
 * being thrown away.
 *
 * Harvested from the DOM immediately before the replacement rather than
 * recorded from `toggle` events: `toggle` fires asynchronously, so a card
 * opened in the same tick as an arriving token was still reported shut when
 * the re-render ran. Reading the elements is synchronous and cannot miss.
 *
 * The renderer's default - a reasoning block open while it is the only thing
 * happening - is applied once, the first time a key is seen. After that the
 * state belongs to the user, so a block they deliberately closed stays closed
 * even while it is still streaming.
 */
const openState = new Map<string, boolean>();
const scrollState = new Map<string, number>();

function harvestOpenAndScroll(): void {
  for (const element of messagesEl.querySelectorAll<HTMLDetailsElement>("[data-toggle-key]")) {
    const key = element.dataset.toggleKey;
    if (key) openState.set(key, element.open);
  }
  for (const element of messagesEl.querySelectorAll<HTMLElement>("[data-scroll-key]")) {
    const key = element.dataset.scrollKey;
    if (key && element.scrollTop > 0) scrollState.set(key, element.scrollTop);
  }
}

function restoreOpenAndScroll(): void {
  for (const element of messagesEl.querySelectorAll<HTMLDetailsElement>("[data-toggle-key]")) {
    const key = element.dataset.toggleKey ?? "";
    const known = openState.get(key);
    if (known === undefined) {
      // First sight: the renderer's default stands, and is recorded so that
      // the next render treats it as state rather than applying it again.
      openState.set(key, element.open);
    } else {
      element.open = known;
    }
  }
  for (const element of messagesEl.querySelectorAll<HTMLElement>("[data-scroll-key]")) {
    const wanted = scrollState.get(element.dataset.scrollKey ?? "");
    if (wanted !== undefined) element.scrollTop = wanted;
  }
}

/**
 * Open or shut one card, now, rather than letting the browser do it later.
 *
 * `<details>` toggles as the *default action of a click*, and a click needs
 * its element to survive from mousedown to mouseup. During streaming it does
 * not: a token lands in between, the whole transcript is replaced, and the
 * node under the cursor no longer exists - so the browser has nothing to
 * dispatch the click to and the card never moves. Which is exactly when
 * somebody wants to open one.
 *
 * So the toggle is done on `mousedown` instead - a single event, with no
 * second half to lose - and written to both the element and `openState`, the
 * two places the next render reads.
 */
function toggleCard(summary: HTMLElement): void {
  const details = summary.closest<HTMLDetailsElement>("[data-toggle-key]");
  const key = details?.dataset.toggleKey;
  if (!details || !key) return;
  const next = !details.open;
  details.open = next;
  openState.set(key, next);
}

function renderMessages(): void {
  if (!messages.length) {
    messagesEl.innerHTML = renderWelcome(mode);
    return;
  }

  // Whether to follow new content to the bottom, decided BEFORE the list is
  // replaced: once new content lands, "am I at the bottom" would already be
  // answering about the new, taller page rather than the one the user was
  // actually looking at.
  const distanceFromBottom =
    messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight;
  const shouldStick = distanceFromBottom < 80;

  // Before the DOM goes away, not after: this is the only moment the user's
  // current open/scroll state still exists anywhere.
  harvestOpenAndScroll();

  messagesEl.innerHTML = messages
    .map((message) =>
      renderMessage({
        ...message,
        references: message.references?.filter((reference) =>
          matchesFilter(reference, sourceFilter),
        ),
      }),
    )
    .join("");

  // Synchronously, in the same frame the new HTML lands, so nothing is ever
  // painted in the collapsed state.
  restoreOpenAndScroll();

  if (shouldStick) {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}

function setMode(next: Mode): void {
  mode = next;
  modeSelectEl.value = next;
  // Attachments are read into the agent's prompt. A federated search has
  // nowhere to put them, so the button is hidden rather than offered and
  // quietly ignored.
  attachButton.hidden = next !== "coder";
  agentModeEl.hidden = next !== "coder";
  sourceFilterEl.hidden = next !== "kb";
  inputEl.placeholder = next === "kb" ? "Ask a question…" : "Ask the coder to do something…";
  vscode.post({ type: "setMode", mode: next });
  if (!messages.length) renderMessages();
}

function setAgentMode(next: AgentMode): void {
  agentMode = next;
  agentModeEl.value = next;
  vscode.post({ type: "setAgentMode", mode: next });
}

/**
 * Rebuild the filter from the repositories and documents actually in the
 * transcript.
 *
 * Two levels in a flat `<select>` rather than a tree widget: both a container
 * and a document have to be selectable, and `<optgroup>` labels are not - so
 * an optgroup could name a repository and never let you pick it. Indentation
 * carries the nesting instead, which also survives a 300px sidebar, where a
 * real tree would not.
 *
 * A dot marks what the answer actually cited, and those sort first.
 */
function refreshSourceFilter(): void {
  const references = messages.flatMap((message) => message.references ?? []);
  const cited = new Set(
    messages.flatMap((message) =>
      (message.citations ?? []).map((citation) => citation.hitId).filter(Boolean),
    ) as string[],
  );
  const groups = groupReferences(references, cited);

  sourceFilterEl.innerHTML = "";
  sourceFilterEl.append(option("", "All results"));
  for (const group of groups) {
    sourceFilterEl.append(option(group.key, label(group.title, group.cited, 0)));
    if (group.standalone) continue;
    for (const document of group.documents) {
      sourceFilterEl.append(option(document.key, label(document.title, document.cited, 1)));
    }
  }

  // A selection whose group is gone - a new search returned different
  // results - would leave the box showing nothing and every hit filtered
  // out. Fall back to showing everything.
  const keys = new Set([...sourceFilterEl.options].map((entry) => entry.value));
  if (sourceFilter && !keys.has(sourceFilter)) sourceFilter = null;
  sourceFilterEl.value = sourceFilter ?? "";
  sourceFilterEl.hidden = mode !== "kb" || groups.length === 0;
}

function option(value: string, text: string): HTMLOptionElement {
  const element = document.createElement("option");
  element.value = value;
  element.textContent = text;
  return element;
}

function label(title: string, cited: boolean, depth: number): string {
  // Figure space, not a plain space: a select trims leading whitespace, and
  // the indentation is the only thing carrying the nesting.
  return `${"  ".repeat(depth)}${cited ? "● " : ""}${title}`;
}

function setSending(value: boolean): void {
  sending = value;
  sendButton.hidden = value;
  stopButton.hidden = !value;
  inputEl.disabled = value;
}

function setSignedIn(value: boolean): void {
  banner.hidden = value;
}

/**
 * Grow the box with its content, up to a ceiling.
 *
 * `height = "auto"` first is not redundant: `scrollHeight` of an element that
 * is already taller than its content reports the old, larger height, so
 * without the reset the box grows and never shrinks back.
 */
function autoGrow(): void {
  inputEl.style.height = "auto";
  inputEl.style.height = `${Math.min(inputEl.scrollHeight, MAX_INPUT_HEIGHT)}px`;
}

setMode(mode);
setAgentMode(agentMode);
renderMessages();

// --- sending -----------------------------------------------------------------

function send(): void {
  const text = inputEl.value.trim();
  if (!text || sending) return;
  inputEl.value = "";
  autoGrow();
  setSending(true);
  vscode.post({ type: "send", text, mode });
}

sendButton.addEventListener("click", send);
stopButton.addEventListener("click", () => {
  vscode.post({ type: "stop" });
  setSending(false);
});
inputEl.addEventListener("input", autoGrow);
inputEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    send();
  }
});

modeSelectEl.addEventListener("change", () => setMode(modeSelectEl.value as Mode));
agentModeEl.addEventListener("change", () => setAgentMode(agentModeEl.value as AgentMode));
attachButton.addEventListener("click", () => vscode.post({ type: "pickContext" }));
requireEl<HTMLButtonElement>("signin-button").addEventListener("click", () => {
  vscode.post({ type: "signIn" });
});
sourceFilterEl.addEventListener("change", () => {
  sourceFilter = sourceFilterEl.value || null;
  renderMessages();
});

contextChipsEl.addEventListener("click", (event) => {
  const remove = (event.target as HTMLElement).closest<HTMLElement>("[data-remove-context]");
  if (remove) vscode.post({ type: "removeContext", id: remove.dataset.removeContext ?? "" });
});

// --- clicks inside the transcript ---------------------------------------------
//
// One delegated listener rather than one per message: the list is replaced
// wholesale on every render, and re-attaching a listener per chip per render
// would leak exactly as many as the transcript is long.

// Before the click that would never arrive.
messagesEl.addEventListener("mousedown", (event) => {
  const summary = (event.target as HTMLElement).closest<HTMLElement>("summary");
  if (!summary) return;
  toggleCard(summary);
});

// And the click itself, whether or not it arrives.
//
// `<details>` toggles as the default action of **click**, not of mousedown -
// so cancelling it has to happen here. Cancelling the mousedown instead left
// the native toggle to run whenever the node did survive to be clicked (any
// pause between tokens, or after the answer finished), undoing the one above
// and shutting the card the instant it opened.
//
// A keyboard activation - Enter or Space on a focused summary - arrives as a
// click with no pointer behind it and no mousedown before it, so that is the
// one case this still has to do the toggling for.
messagesEl.addEventListener("click", (event) => {
  const summary = (event.target as HTMLElement).closest<HTMLElement>("summary");
  if (!summary) return;
  event.preventDefault();
  if (event.detail !== 0) return;
  toggleCard(summary);
});

messagesEl.addEventListener("click", (event) => {
  const target = event.target as HTMLElement;

  const suggestion = target.closest<HTMLElement>("[data-suggestion]");
  if (suggestion) {
    inputEl.value = suggestion.dataset.suggestion ?? "";
    autoGrow();
    inputEl.focus();
    return;
  }

  const codeAction = target.closest<HTMLElement>("[data-code-action]");
  if (codeAction) {
    const block = codeAction.closest(".code-block");
    // `textContent` hands back the source unescaped, which is why the code
    // is not also carried in a data attribute: one copy, one escaping path.
    const code = block?.querySelector("code")?.textContent ?? "";
    if (!code) return;
    const language = (block as HTMLElement | null)?.dataset.language ?? "";
    switch (codeAction.dataset.codeAction) {
      case "copy":
        vscode.post({ type: "copyCode", code });
        break;
      case "insert":
        vscode.post({ type: "insertCode", code });
        break;
      case "new-file":
        vscode.post({ type: "newFile", code, language });
        break;
      case "terminal":
        vscode.post({ type: "runInTerminal", command: code });
        break;
    }
    return;
  }

  const confirmTool = target.closest<HTMLElement>("[data-confirm-tool]");
  if (confirmTool) {
    vscode.post({
      type: "confirmTool",
      id: confirmTool.dataset.confirmTool ?? "",
      allow: confirmTool.dataset.allow === "true",
      always: confirmTool.dataset.always === "true",
    });
    return;
  }

  const showDiff = target.closest<HTMLElement>("[data-show-diff]");
  if (showDiff) {
    vscode.post({ type: "showDiff", id: showDiff.dataset.showDiff ?? "" });
    return;
  }

  const messageAction = target.closest<HTMLElement>("[data-message-action]");
  if (messageAction) {
    const id = messageAction.closest<HTMLElement>(".message")?.dataset.id ?? "";
    if (messageAction.dataset.messageAction === "copy") {
      vscode.post({ type: "copyMessage", id });
    } else {
      vscode.post({ type: "retry", id });
    }
    return;
  }

  const reference = target.closest<HTMLElement>("[data-open-reference]");
  if (reference) {
    event.preventDefault();
    const url = reference.dataset.url || null;
    vscode.post({ type: "openReference", hitId: reference.dataset.openReference ?? "", url });
    return;
  }

  const anchor = target.closest("a");
  if (anchor?.href) {
    // Every link a webview renders is either an external permalink or a
    // citation resolved to one - never a page this panel should navigate
    // itself to, so the click is handed to the extension host instead of
    // being allowed to run.
    event.preventDefault();
    vscode.post({ type: "openLink", url: anchor.href });
  }
});

// --- the extension host's side of the conversation -----------------------------

vscode.onMessage((message: HostMessage) => {
  switch (message.type) {
    case "init":
      setSignedIn(message.signedIn);
      setMode(message.mode);
      setAgentMode(message.agentMode);
      break;
    case "signedIn":
      setSignedIn(message.value);
      break;
    case "messages":
      messages = message.messages;
      refreshSourceFilter();
      // A turn is in flight for as long as the last message is still
      // streaming - this is what un-disables the composer once an answer
      // (or a tool-calling turn) actually finishes, including on the empty
      // "nothing happened yet" state where there is nothing to check.
      setSending(messages.at(-1)?.status === "streaming");
      renderMessages();
      break;
    case "context":
      contextItems = message.items;
      contextChipsEl.innerHTML = renderContextItems(contextItems);
      break;
    case "prefill":
      inputEl.value = message.text;
      autoGrow();
      inputEl.focus();
      break;
    case "error":
      // A transport-level failure, not a turn-level one - those already
      // render as the message's own error state. This is for the rest:
      // couldn't reach the panel's own backend calls at all.
      messagesEl.insertAdjacentHTML(
        "beforeend",
        `<div class="message-error">${message.text.replace(/[<>&]/g, (char) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" })[char] ?? char)}</div>`,
      );
      break;
  }
});

vscode.post({ type: "ready" });
