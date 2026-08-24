import * as vscode from "vscode";
import { ApiError, AuthError, type ChatMessage, type KnowledgeBaseClient } from "./client";
import { applyLanguage, hitUri, type KnowledgeBaseDocuments } from "./documents";
import { collapse } from "./format";
import { ToolCallAggregator } from "./toolCallAggregator";
import { executeTool, TOOLS } from "./tools";
import type { SearchHit } from "./types";
import type {
  ChatMessageView,
  CitationView,
  HostMessage,
  Mode,
  ReferenceView,
  ToolCallView,
  WebviewMessage,
} from "./webviewProtocol";
import { summariseToolArgs } from "./webviewProtocol";

/**
 * A dedicated tab, styled like the editor rather than borrowed from it.
 *
 * `@kb` and `@coder` live in VS Code's own chat panel, which is the right
 * default - it needs no explaining and works the moment the extension is
 * installed. This is the other thing asked for: a surface this extension
 * fully owns, so a tool call can be shown as a labelled card rather than a
 * line of Markdown, and a source can carry its favicon-shaped kind icon
 * rather than borrowing the generic reference chip every other chat
 * participant uses.
 *
 * It is a view onto the same backend the native participants use - the same
 * `/api/search/stream` and `/api/chat/completions` routes, the same tools,
 * the same confirmation before `write_file` or `run_terminal` acts. Nothing
 * here is a second implementation of the search or the agent loop; it is a
 * second *renderer* for the identical events, which is why ToolCallAggregator
 * moved into its own file rather than being copied.
 */

const MAX_AGENT_TURNS = 10;

export class KnowledgeBaseChatPanel {
  private static current: KnowledgeBaseChatPanel | undefined;

  private readonly panel: vscode.WebviewPanel;
  private readonly disposables: vscode.Disposable[] = [];
  private messages: ChatMessageView[] = [];
  private mode: Mode = "kb";
  private abort: AbortController | undefined;
  private sequence = 0;

  static createOrShow(
    context: vscode.ExtensionContext,
    client: KnowledgeBaseClient,
    documents: KnowledgeBaseDocuments,
    settings: () => { sources: string[]; limit: number },
  ): void {
    if (KnowledgeBaseChatPanel.current) {
      KnowledgeBaseChatPanel.current.panel.reveal();
      return;
    }
    const panel = vscode.window.createWebviewPanel(
      "knowledgeBase.chatPanel",
      "Knowledge Base",
      vscode.ViewColumn.Beside,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "dist")],
      },
    );
    panel.iconPath = vscode.Uri.joinPath(context.extensionUri, "media", "icon.svg");
    KnowledgeBaseChatPanel.current = new KnowledgeBaseChatPanel(
      panel,
      context,
      client,
      documents,
      settings,
    );
  }

  private constructor(
    panel: vscode.WebviewPanel,
    context: vscode.ExtensionContext,
    private readonly client: KnowledgeBaseClient,
    private readonly documents: KnowledgeBaseDocuments,
    private readonly settings: () => { sources: string[]; limit: number },
  ) {
    this.panel = panel;
    panel.webview.html = renderHtml(panel.webview, context.extensionUri);

    panel.webview.onDidReceiveMessage(
      (message: WebviewMessage) => void this.handle(message),
      null,
      this.disposables,
    );
    panel.onDidDispose(() => this.dispose(), null, this.disposables);
  }

  private dispose(): void {
    if (KnowledgeBaseChatPanel.current === this) {
      KnowledgeBaseChatPanel.current = undefined;
    }
    this.abort?.abort();
    for (const disposable of this.disposables.splice(0)) disposable.dispose();
  }

  private post(message: HostMessage): void {
    void this.panel.webview.postMessage(message);
  }

  private publish(): void {
    this.post({ type: "messages", messages: this.messages });
  }

  private async handle(message: WebviewMessage): Promise<void> {
    switch (message.type) {
      case "ready": {
        const signedIn = await this.client.hasCredentials();
        this.post({ type: "init", signedIn, mode: this.mode });
        this.publish();
        return;
      }
      case "setMode":
        this.mode = message.mode;
        return;
      case "send":
        this.mode = message.mode;
        await this.send(message.text);
        return;
      case "stop":
        this.abort?.abort();
        return;
      case "signIn":
        await vscode.commands.executeCommand("knowledgeBase.signIn");
        this.post({ type: "signedIn", value: await this.client.hasCredentials() });
        return;
      case "openAccounts":
        await vscode.commands.executeCommand("knowledgeBase.accounts");
        return;
      case "openReference":
        // A permalink is the fast path and the common one - Drive and
        // GitLab hits carry one. A knowledge-base row does not, so that
        // case opens the same read-only document `knowledgeBase.openHit`
        // would, through the client rather than through the command: the
        // command wants a full SearchHit, and a webview reference only ever
        // carries the handful of fields it was rendered from.
        if (message.url) await vscode.env.openExternal(vscode.Uri.parse(message.url));
        else await this.openReferenceDocument(message.hitId);
        return;
      case "openLink":
        await vscode.env.openExternal(vscode.Uri.parse(message.url));
        return;
      case "confirmTool":
        // Reserved: tools currently confirm through VS Code's own modal
        // (see tools.ts), which already blocks the turn until answered. A
        // panel-native confirmation card is the natural next step if that
        // modal turns out to be the wrong place to ask from inside a
        // dedicated tab, but nothing today depends on this message.
        return;
    }
  }

  private async openReferenceDocument(hitId: string): Promise<void> {
    const reference = findReference(this.messages, hitId);
    try {
      const content = await this.client.content(hitId);
      // Only the two fields hitUri/fileName actually read - id and title -
      // are known here, not a full SearchHit. The rest are filled with
      // whatever a hit with nothing to say about them would have.
      const hit: SearchHit = {
        id: hitId,
        source: reference?.source ?? "",
        kind: "unknown",
        title: content.title || reference?.title || hitId,
        snippet: "",
        url: reference?.url ?? null,
        author: null,
        timestamp: null,
        preview_pages: content.preview_pages || null,
        rank_in_source: 0,
        score: 0,
      };
      const uri = hitUri(hit);
      this.documents.set(uri, content.truncated ? `${content.text}\n\n[truncated]` : content.text);
      const document = await vscode.workspace.openTextDocument(uri);
      await applyLanguage(document, content.language);
      await vscode.window.showTextDocument(document, { preview: true });
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        vscode.window.showInformationMessage("This result has nothing more to show.");
      } else {
        vscode.window.showErrorMessage(`Could not open that result: ${(error as Error).message}`);
      }
    }
  }

  private nextId(): string {
    this.sequence += 1;
    return `m${this.sequence}`;
  }

  private async send(text: string): Promise<void> {
    const question = collapse(text);
    if (!question || this.abort) return;

    this.messages.push({
      id: this.nextId(),
      role: "user",
      mode: this.mode,
      text: question,
      status: "done",
    });
    const assistant: ChatMessageView = {
      id: this.nextId(),
      role: "assistant",
      mode: this.mode,
      text: "",
      status: "streaming",
    };
    this.messages.push(assistant);
    this.publish();

    this.abort = new AbortController();
    try {
      if (this.mode === "kb") await this.runSearch(question, assistant, this.abort.signal);
      else await this.runAgent(question, assistant, this.abort.signal);
    } catch (error) {
      if (!this.abort.signal.aborted) {
        assistant.status = "error";
        if (error instanceof AuthError) {
          assistant.error = error.message;
          this.post({ type: "init", signedIn: false, mode: this.mode });
        } else {
          assistant.error = (error as Error).message ?? String(error);
        }
      } else {
        assistant.status = "done";
      }
    } finally {
      this.abort = undefined;
      if (assistant.status === "streaming") assistant.status = "done";
      this.publish();
    }
  }

  // --- @kb: search and cite -------------------------------------------------

  private async runSearch(
    question: string,
    assistant: ChatMessageView,
    signal: AbortSignal,
  ): Promise<void> {
    const config = this.settings();
    const history = historyFromMessages(this.messages.slice(0, -2));

    for await (const event of this.client.searchStream(
      question,
      { sources: config.sources, limit: config.limit, answer: true, history },
      signal,
    )) {
      switch (event.event) {
        case "hits": {
          const payload = event.data as { hits?: RawHit[] };
          assistant.references = (payload.hits ?? []).map(toReference);
          this.publish();
          break;
        }
        case "token": {
          const { text } = event.data as { text?: string };
          if (text) {
            assistant.text += text;
            this.publish();
          }
          break;
        }
        case "citations": {
          const { citations } = event.data as { citations?: RawCitation[] };
          assistant.citations = (citations ?? []).map(toCitation);
          this.publish();
          break;
        }
        case "error": {
          const { message } = event.data as { message?: string };
          assistant.status = "error";
          assistant.error = message ?? "The search failed.";
          break;
        }
      }
    }
  }

  // --- @coder: the tool-calling agent loop -----------------------------------

  private async runAgent(
    question: string,
    assistant: ChatMessageView,
    signal: AbortSignal,
  ): Promise<void> {
    const messages: ChatMessage[] = [
      ...historyFromMessages(this.messages.slice(0, -2)).map((turn) => ({
        role: turn.role,
        content: turn.content,
      })),
      { role: "user", content: question },
    ];

    for (let turn = 0; turn < MAX_AGENT_TURNS; turn++) {
      if (signal.aborted) return;

      // Each round's own narration is a separate thought, cut off wherever
      // the model decided to call a tool rather than at a sentence boundary.
      // Run it straight into the previous round's text and two unrelated
      // sentences read as one - so a fresh paragraph starts here, once, before
      // this round's deltas begin arriving.
      if (turn > 0 && assistant.text && !assistant.text.endsWith("\n")) {
        assistant.text += "\n\n";
      }

      const aggregator = new ToolCallAggregator();
      let content = "";
      for await (const event of this.client.chatCompletionsStream(messages, signal, TOOLS)) {
        if (signal.aborted) return;
        const chunk = event.data as {
          choices?: Array<{ delta?: { content?: string; tool_calls?: unknown[] } }>;
        };
        const delta = chunk.choices?.[0]?.delta;
        if (delta?.content) {
          content += delta.content;
          assistant.text += delta.content;
          this.publish();
        }
        for (const call of delta?.tool_calls ?? []) {
          aggregator.feed(call as Parameters<ToolCallAggregator["feed"]>[0]);
        }
      }

      const calls = aggregator.finalize();
      if (calls.length === 0) return;

      const cards: ToolCallView[] = calls.map((call) => ({
        id: call.id,
        name: call.function.name,
        argsSummary: summariseToolArgs(call.function.name, call.function.arguments),
        status: "running",
      }));
      assistant.toolCalls = [...(assistant.toolCalls ?? []), ...cards];
      this.publish();

      messages.push({ role: "assistant", content, tool_calls: calls as unknown[] });

      for (const call of calls) {
        const output = await executeTool(call, this.client);
        const card = assistant.toolCalls?.find((entry) => entry.id === call.id);
        if (card) {
          card.status = output.startsWith("Cancelled:") ? "cancelled" : "done";
          card.result = output;
          this.publish();
        }
        messages.push({ role: "tool", tool_call_id: call.id, content: output });
      }
    }

    assistant.text +=
      (assistant.text ? "\n\n" : "") +
      `_Stopped after ${MAX_AGENT_TURNS} tool rounds. Ask again to carry on._`;
  }
}

// --- turning stored messages back into history, and raw events into views -----

interface RawHit {
  id: string;
  title: string;
  source: string;
  url: string | null;
}

interface RawCitation {
  n: number;
  title: string;
  url: string | null;
  source: string;
}

function findReference(messages: ChatMessageView[], hitId: string): ReferenceView | undefined {
  for (const message of messages) {
    const found = message.references?.find((reference) => reference.hitId === hitId);
    if (found) return found;
  }
  return undefined;
}

function toReference(hit: RawHit): ReferenceView {
  return { hitId: hit.id, title: hit.title, source: hit.source, url: hit.url };
}

function toCitation(citation: RawCitation): CitationView {
  return { n: citation.n, title: citation.title, url: citation.url, source: citation.source };
}

/**
 * The panel's own transcript, reduced to the `{role, content}` pairs the
 * planner and the agent loop both want. Tool-call and reference detail is
 * for this panel's own rendering and was never part of what either backend
 * call reads back.
 */
function historyFromMessages(
  messages: ChatMessageView[],
): { role: "user" | "assistant"; content: string }[] {
  return messages
    .filter((message) => message.text.trim())
    .map((message) => ({ role: message.role, content: message.text }));
}

// --- the page ----------------------------------------------------------------

function renderHtml(webview: vscode.Webview, extensionUri: vscode.Uri): string {
  const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(extensionUri, "dist", "webview.js"));
  const nonce = randomNonce();
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${webview.cspSource} https:; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Knowledge Base</title>
<style>${STYLES}</style>
</head>
<body>
<div id="root"></div>
<script nonce="${nonce}" src="${scriptUri}"></script>
</body>
</html>`;
}

/**
 * VS Code's own theme variables throughout, never a hard-coded colour - this
 * is what makes the panel look native rather than pasted in, and what keeps
 * it correct in a light theme without a second stylesheet to maintain.
 */
const STYLES = `
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  /* Every hidden element below (the banner, the stop button) also carries
     its own \`display\` in a class rule of equal specificity - author CSS
     always wins over the UA stylesheet's own [hidden]{display:none}, so
     without this override the sign-in banner rendered permanently visible
     regardless of sign-in state. Caught by loading the bundled webview
     script in a browser and watching it, not by anything that reads code. */
  [hidden] { display: none !important; }
  html, body {
    height: 100%;
    margin: 0;
    padding: 0;
    font-family: var(--vscode-font-family);
    font-size: var(--vscode-font-size, 13px);
    color: var(--vscode-editor-foreground);
    background: var(--vscode-editor-background);
  }
  #root { display: flex; flex-direction: column; height: 100vh; }

  .banner {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 12px;
    background: var(--vscode-inputValidation-warningBackground);
    border-bottom: 1px solid var(--vscode-inputValidation-warningBorder, var(--vscode-panel-border));
    font-size: 12px;
  }
  .banner button, .composer-actions button, #accounts-button, .mode-button {
    font-family: inherit;
    font-size: 12px;
    border: 1px solid var(--vscode-button-border, transparent);
    border-radius: 4px;
    padding: 3px 10px;
    cursor: pointer;
    background: var(--vscode-button-secondaryBackground, transparent);
    color: var(--vscode-button-secondaryForeground, var(--vscode-editor-foreground));
  }
  .banner button:hover, #accounts-button:hover { background: var(--vscode-button-secondaryHoverBackground, var(--vscode-toolbar-hoverBackground)); }

  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    border-bottom: 1px solid var(--vscode-panel-border);
  }
  .mode-toggle { display: flex; gap: 2px; }
  .mode-button.active {
    background: var(--vscode-button-background);
    color: var(--vscode-button-foreground);
    border-color: var(--vscode-button-background);
  }
  #accounts-button { color: var(--vscode-descriptionForeground); }

  .messages {
    flex: 1;
    overflow-y: auto;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 18px;
  }

  .message { max-width: 100%; }
  .message-role {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--vscode-descriptionForeground);
    margin-bottom: 4px;
  }
  .message-user .message-body-plain {
    white-space: pre-wrap;
    border-left: 2px solid var(--vscode-textLink-foreground);
    padding-left: 10px;
    color: var(--vscode-foreground);
    opacity: 0.9;
  }
  .message-body { line-height: 1.5; }
  .message-body p { margin: 0 0 8px; }
  .message-body p:last-child { margin-bottom: 0; }
  .message-body ul { margin: 4px 0 8px; padding-left: 20px; }
  .message-body h1, .message-body h2, .message-body h3, .message-body h4 { margin: 12px 0 6px; }
  .message-body code {
    font-family: var(--vscode-editor-font-family, monospace);
    background: var(--vscode-textCodeBlock-background);
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 0.92em;
  }
  .message-body pre {
    background: var(--vscode-textCodeBlock-background);
    border: 1px solid var(--vscode-panel-border);
    border-radius: 4px;
    padding: 8px 10px;
    overflow-x: auto;
    margin: 6px 0 8px;
  }
  .message-body pre code { background: none; padding: 0; }
  .message-body a { color: var(--vscode-textLink-foreground); }
  .message-body a:hover { color: var(--vscode-textLink-activeForeground); }

  .message-pending { color: var(--vscode-descriptionForeground); letter-spacing: 2px; }
  .message-error {
    color: var(--vscode-errorForeground);
    background: var(--vscode-inputValidation-errorBackground, transparent);
    border-left: 2px solid var(--vscode-errorForeground);
    padding: 4px 8px;
    font-size: 12px;
    margin-top: 6px;
  }
  .cursor {
    display: inline-block;
    width: 6px;
    height: 1em;
    margin-left: 2px;
    background: var(--vscode-editor-foreground);
    vertical-align: text-bottom;
    animation: blink 1s step-start infinite;
  }
  @keyframes blink { 50% { opacity: 0; } }

  .references { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
  .chip {
    font-family: inherit;
    font-size: 11px;
    border: 1px solid var(--vscode-panel-border);
    border-radius: 999px;
    padding: 2px 10px 2px 8px;
    background: var(--vscode-badge-background, transparent);
    color: var(--vscode-badge-foreground, var(--vscode-editor-foreground));
    cursor: pointer;
    max-width: 260px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .chip:hover { border-color: var(--vscode-focusBorder); }
  .chip-source {
    text-transform: uppercase;
    font-size: 9px;
    letter-spacing: 0.04em;
    opacity: 0.7;
    margin-right: 5px;
  }

  .citations { margin: 8px 0 0; padding-left: 4px; list-style: none; font-size: 12px; color: var(--vscode-descriptionForeground); }
  .citations a { color: var(--vscode-textLink-foreground); }
  .citation-source { text-transform: uppercase; font-size: 9px; opacity: 0.7; }

  .tool-calls { display: flex; flex-direction: column; gap: 4px; margin: 6px 0; }
  .tool-call {
    border: 1px solid var(--vscode-panel-border);
    border-radius: 4px;
    font-size: 12px;
  }
  .tool-call summary {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 8px;
    cursor: pointer;
    list-style: none;
  }
  .tool-call summary::-webkit-details-marker { display: none; }
  .tool-icon { opacity: 0.7; }
  .tool-name { font-family: var(--vscode-editor-font-family, monospace); font-weight: 600; }
  .tool-args {
    color: var(--vscode-descriptionForeground);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
  }
  .tool-status { font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--vscode-descriptionForeground); }
  .tool-status-running .tool-status { color: var(--vscode-charts-yellow, var(--vscode-editorWarning-foreground)); }
  .tool-status-done .tool-status { color: var(--vscode-charts-green, var(--vscode-testing-iconPassed, inherit)); }
  .tool-status-cancelled .tool-status { color: var(--vscode-errorForeground); }
  .tool-result {
    margin: 0;
    padding: 8px;
    border-top: 1px solid var(--vscode-panel-border);
    font-family: var(--vscode-editor-font-family, monospace);
    font-size: 11px;
    white-space: pre-wrap;
    max-height: 240px;
    overflow-y: auto;
  }

  .composer {
    border-top: 1px solid var(--vscode-panel-border);
    padding: 10px 12px 12px;
  }
  #input {
    width: 100%;
    resize: vertical;
    font-family: inherit;
    font-size: 13px;
    background: var(--vscode-input-background);
    color: var(--vscode-input-foreground);
    border: 1px solid var(--vscode-input-border, var(--vscode-panel-border));
    border-radius: 4px;
    padding: 8px;
  }
  #input:focus { outline: 1px solid var(--vscode-focusBorder); outline-offset: -1px; }
  #input:disabled { opacity: 0.6; }
  .composer-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }
  #send-button {
    background: var(--vscode-button-background);
    color: var(--vscode-button-foreground);
  }
  #send-button:hover { background: var(--vscode-button-hoverBackground); }
`;

function randomNonce(): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let nonce = "";
  for (let index = 0; index < 32; index++) {
    nonce += alphabet[Math.floor(Math.random() * alphabet.length)];
  }
  return nonce;
}
