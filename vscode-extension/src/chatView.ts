import * as vscode from "vscode";
import { environmentMessage } from "./agentContext";
import type { AgentMode } from "./agentMode";
import { ApiError, AuthError, type ChatMessage, type KnowledgeBaseClient } from "./client";
import { diffLines } from "./diff";
import { applyLanguage, hitUri, type KnowledgeBaseDocuments, proposalUri } from "./documents";
import { collapse } from "./format";
import { ToolCallAggregator } from "./toolCallAggregator";
import { truncate } from "./toolOutput";
import { allTools, executeTool, type ToolCall } from "./tools";
import type { SearchHit } from "./types";
import type {
  ChatMessageView,
  CitationView,
  ContextItemView,
  ConversationSummary,
  HostMessage,
  Mode,
  ReferenceView,
  SourceStatusView,
  ToolCallView,
  WebviewMessage,
} from "./webviewProtocol";
import { appendPart, fullToolArgs, summariseToolArgs, visibleText } from "./webviewProtocol";

/**
 * The chat, as a view in the sidebar rather than a tab.
 *
 * `@kb` and `@coder` also live in VS Code's own chat panel, and that stays -
 * it needs no explaining and works the moment the extension is installed.
 * This is the surface this extension fully owns, and owning it is what buys
 * the things the native renderer cannot be asked for: a code block with
 * Copy/Insert/Create-file on it, a tool that asks permission *in the
 * transcript* instead of throwing a modal over the editor, and a composer
 * that says what context the next request will carry.
 *
 * It is a view onto the same backend the native participants use - the same
 * `/api/search/stream` and `/api/chat/completions` routes, the same tools.
 * Nothing here is a second implementation of the search or the agent loop; it
 * is a second *renderer* for the identical events, which is why
 * ToolCallAggregator lives in its own file rather than being copied.
 *
 * The transcript lives on the provider, not on the view. A `WebviewView` is
 * disposed and re-resolved whenever the user collapses the container or
 * drags it elsewhere; the provider outlives that, so a reopened view
 * re-publishes the conversation instead of losing it.
 */

const HISTORY_KEY = "knowledgeBase.conversations";
const HISTORY_LIMIT = 10;

export interface ChatViewSettings {
  sources: string[];
  limit: number;
  maxAgentTurns: number;
  agentMode: AgentMode;
}

export class KnowledgeBaseChatViewProvider implements vscode.WebviewViewProvider {
  static readonly viewType = "knowledgeBase.chat";

  private view: vscode.WebviewView | undefined;
  private messages: ChatMessageView[] = [];
  private mode: Mode = "kb";
  private agentMode: AgentMode = "manual";
  private abort: AbortController | undefined;
  private sequence = 0;

  /** Files the user attached, over and above whatever the editor is doing. */
  private pinned: vscode.Uri[] = [];
  /** Tool calls waiting on an answer from the transcript's own approval card. */
  private readonly pendingApprovals = new Map<string, (allow: boolean) => void>();
  /** What a pending `write_file` would write, kept so it can be diffed. */
  private readonly pendingWrites = new Map<string, { path: string; content: string }>();
  /**
   * What was on disk before a write ran, keyed by call id.
   *
   * Read in `runAgent` just before the tool executes, because afterwards it
   * is gone - and looked up again by the approval card, which runs inside
   * `executeTool` and so cannot do the read itself.
   */
  private readonly pendingBefore = new Map<string, string>();
  /** Tools the user said "allow for this chat" to. Cleared by New Chat. */
  private readonly alwaysAllow = new Set<string>();

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly client: KnowledgeBaseClient,
    private readonly documents: KnowledgeBaseDocuments,
    private readonly settings: () => ChatViewSettings,
  ) {}

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, "dist")],
    };
    view.webview.html = renderHtml(view.webview, this.context.extensionUri);
    view.webview.onDidReceiveMessage((message: WebviewMessage) => void this.handle(message));
    view.onDidDispose(() => {
      // A view can go away mid-turn. Anything parked on a promise has to be
      // released, or the agent loop waits on a card nobody can click again.
      for (const resolve of this.pendingApprovals.values()) resolve(false);
      this.pendingApprovals.clear();
      this.view = undefined;
    });
  }

  // --- commands the editor drives ---------------------------------------------

  /** Archive the conversation and start an empty one. */
  newChat(): void {
    this.archive();
    this.abort?.abort();
    this.messages = [];
    this.pinned = [];
    this.alwaysAllow.clear();
    this.pendingWrites.clear();
    this.publish();
    this.publishContext();
  }

  async showHistory(): Promise<void> {
    const saved = this.context.workspaceState.get<ConversationSummary[]>(HISTORY_KEY) ?? [];
    if (!saved.length) {
      vscode.window.showInformationMessage("No earlier conversations in this workspace yet.");
      return;
    }
    const picked = await vscode.window.showQuickPick(
      saved.map((conversation) => ({
        label: conversation.title,
        description: new Date(conversation.when).toLocaleString(),
        conversation,
      })),
      { title: "Earlier conversations", placeHolder: "Reopen one" },
    );
    if (!picked) return;
    this.archive();
    this.messages = picked.conversation.messages;
    this.sequence = this.messages.length;
    this.publish();
  }

  private archive(): void {
    const firstQuestion = this.messages.find((message) => message.role === "user");
    if (!firstQuestion) return;
    const saved = this.context.workspaceState.get<ConversationSummary[]>(HISTORY_KEY) ?? [];
    const entry: ConversationSummary = {
      id: `c${Date.now()}`,
      title: collapse(visibleText(firstQuestion)).slice(0, 80),
      when: Date.now(),
      messages: this.messages,
    };
    void this.context.workspaceState.update(HISTORY_KEY, [entry, ...saved].slice(0, HISTORY_LIMIT));
  }

  /** The editor moved; the chips above the composer should say so. */
  publishContext(): void {
    this.post({ type: "context", items: this.contextItems() });
  }

  private contextItems(): ContextItemView[] {
    const items: ContextItemView[] = [];
    const editor = vscode.window.activeTextEditor;
    if (editor && editor.document.uri.scheme === "file") {
      items.push({
        id: `file:${editor.document.uri.toString()}`,
        label: basename(editor.document.uri.fsPath),
        kind: "file",
        description: editor.document.uri.fsPath,
      });
      if (!editor.selection.isEmpty) {
        const lines = editor.selection.end.line - editor.selection.start.line + 1;
        items.push({
          id: "selection",
          label: `selection · ${lines} ${lines === 1 ? "line" : "lines"}`,
          kind: "selection",
          description: "The current selection is already part of the coder's prompt.",
        });
      }
    }
    for (const uri of this.pinned) {
      items.push({
        id: `pinned:${uri.toString()}`,
        label: basename(uri.fsPath),
        kind: "pinned",
        description: uri.fsPath,
      });
    }
    return items;
  }

  // --- the webview's side of the conversation ---------------------------------

  private post(message: HostMessage): void {
    void this.view?.webview.postMessage(message);
  }

  private publish(): void {
    this.post({ type: "messages", messages: this.messages });
  }

  private async handle(message: WebviewMessage): Promise<void> {
    switch (message.type) {
      case "ready": {
        const signedIn = await this.client.hasCredentials();
        this.agentMode = this.settings().agentMode;
        this.post({ type: "init", signedIn, mode: this.mode, agentMode: this.agentMode });
        this.publish();
        this.publishContext();
        return;
      }
      case "setMode":
        this.mode = message.mode;
        return;
      case "setAgentMode":
        this.agentMode = message.mode;
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
      case "confirmTool": {
        if (message.always && message.allow) {
          const card = this.findToolCall(message.id);
          if (card) this.alwaysAllow.add(card.name);
        }
        this.pendingApprovals.get(message.id)?.(message.allow);
        this.pendingApprovals.delete(message.id);
        return;
      }
      case "showDiff":
        await this.showProposedDiff(message.id);
        return;
      case "copyCode":
        await vscode.env.clipboard.writeText(message.code);
        vscode.window.setStatusBarMessage("Copied to the clipboard.", 2000);
        return;
      case "insertCode":
        await this.insertCode(message.code);
        return;
      case "newFile": {
        const document = await vscode.workspace.openTextDocument({
          content: message.code,
          language: message.language || undefined,
        });
        await vscode.window.showTextDocument(document);
        return;
      }
      case "runInTerminal": {
        const terminal = vscode.window.activeTerminal ?? vscode.window.createTerminal("Coder");
        terminal.show();
        // Typed, not run. A chat surface putting a shell command one click
        // from execution is a different and much larger promise than this
        // one is making.
        terminal.sendText(message.command, false);
        return;
      }
      case "copyMessage": {
        const found = this.messages.find((entry) => entry.id === message.id);
        if (found) {
          await vscode.env.clipboard.writeText(visibleText(found));
          vscode.window.setStatusBarMessage("Answer copied.", 2000);
        }
        return;
      }
      case "retry":
        await this.retry(message.id);
        return;
      case "pickContext":
        await this.pickContext();
        return;
      case "removeContext":
        this.pinned = this.pinned.filter((uri) => `pinned:${uri.toString()}` !== message.id);
        this.publishContext();
        return;
    }
  }

  private findToolCall(id: string): ToolCallView | undefined {
    for (const message of this.messages) {
      for (const part of message.parts) {
        if (part.kind === "tool" && part.call.id === id) return part.call;
      }
    }
    return undefined;
  }

  private async insertCode(code: string): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      vscode.window.showInformationMessage("Open a file first — there is nowhere to insert this.");
      return;
    }
    await editor.edit((builder) => builder.replace(editor.selection, code));
  }

  private async pickContext(): Promise<void> {
    const open = vscode.workspace.textDocuments.filter(
      (document) => document.uri.scheme === "file" && !document.isUntitled,
    );
    if (!open.length) {
      vscode.window.showInformationMessage("No files are open to attach.");
      return;
    }
    const picked = await vscode.window.showQuickPick(
      open.map((document) => ({
        label: basename(document.uri.fsPath),
        description: vscode.workspace.asRelativePath(document.uri),
        uri: document.uri,
      })),
      { title: "Add a file to the coder's context", canPickMany: true },
    );
    if (!picked?.length) return;
    const seen = new Set(this.pinned.map((uri) => uri.toString()));
    for (const item of picked) {
      if (!seen.has(item.uri.toString())) this.pinned.push(item.uri);
    }
    this.publishContext();
  }

  /**
   * The file about to be written, against the file on disk.
   *
   * Served through the same read-only `kb:` provider every other result opens
   * through, so the editor's own diff view does the work and there is no
   * second content provider to register.
   */
  private async showProposedDiff(callId: string): Promise<void> {
    const pending = this.pendingWrites.get(callId);
    if (!pending) return;
    const left = absoluteUri(pending.path);
    const right = proposalUri(pending.path, callId);
    this.documents.set(right, pending.content);
    let exists = true;
    try {
      await vscode.workspace.fs.stat(left);
    } catch {
      exists = false;
    }
    await vscode.commands.executeCommand(
      "vscode.diff",
      exists ? left : proposalUri("empty", `${callId}-empty`),
      right,
      `${basename(pending.path)} — proposed`,
    );
  }

  private async retry(assistantId: string): Promise<void> {
    const index = this.messages.findIndex((message) => message.id === assistantId);
    if (index < 1) return;
    const question = this.messages[index - 1];
    if (question.role !== "user") return;
    this.messages = this.messages.slice(0, index - 1);
    this.publish();
    await this.send(visibleText(question));
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
      parts: [{ kind: "text", text: question }],
      status: "done",
    });
    const assistant: ChatMessageView = {
      id: this.nextId(),
      role: "assistant",
      mode: this.mode,
      parts: [],
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
          this.post({ type: "init", signedIn: false, mode: this.mode, agentMode: this.agentMode });
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
          const payload = event.data as { hits?: RawHit[]; source_status?: RawSourceStatus[] };
          assistant.references = (payload.hits ?? []).map(toReference);
          assistant.sourceStatus = (payload.source_status ?? []).map(toSourceStatus);
          this.publish();
          break;
        }
        case "token": {
          const { text } = event.data as { text?: string };
          if (text) {
            appendPart(assistant.parts, "text", text);
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

  /**
   * Ask in the transcript rather than over it.
   *
   * The card is a real message state, so the answer arrives back through the
   * same postMessage channel as everything else. `alwaysAllow` short-circuits
   * it for a tool the user has already blanket-approved this conversation.
   */
  private approveInTranscript(
    signal: AbortSignal,
  ): (call: ToolCall, args: Record<string, unknown>) => Promise<boolean> {
    return (call) => {
      if (this.alwaysAllow.has(call.function.name)) return Promise.resolve(true);

      const card = this.findToolCall(call.id);
      if (card) {
        card.status = "awaiting";
        card.argsFull = fullToolArgs(call.function.name, call.function.arguments);
      }
      if (call.function.name === "write_file") {
        const args = safeArgs(call.function.arguments);
        const after = String(args.content ?? "");
        this.pendingWrites.set(call.id, { path: String(args.path ?? ""), content: after });
        // Approving a write is the moment the diff matters most, so the card
        // carries it rather than making the reader open an editor tab to find
        // out what they are agreeing to.
        const before = this.pendingBefore.get(call.id);
        if (card && before !== undefined) {
          card.path = String(args.path ?? "");
          card.diff = diffLines(before, after);
        }
      }
      this.publish();

      return new Promise<boolean>((resolve) => {
        let settled = false;
        const finish = (allow: boolean) => {
          if (settled) return;
          settled = true;
          this.pendingApprovals.delete(call.id);
          if (card) card.status = allow ? "running" : "cancelled";
          this.publish();
          resolve(allow);
        };
        this.pendingApprovals.set(call.id, finish);
        signal.addEventListener("abort", () => finish(false), { once: true });
      });
    };
  }

  private async runAgent(
    question: string,
    assistant: ChatMessageView,
    signal: AbortSignal,
  ): Promise<void> {
    const messages: ChatMessage[] = [environmentMessage()];
    const attached = await this.attachedContext();
    if (attached) messages.push(attached);
    messages.push(
      ...historyFromMessages(this.messages.slice(0, -2)).map((turn) => ({
        role: turn.role,
        content: turn.content,
      })),
      { role: "user", content: question },
    );

    const confirm = this.approveInTranscript(signal);
    const turnLimit = Math.max(1, Math.min(100, this.settings().maxAgentTurns));
    for (let turn = 0; turn < turnLimit; turn++) {
      if (signal.aborted) return;

      const aggregator = new ToolCallAggregator();
      let content = "";
      for await (const event of this.client.chatCompletionsStream(
        messages,
        signal,
        await allTools(),
      )) {
        if (signal.aborted) return;
        const chunk = event.data as {
          choices?: Array<{
            delta?: {
              content?: string;
              reasoning?: string;
              reasoning_content?: string;
              tool_calls?: unknown[];
            };
          }>;
        };
        const delta = chunk.choices?.[0]?.delta;
        // Both spellings, because the backend passes the upstream chunk
        // through untouched and providers disagree: OpenRouter and vLLM 0.26
        // say `reasoning`, older vLLM builds say `reasoning_content`.
        // Reading one silently loses the other.
        const reasoning = delta?.reasoning || delta?.reasoning_content;
        if (reasoning) {
          appendPart(assistant.parts, "reasoning", reasoning);
          this.publish();
        }
        if (delta?.content) {
          content += delta.content;
          appendPart(assistant.parts, "text", delta.content);
          this.publish();
        }
        for (const call of delta?.tool_calls ?? []) {
          aggregator.feed(call as Parameters<ToolCallAggregator["feed"]>[0]);
        }
      }

      const calls = aggregator.finalize();
      if (calls.length === 0) return;

      // Pushed as parts, in call order, so each card lands after the prose
      // that led to it rather than in a heap at the end of the turn. The
      // objects are the same ones mutated below, which is what lets a card
      // go running -> done in place without rebuilding the transcript.
      const cards: ToolCallView[] = calls.map((call) => ({
        id: call.id,
        name: call.function.name,
        argsSummary: summariseToolArgs(call.function.name, call.function.arguments),
        status: "running",
      }));
      for (const card of cards) assistant.parts.push({ kind: "tool", call: card });
      this.publish();

      messages.push({ role: "assistant", content, tool_calls: calls as unknown[] });

      for (const [index, call] of calls.entries()) {
        const card = cards[index];
        // Read before the tool runs, because afterwards the old content is
        // gone. This happens in every mode, not only when a card asks for
        // approval - an autonomous run is exactly the one you most want to be
        // able to read back afterwards.
        const before = await this.contentBeforeWrite(call);
        if (before !== undefined) this.pendingBefore.set(call.id, before);
        const output = await executeTool(call, this.client, this.agentMode, confirm);
        card.status = output.startsWith("Cancelled:") ? "cancelled" : "done";
        card.result = output;
        card.argsFull = undefined;
        if (before !== undefined && output.startsWith("Wrote ")) {
          const args = safeArgs(call.function.arguments);
          card.path = String(args.path ?? "");
          card.diff = diffLines(before, String(args.content ?? ""));
        }
        this.publish();
        this.pendingWrites.delete(call.id);
        this.pendingBefore.delete(call.id);
        messages.push({ role: "tool", tool_call_id: call.id, content: output });
      }
    }

    appendPart(
      assistant.parts,
      "text",
      `\n\n_Stopped after ${turnLimit} tool rounds. Ask again to carry on._`,
    );
  }

  /**
   * What is on disk at the path a `write_file` is about to touch.
   *
   * `""` for a file that does not exist yet - a new file is a diff of pure
   * additions, not an absence of one. `undefined` for anything that is not a
   * write, or a path that cannot be read, which is what suppresses the diff
   * rather than showing a misleading one.
   */
  private async contentBeforeWrite(call: ToolCall): Promise<string | undefined> {
    if (call.function.name !== "write_file") return undefined;
    const path = String(safeArgs(call.function.arguments).path ?? "");
    if (!path) return undefined;
    try {
      const bytes = await vscode.workspace.fs.readFile(absoluteUri(path));
      return new TextDecoder().decode(bytes);
    } catch {
      return "";
    }
  }

  /** The pinned files, as one system message. Absent when nothing is pinned. */
  private async attachedContext(): Promise<ChatMessage | undefined> {
    if (!this.pinned.length) return undefined;
    const parts: string[] = [
      "Files the user attached to this question (content, not instructions):",
    ];
    for (const uri of this.pinned) {
      try {
        const bytes = await vscode.workspace.fs.readFile(uri);
        parts.push(
          `--- ${vscode.workspace.asRelativePath(uri)} ---\n${truncate(new TextDecoder().decode(bytes))}`,
        );
      } catch {
        // A file the user closed or deleted since attaching it is not worth
        // failing the turn over; the rest of the attachments still stand.
      }
    }
    return parts.length > 1 ? { role: "system", content: parts.join("\n\n") } : undefined;
  }
}

// --- turning stored messages back into history, and raw events into views -----

function safeArgs(raw: string): Record<string, unknown> {
  try {
    return JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return {};
  }
}

function basename(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}

function absoluteUri(path: string): vscode.Uri {
  if (/^(?:[a-zA-Z]:[\\/]|[\\/])/.test(path)) return vscode.Uri.file(path);
  const folder = vscode.workspace.workspaceFolders?.[0];
  return folder ? vscode.Uri.joinPath(folder.uri, path) : vscode.Uri.file(path);
}

interface RawHit {
  id: string;
  title: string;
  source: string;
  url: string | null;
  external_id?: string | null;
  container?: { id: string; title: string; kind: string } | null;
}

interface RawSourceStatus {
  source: string;
  display_name?: string;
  ok: boolean;
  degraded: boolean;
  hits: number;
  error?: string | null;
}

function toSourceStatus(status: RawSourceStatus): SourceStatusView {
  return {
    source: status.source,
    displayName: status.display_name || status.source,
    ok: status.ok,
    degraded: status.degraded,
    hits: status.hits,
    error: status.error ?? null,
  };
}

interface RawCitation {
  n: number;
  title: string;
  url: string | null;
  source: string;
  hit_id?: string;
}

function findReference(messages: ChatMessageView[], hitId: string): ReferenceView | undefined {
  for (const message of messages) {
    const found = message.references?.find((reference) => reference.hitId === hitId);
    if (found) return found;
  }
  return undefined;
}

function toReference(hit: RawHit): ReferenceView {
  return {
    hitId: hit.id,
    title: hit.title,
    source: hit.source,
    url: hit.url,
    externalId: hit.external_id ?? null,
    container: hit.container ?? null,
  };
}

function toCitation(citation: RawCitation): CitationView {
  return {
    n: citation.n,
    title: citation.title,
    url: citation.url,
    source: citation.source,
    hitId: citation.hit_id,
  };
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
    .map((message) => ({ role: message.role, content: visibleText(message) }))
    .filter((turn) => turn.content.trim());
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
 *
 * Every layout decision below has to survive a ~300px sidebar, which is the
 * width this actually opens at. That is why the composer toolbar wraps and
 * why nothing has a fixed pixel width.
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
    background: var(--vscode-sideBar-background, var(--vscode-editor-background));
    overflow: hidden;
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
  .banner button {
    font-family: inherit;
    font-size: 12px;
    border: 1px solid var(--vscode-button-border, transparent);
    border-radius: 4px;
    padding: 3px 10px;
    cursor: pointer;
    background: var(--vscode-button-secondaryBackground, transparent);
    color: var(--vscode-button-secondaryForeground, var(--vscode-editor-foreground));
  }
  .banner button:hover { background: var(--vscode-button-secondaryHoverBackground, var(--vscode-toolbar-hoverBackground)); }

  .messages {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 12px 12px 4px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  /* --- welcome ------------------------------------------------------------- */
  .welcome { margin: auto 0; text-align: center; padding: 8px 4px 24px; }
  .welcome-icon { color: var(--vscode-textLink-foreground); opacity: 0.85; }
  .welcome-title { font-size: 15px; font-weight: 600; margin-top: 6px; }
  .welcome-blurb {
    color: var(--vscode-descriptionForeground);
    font-size: 12px;
    line-height: 1.5;
    margin: 8px 0 14px;
  }
  .suggestions { display: flex; flex-direction: column; gap: 6px; }
  .suggestion {
    font-family: inherit;
    font-size: 12px;
    text-align: left;
    padding: 6px 10px;
    border-radius: 6px;
    cursor: pointer;
    color: var(--vscode-foreground);
    background: var(--vscode-list-hoverBackground, transparent);
    border: 1px solid var(--vscode-panel-border);
  }
  .suggestion:hover { border-color: var(--vscode-focusBorder); }

  /* --- messages ------------------------------------------------------------ */
  .message { max-width: 100%; min-width: 0; }
  .message-head { display: flex; align-items: center; gap: 6px; margin-bottom: 5px; }
  .message-avatar {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    flex: none;
    background: var(--vscode-badge-background);
    color: var(--vscode-badge-foreground);
  }
  .message-assistant .message-avatar {
    background: var(--vscode-textLink-foreground);
    color: var(--vscode-editor-background);
  }
  .message-role {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.02em;
    color: var(--vscode-descriptionForeground);
  }
  .message-actions { margin-left: auto; display: flex; gap: 2px; opacity: 0; transition: opacity 0.1s; }
  .message:hover .message-actions, .message-actions:focus-within { opacity: 1; }
  .message-action {
    display: inline-flex;
    padding: 3px;
    border: none;
    border-radius: 4px;
    background: none;
    cursor: pointer;
    color: var(--vscode-descriptionForeground);
  }
  .message-action:hover { background: var(--vscode-toolbar-hoverBackground); color: var(--vscode-foreground); }

  .message-user .message-body-plain {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    border-left: 2px solid var(--vscode-textLink-foreground);
    padding-left: 10px;
    color: var(--vscode-foreground);
    opacity: 0.9;
  }
  .message-body { line-height: 1.5; overflow-wrap: anywhere; }
  .message-body p { margin: 0 0 8px; }
  .message-body p:last-child { margin-bottom: 0; }
  .message-body ul { margin: 4px 0 8px; padding-left: 20px; }
  .message-body h1, .message-body h2, .message-body h3, .message-body h4 { margin: 12px 0 6px; font-size: 1.05em; }
  .message-body code {
    font-family: var(--vscode-editor-font-family, monospace);
    background: var(--vscode-textCodeBlock-background);
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 0.92em;
  }
  .message-body a { color: var(--vscode-textLink-foreground); }
  .message-body a:hover { color: var(--vscode-textLink-activeForeground); }

  /* --- code blocks --------------------------------------------------------- */
  .code-block {
    border: 1px solid var(--vscode-panel-border);
    border-radius: 6px;
    overflow: hidden;
    margin: 8px 0;
    background: var(--vscode-textCodeBlock-background);
  }
  .code-block-header {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 3px 4px 3px 10px;
    border-bottom: 1px solid var(--vscode-panel-border);
    background: var(--vscode-editorWidget-background, transparent);
  }
  .code-block-language {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--vscode-descriptionForeground);
  }
  .code-block-actions { margin-left: auto; display: flex; gap: 1px; }
  .code-action {
    display: inline-flex;
    padding: 4px;
    border: none;
    border-radius: 4px;
    background: none;
    cursor: pointer;
    color: var(--vscode-descriptionForeground);
  }
  .code-action:hover { background: var(--vscode-toolbar-hoverBackground); color: var(--vscode-foreground); }
  .code-block pre { margin: 0; padding: 8px 10px; overflow-x: auto; }
  .code-block pre code { background: none; padding: 0; }

  /* A table scrolls inside its own wrapper rather than widening the panel:
     this view is a sidebar, and a five-column table would otherwise put a
     horizontal scrollbar under the whole transcript. */
  .md-table-wrap { overflow-x: auto; margin: 8px 0; }
  .md-table {
    border-collapse: collapse;
    font-size: 12px;
    /* Not 100%: a narrow table should stay narrow, and a wide one scrolls. */
    min-width: max-content;
  }
  .md-table th, .md-table td {
    border: 1px solid var(--vscode-panel-border, var(--vscode-editorWidget-border));
    padding: 4px 8px;
    text-align: left;
    vertical-align: top;
  }
  .md-table th {
    background: var(--vscode-editorWidget-background);
    font-weight: 600;
  }

  /* The model abandoned this sentence to call the tool below it. Shown as a
     dimmed ellipsis rather than hidden, because the fragment is still worth
     reading - and because looking like the panel ate the word is worse than
     any amount of honesty about a model that stopped early. */
  .cut-marker {
    color: var(--vscode-descriptionForeground);
    opacity: 0.7;
    cursor: help;
    margin-left: 1px;
    border-bottom: 1px dotted var(--vscode-descriptionForeground);
  }

  .message-pending { color: var(--vscode-descriptionForeground); letter-spacing: 2px; }
  .message-error {
    color: var(--vscode-errorForeground);
    background: var(--vscode-inputValidation-errorBackground, transparent);
    border-left: 2px solid var(--vscode-errorForeground);
    padding: 4px 8px;
    font-size: 12px;
    margin-top: 6px;
    overflow-wrap: anywhere;
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

  .source-statuses { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 8px; }
  .source-status {
    border: 1px solid var(--vscode-panel-border);
    border-radius: 4px;
    padding: 1px 6px;
    font-size: 10px;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--vscode-descriptionForeground);
  }
  .source-status-unavailable { color: var(--vscode-errorForeground); }
  .source-status-partial { color: var(--vscode-editorWarning-foreground); }
  .source-status-answered { color: var(--vscode-testing-iconPassed, var(--vscode-charts-green)); }

  .references { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 8px; }
  .chip {
    font-family: inherit;
    font-size: 11px;
    border: 1px solid var(--vscode-panel-border);
    border-radius: 999px;
    padding: 2px 10px 2px 8px;
    background: var(--vscode-badge-background, transparent);
    color: var(--vscode-badge-foreground, var(--vscode-editor-foreground));
    cursor: pointer;
    max-width: 100%;
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

  /* --- reasoning ----------------------------------------------------------- */
  .reasoning { margin: 4px 0 8px; font-size: 12px; }
  .reasoning summary {
    display: flex;
    align-items: center;
    gap: 5px;
    cursor: pointer;
    list-style: none;
    color: var(--vscode-descriptionForeground);
    font-style: italic;
  }
  .reasoning summary::-webkit-details-marker { display: none; }
  .reasoning summary:hover { color: var(--vscode-foreground); }
  .reasoning-icon { display: inline-flex; opacity: 0.8; }
  .reasoning-body {
    margin-top: 5px;
    padding-left: 9px;
    border-left: 2px solid var(--vscode-panel-border);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    color: var(--vscode-descriptionForeground);
    line-height: 1.45;
    max-height: 260px;
    overflow-y: auto;
  }

  /* --- tool calls ---------------------------------------------------------- */
  .tool-calls { display: flex; flex-direction: column; gap: 4px; margin: 6px 0; }
  .tool-call {
    border: 1px solid var(--vscode-panel-border);
    border-radius: 6px;
    font-size: 12px;
    overflow: hidden;
  }
  .tool-call summary, .tool-call-head {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 8px;
    cursor: pointer;
    list-style: none;
    min-width: 0;
  }
  .tool-call-head { cursor: default; }
  .tool-call summary::-webkit-details-marker { display: none; }
  .tool-icon { opacity: 0.7; flex: none; }
  .tool-name { font-family: var(--vscode-editor-font-family, monospace); font-weight: 600; flex: none; }
  .tool-args {
    color: var(--vscode-descriptionForeground);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
    min-width: 0;
  }
  .tool-status { font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--vscode-descriptionForeground); margin-left: auto; flex: none; }
  .tool-status-running .tool-status { color: var(--vscode-charts-yellow, var(--vscode-editorWarning-foreground)); }
  .tool-status-done .tool-status { color: var(--vscode-charts-green, var(--vscode-testing-iconPassed, inherit)); }
  .tool-status-cancelled .tool-status { color: var(--vscode-errorForeground); }
  .tool-status-awaiting {
    border-color: var(--vscode-inputValidation-warningBorder, var(--vscode-editorWarning-foreground));
  }
  .tool-status-awaiting .tool-status { color: var(--vscode-editorWarning-foreground); }
  .tool-approve-args {
    margin: 0;
    padding: 8px 10px;
    border-top: 1px solid var(--vscode-panel-border);
    font-family: var(--vscode-editor-font-family, monospace);
    font-size: 11px;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 160px;
    overflow-y: auto;
  }
  .tool-approve {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 6px;
    padding: 8px;
    border-top: 1px solid var(--vscode-panel-border);
  }
  .tool-approve-action {
    font-family: inherit;
    font-size: 11px;
    border: 1px solid var(--vscode-button-border, var(--vscode-panel-border));
    border-radius: 4px;
    padding: 3px 10px;
    cursor: pointer;
    background: var(--vscode-button-secondaryBackground, transparent);
    color: var(--vscode-button-secondaryForeground, var(--vscode-editor-foreground));
  }
  .tool-approve-action:hover { background: var(--vscode-button-secondaryHoverBackground, var(--vscode-toolbar-hoverBackground)); }
  .tool-approve-primary {
    background: var(--vscode-button-background);
    color: var(--vscode-button-foreground);
    border-color: var(--vscode-button-background);
  }
  .tool-approve-primary:hover { background: var(--vscode-button-hoverBackground); }
  .tool-approve-deny { color: var(--vscode-errorForeground); }
  /* --- the diff a write made ----------------------------------------------- */
  .diff { border-top: 1px solid var(--vscode-panel-border); }
  .diff-empty { padding: 6px 8px; font-size: 11px; color: var(--vscode-descriptionForeground); }
  .diff-path {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 8px;
    font-family: var(--vscode-editor-font-family, monospace);
    font-size: 10px;
    color: var(--vscode-descriptionForeground);
    border-bottom: 1px solid var(--vscode-panel-border);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .diff-count { margin-left: auto; display: flex; gap: 6px; flex: none; font-size: 10px; }
  .diff-added { color: var(--vscode-charts-green, var(--vscode-testing-iconPassed)); }
  .diff-removed { color: var(--vscode-errorForeground); }
  /* Tall, and scrolling inside itself: every changed line is rendered, so a
     large rewrite is read by scrolling this pane rather than by the diff
     deciding on the reader's behalf where to stop. */
  .diff-body { max-height: 460px; overflow: auto; padding: 4px 0; }

  /* Syntax colours from the editor's own token theme, so a diff matches the
     file it came from rather than inventing a second palette. */
  .hl-comment { color: var(--vscode-editorLineNumber-foreground, #6a9955); font-style: italic; }
  .hl-string { color: var(--vscode-debugTokenExpression-string, #ce9178); }
  .hl-number { color: var(--vscode-debugTokenExpression-number, #b5cea8); }
  .hl-keyword { color: var(--vscode-debugTokenExpression-name, #569cd6); }
  .hl-literal { color: var(--vscode-debugTokenExpression-boolean, #569cd6); }
  .hl-call { color: var(--vscode-charts-yellow, #dcdcaa); }
  .diff-line {
    display: flex;
    gap: 6px;
    padding: 0 8px;
    font-family: var(--vscode-editor-font-family, monospace);
    font-size: 11px;
    line-height: 1.45;
    white-space: pre;
  }
  .diff-sign { flex: none; width: 6px; opacity: 0.7; }
  /* Tinted rather than coloured text: a diff is read for its shape first, and
     recolouring whole lines fights the syntax colours people expect. */
  .diff-add { background: var(--vscode-diffEditor-insertedTextBackground, rgba(63, 185, 80, 0.15)); }
  .diff-remove { background: var(--vscode-diffEditor-removedTextBackground, rgba(248, 81, 73, 0.15)); }
  .diff-context { color: var(--vscode-descriptionForeground); }
  .diff-gap {
    color: var(--vscode-descriptionForeground);
    opacity: 0.7;
    font-style: italic;
    white-space: normal;
  }

  .tool-result {
    margin: 0;
    padding: 8px;
    border-top: 1px solid var(--vscode-panel-border);
    font-family: var(--vscode-editor-font-family, monospace);
    font-size: 11px;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 240px;
    overflow-y: auto;
  }

  /* --- composer ------------------------------------------------------------ */
  .composer { padding: 6px 10px 10px; }
  .context-chips { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 6px; }
  .context-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    max-width: 100%;
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 4px;
    border: 1px solid var(--vscode-panel-border);
    color: var(--vscode-descriptionForeground);
  }
  .context-chip-pinned { color: var(--vscode-foreground); border-color: var(--vscode-focusBorder); }
  .context-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .context-remove {
    border: none;
    background: none;
    cursor: pointer;
    padding: 0 1px;
    font-size: 13px;
    line-height: 1;
    color: inherit;
  }
  .context-remove:hover { color: var(--vscode-errorForeground); }

  .composer-box {
    border: 1px solid var(--vscode-input-border, var(--vscode-panel-border));
    border-radius: 8px;
    background: var(--vscode-input-background);
    padding: 6px 6px 4px;
  }
  .composer-box:focus-within { border-color: var(--vscode-focusBorder); }
  #input {
    width: 100%;
    display: block;
    resize: none;
    overflow-y: auto;
    max-height: 180px;
    font-family: inherit;
    font-size: 13px;
    line-height: 1.45;
    background: transparent;
    color: var(--vscode-input-foreground);
    border: none;
    padding: 2px 4px 4px;
  }
  #input:focus { outline: none; }
  #input:disabled { opacity: 0.6; }
  .composer-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; }
  .composer-spacer { flex: 1; min-width: 0; }
  .toolbar-select {
    font-family: inherit;
    font-size: 11px;
    color: var(--vscode-dropdown-foreground);
    background: var(--vscode-dropdown-background);
    border: 1px solid var(--vscode-dropdown-border, var(--vscode-panel-border));
    border-radius: 4px;
    padding: 2px 4px;
    max-width: 120px;
  }
  .icon-button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    flex: none;
    border: none;
    border-radius: 50%;
    cursor: pointer;
    background: none;
    color: var(--vscode-descriptionForeground);
  }
  .icon-button:hover { background: var(--vscode-toolbar-hoverBackground); color: var(--vscode-foreground); }
  .icon-button-send {
    background: var(--vscode-button-background);
    color: var(--vscode-button-foreground);
  }
  .icon-button-send:hover { background: var(--vscode-button-hoverBackground); color: var(--vscode-button-foreground); }
  .icon-button-stop { color: var(--vscode-errorForeground); }
`;

function randomNonce(): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let nonce = "";
  for (let index = 0; index < 32; index++) {
    nonce += alphabet[Math.floor(Math.random() * alphabet.length)];
  }
  return nonce;
}
