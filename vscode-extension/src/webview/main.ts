import type { ChatMessageView, HostMessage, Mode } from "../webviewProtocol.ts";
import { renderMessage } from "./render.ts";
import { connectVsCodeApi } from "./vscodeApi.ts";

/**
 * The webview's own script - DOM glue only. Every decision about what a
 * message looks like lives in `render.ts`, which is plain enough to test
 * with `node --test`; everything below exists to get that HTML on screen,
 * wire clicks back to the extension host, and keep the composer usable.
 */

const vscode = connectVsCodeApi();

let messages: ChatMessageView[] = [];
let mode: Mode = "kb";
let sending = false;

const root = document.getElementById("root");
if (!root) throw new Error("no #root element in the panel's own HTML");

root.innerHTML = `
  <div class="banner" id="signin-banner" hidden>
    <span>Not signed in.</span>
    <button type="button" id="signin-button">Sign in</button>
  </div>
  <div class="header">
    <div class="mode-toggle" role="group" aria-label="Mode">
      <button type="button" class="mode-button" data-mode="kb">@kb</button>
      <button type="button" class="mode-button" data-mode="coder">@coder</button>
    </div>
    <button type="button" id="accounts-button" title="Connect Google and GitLab">Accounts</button>
  </div>
  <div class="messages" id="messages"></div>
  <div class="composer">
    <textarea id="input" rows="2" placeholder="Ask a question…  (Enter to send, Shift+Enter for a new line)"></textarea>
    <div class="composer-actions">
      <button type="button" id="stop-button" hidden>Stop</button>
      <button type="button" id="send-button">Send</button>
    </div>
  </div>
`;

const messagesEl = requireEl<HTMLDivElement>("messages");
const inputEl = requireEl<HTMLTextAreaElement>("input");
const sendButton = requireEl<HTMLButtonElement>("send-button");
const stopButton = requireEl<HTMLButtonElement>("stop-button");
const banner = requireEl<HTMLDivElement>("signin-banner");
const accountsButton = requireEl<HTMLButtonElement>("accounts-button");

function requireEl<T extends HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing #${id} in the panel's own HTML`);
  return el as T;
}

// --- rendering -------------------------------------------------------------------

function renderMessages(): void {
  // Whether to follow new content to the bottom, decided BEFORE the list is
  // replaced: once new content lands, "am I at the bottom" would already be
  // answering about the new, taller page rather than the one the user was
  // actually looking at.
  const distanceFromBottom =
    messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight;
  const shouldStick = distanceFromBottom < 80;

  messagesEl.innerHTML = messages.map(renderMessage).join("");

  if (shouldStick) {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}

function setMode(next: Mode): void {
  mode = next;
  for (const button of document.querySelectorAll<HTMLButtonElement>(".mode-button")) {
    button.classList.toggle("active", button.dataset.mode === next);
  }
  vscode.post({ type: "setMode", mode: next });
}
setMode(mode);

function setSending(value: boolean): void {
  sending = value;
  sendButton.hidden = value;
  stopButton.hidden = !value;
  inputEl.disabled = value;
}

function setSignedIn(value: boolean): void {
  banner.hidden = value;
}

// --- sending -----------------------------------------------------------------

function send(): void {
  const text = inputEl.value.trim();
  if (!text || sending) return;
  inputEl.value = "";
  setSending(true);
  vscode.post({ type: "send", text, mode });
}

sendButton.addEventListener("click", send);
stopButton.addEventListener("click", () => {
  vscode.post({ type: "stop" });
  setSending(false);
});
inputEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    send();
  }
});

for (const button of document.querySelectorAll<HTMLButtonElement>(".mode-button")) {
  button.addEventListener("click", () => setMode(button.dataset.mode as Mode));
}
requireEl<HTMLButtonElement>("signin-button").addEventListener("click", () => {
  vscode.post({ type: "signIn" });
});
accountsButton.addEventListener("click", () => {
  vscode.post({ type: "openAccounts" });
});

// --- clicks inside the transcript ---------------------------------------------
//
// One delegated listener rather than one per message: the list is replaced
// wholesale on every render, and re-attaching a listener per chip per render
// would leak exactly as many as the transcript is long.

messagesEl.addEventListener("click", (event) => {
  const target = event.target as HTMLElement;

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
      break;
    case "signedIn":
      setSignedIn(message.value);
      break;
    case "messages":
      messages = message.messages;
      // A turn is in flight for as long as the last message is still
      // streaming - this is what un-disables the composer once an answer
      // (or a tool-calling turn) actually finishes, including on the empty
      // "nothing happened yet" state where there is nothing to check.
      setSending(messages.at(-1)?.status === "streaming");
      renderMessages();
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
