import * as vscode from "vscode";
import type { AgentMode } from "./agentMode";
import { registerChatParticipant } from "./chat";
import { KnowledgeBaseChatViewProvider } from "./chatView";
import { ApiError, AuthError, KnowledgeBaseClient } from "./client";
import { registerCoderParticipant } from "./coder";
import { manageCredentials, offerMissing } from "./credentials";
import { answerUri, applyLanguage, hitUri, KnowledgeBaseDocuments, SCHEME } from "./documents";
import { answerMarkdown, collapse } from "./format";
import { ResultsProvider } from "./resultsView";
import type { SearchHit, SearchResponse } from "./types";

/**
 * Federated search, from the editor.
 *
 * The whole extension is a thin client: the backend already plans the
 * queries, fuses the sources and writes the cited answer, and nothing here
 * re-implements any of that. What it adds is the two things an editor is for
 * — searching for what is under the cursor without retyping it, and opening a
 * result as a real document instead of a card.
 */

/** Where the chosen source filter lives. Per window, so two projects can differ. */
const SOURCES_KEY = "knowledgeBase.sources";

/**
 * What the welcome screen switches on.
 *
 * A view with no children shows its `viewsWelcome` content, and there are two
 * of them - one offering to sign in, one offering to search. Without a context
 * key to pick between them the panel says "Sign in" to somebody who has just
 * signed in, and looks like nothing happened.
 */
const SIGNED_IN_CONTEXT = "knowledgeBase.signedIn";

export function activate(context: vscode.ExtensionContext): void {
  const client = new KnowledgeBaseClient(context.secrets, readSettings);
  const documents = new KnowledgeBaseDocuments();
  const results = new ResultsProvider();

  const view = vscode.window.createTreeView("knowledgeBase.results", {
    treeDataProvider: results,
    showCollapseAll: true,
  });

  async function setSignedIn(value: boolean): Promise<void> {
    await vscode.commands.executeCommand("setContext", SIGNED_IN_CONTEXT, value);
    // Then ask the view to redraw. The context change should be enough on its
    // own; this costs nothing and removes the question.
    results.show(results.current);
  }

  /** Sign in and reflect it, wherever the prompt came from. */
  async function authenticate(): Promise<boolean> {
    const ok = await signIn(client);
    if (ok) {
      await setSignedIn(true);
      // Signing in says who you are; it does not say what you may read.
      // Offered once, here, because this is the moment the difference
      // becomes visible - and never nagged afterwards.
      void offerMissing(client);
    }
    return ok;
  }

  // A session lives in memory, so a restarted editor has none - but the
  // credential that would establish one has been in the keychain since the
  // first sign-in. Asked here rather than assumed, so the panel opens in the
  // state the user left it in.
  void client.hasCredentials().then(setSignedIn);

  const chat = registerChat(context, client, results, view);
  const hasChat = chat.length > 0;

  const coderChat = registerCoder(
    context,
    client,
    () => readSettings().maxAgentTurns,
    () => readSettings().agentMode,
  );

  /** Put a question in the chat box, addressed to the participant. */
  async function askInChat(question: string): Promise<void> {
    await vscode.commands.executeCommand("workbench.action.chat.open", {
      // A trailing space leaves the caret after the mention rather than
      // inside it, so typing continues the question.
      query: question ? `@kb ${question}` : "@kb ",
    });
  }

  async function askInCoderChat(question: string): Promise<void> {
    await vscode.commands.executeCommand("workbench.action.chat.open", {
      query: question ? `@coder ${question}` : "@coder ",
    });
  }

  const chatView = new KnowledgeBaseChatViewProvider(context, client, documents, () => {
    const settings = readSettings();
    return {
      sources: context.workspaceState.get<string[]>(SOURCES_KEY) ?? settings.sources,
      limit: settings.limit,
      maxAgentTurns: settings.maxAgentTurns,
      agentMode: settings.agentMode,
    };
  });

  // Selection changes arrive per keystroke while a user drags a selection.
  // The chips only ever show a filename and a line count, so redrawing them
  // that often is pure waste - one refresh once the movement settles is the
  // same result for a fraction of the traffic across the postMessage bridge.
  let contextTimer: NodeJS.Timeout | undefined;
  function scheduleContextRefresh(): void {
    if (contextTimer) clearTimeout(contextTimer);
    contextTimer = setTimeout(() => chatView.publishContext(), 150);
  }

  context.subscriptions.push(
    view,
    vscode.workspace.registerTextDocumentContentProvider(SCHEME, documents),

    // `@kb` in the chat panel, which is the interface this is really for: the
    // backend plans a search from the conversation, and only a chat has one.
    // Registration is guarded because the chat API is absent in editors built
    // without it, and an extension that fails to activate takes its sidebar
    // down with it.
    ...chat,

    // `@coder` uses the same backend RAG pipeline but speaks the OpenAI
    // chat-completion format, so it can be used as a coding assistant.
    ...coderChat,

    vscode.commands.registerCommand("knowledgeBase.openChat", () => askInChat("")),

    vscode.commands.registerCommand("knowledgeBase.accounts", () => manageCredentials(client)),

    vscode.commands.registerCommand("knowledgeBase.openCoderChat", () => askInCoderChat("")),

    vscode.window.registerWebviewViewProvider(KnowledgeBaseChatViewProvider.viewType, chatView, {
      // The transcript is a page, not a render of state the host can rebuild
      // cheaply - a collapsed sidebar that threw it away and replayed it
      // would lose scroll position and every open tool card with it.
      webviewOptions: { retainContextWhenHidden: true },
    }),

    // The editor moved, so the chips above the composer should say so. Both
    // events fire far more often than the view needs redrawing, hence the
    // coalescing timer rather than a post per keystroke.
    vscode.window.onDidChangeActiveTextEditor(scheduleContextRefresh),
    vscode.window.onDidChangeTextEditorSelection(scheduleContextRefresh),

    vscode.commands.registerCommand("knowledgeBase.openCustomChat", () =>
      vscode.commands.executeCommand("knowledgeBase.chat.focus"),
    ),

    vscode.commands.registerCommand("knowledgeBase.newChat", () => chatView.newChat()),

    vscode.commands.registerCommand("knowledgeBase.chatHistory", () => chatView.showHistory()),

    vscode.commands.registerCommand("knowledgeBase.signIn", authenticate),

    vscode.commands.registerCommand("knowledgeBase.signOut", async () => {
      await client.signOut();
      results.show(undefined);
      await setSignedIn(false);
      vscode.window.showInformationMessage("Signed out of the knowledge base.");
    }),

    vscode.commands.registerCommand("knowledgeBase.search", async () => {
      const query = await ask(prefill(false));
      if (query) await run(query);
    }),

    vscode.commands.registerCommand("knowledgeBase.searchSelection", async () => {
      // Straight into the chat, carrying the selection. That is where a
      // question belongs - the answer can be followed up, and the follow-up
      // is planned with this turn in view. The box is the fallback for an
      // editor with no chat panel to open.
      const selected = prefill(true);
      if (hasChat) {
        await askInChat(selected);
        return;
      }
      if (selected) await run(selected);
      else {
        const query = await ask("");
        if (query) await run(query);
      }
    }),

    vscode.commands.registerCommand("knowledgeBase.refresh", async () => {
      const previous = results.current?.query;
      if (previous) await run(previous);
      else await vscode.commands.executeCommand("knowledgeBase.search");
    }),

    vscode.commands.registerCommand("knowledgeBase.chooseSources", () =>
      chooseSources(client, context, authenticate),
    ),

    vscode.commands.registerCommand("knowledgeBase.showAnswer", () => {
      const response = results.current;
      if (!response?.answer) {
        vscode.window.showInformationMessage("No answer yet. Run a search first.");
        return;
      }
      return openAnswer(documents, response);
    }),

    vscode.commands.registerCommand("knowledgeBase.openHit", (hit: SearchHit) =>
      openHit(client, documents, hit, authenticate),
    ),

    vscode.commands.registerCommand("knowledgeBase.openExternal", (node?: { hit?: SearchHit }) => {
      const url = node?.hit?.url;
      if (url) vscode.env.openExternal(vscode.Uri.parse(url));
    }),

    vscode.commands.registerCommand(
      "knowledgeBase.copyLink",
      async (node?: { hit?: SearchHit }) => {
        const url = node?.hit?.url;
        if (!url) return;
        await vscode.env.clipboard.writeText(url);
        vscode.window.showInformationMessage("Link copied.");
      },
    ),
  );

  /**
   * One search, and one retry if signing in was what it needed.
   *
   * The retry is the whole point of the flag. Searching while signed out puts
   * up a toast with a "Sign In" button; without this, pressing it succeeds and
   * then nothing happens, and the user has to remember what they were doing
   * and ask again.
   */
  async function run(query: string, retried = false): Promise<void> {
    const settings = readSettings();
    const sources = context.workspaceState.get<string[]>(SOURCES_KEY) ?? settings.sources;

    let response: SearchResponse;
    try {
      response = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Window, title: `Searching: ${query}` },
        () =>
          client.search(query, {
            sources,
            limit: settings.limit,
            answer: settings.answer,
          }),
      );
    } catch (error) {
      const signedIn = await report(error, authenticate);
      if (signedIn && !retried) await run(query, true);
      return;
    }

    results.show(response);
    view.title = `Results · ${response.hits.length}`;
    // Bring the sidebar forward. A search whose results land in a panel the
    // user cannot see reads as a search that did nothing.
    await vscode.commands.executeCommand("knowledgeBase.results.focus");

    if (response.answer?.text) {
      await openAnswer(documents, response);
    }

    const failed = response.source_status.filter((status) => !status.ok);
    if (failed.length) {
      vscode.window.showWarningMessage(
        `Searched without ${failed.map((status) => status.display_name || status.source).join(", ")}.`,
      );
    }
  }
}

/**
 * The chat participant, plus the wiring that keeps the sidebar in step.
 *
 * Returns nothing when the editor has no chat API rather than throwing: this
 * runs during activation, and an extension that fails there loses its
 * commands and its view as well as the chat it could not have had anyway.
 */
function registerChat(
  context: vscode.ExtensionContext,
  client: KnowledgeBaseClient,
  results: ResultsProvider,
  view: vscode.TreeView<unknown>,
): vscode.Disposable[] {
  if (!vscode.chat?.createChatParticipant) {
    return [];
  }
  try {
    const participant = registerChatParticipant(context, client, () => {
      const settings = readSettings();
      return {
        sources: context.workspaceState.get<string[]>(SOURCES_KEY) ?? settings.sources,
        limit: settings.limit,
        onResults: (response: SearchResponse) => {
          // Everything the chat found also lands in the sidebar, so the
          // results stay browsable after the answer has scrolled away.
          results.show(response);
          view.title = `Results · ${response.hits.length}`;
        },
      };
    });
    return [participant];
  } catch (error) {
    // A duplicate id or a manifest the editor did not accept. Worth a line in
    // the log; not worth taking the rest of the extension down.
    console.warn("knowledge base: chat participant not registered", error);
    return [];
  }
}

/**
 * The coding assistant participant.
 *
 * Same guard as `registerChat`: if the editor has no chat API, this returns
 * nothing instead of failing activation.
 */
function registerCoder(
  context: vscode.ExtensionContext,
  client: KnowledgeBaseClient,
  maxAgentTurns: () => number,
  agentMode: () => AgentMode,
): vscode.Disposable[] {
  if (!vscode.chat?.createChatParticipant) {
    return [];
  }
  try {
    const participant = registerCoderParticipant(context, client, maxAgentTurns, agentMode);
    return [participant];
  } catch (error) {
    console.warn("knowledge base: coder participant not registered", error);
    return [];
  }
}

export function deactivate(): void {
  // Nothing to tear down: every disposable is on the context's subscriptions,
  // and the session lives in memory only.
}

// --- the pieces --------------------------------------------------------------

interface Settings {
  baseUrl: string;
  username: string;
  sources: string[];
  limit: number;
  answer: boolean;
  searchOnSelection: boolean;
  maxAgentTurns: number;
  agentMode: AgentMode;
}

export function readSettings(): Settings {
  const config = vscode.workspace.getConfiguration("knowledgeBase");
  return {
    baseUrl: config.get<string>("baseUrl", "http://localhost:8000"),
    username: config.get<string>("username", ""),
    sources: config.get<string[]>("sources", []),
    limit: config.get<number>("limit", 20),
    answer: config.get<boolean>("answer", true),
    searchOnSelection: config.get<boolean>("searchOnSelection", true),
    maxAgentTurns: config.get<number>("coder.maxAgentTurns", 30),
    agentMode: config.get<AgentMode>("coder.mode", "manual"),
  };
}

/**
 * What to put in the search box before the user types.
 *
 * The selection when there is one, and otherwise the symbol under the cursor
 * — which is the case this is really for. Asking the knowledge base about
 * `RRF_K` should not require selecting it first.
 */
export function prefill(requireSelection: boolean): string {
  const editor = vscode.window.activeTextEditor;
  if (!editor) return "";
  if (!readSettings().searchOnSelection && !requireSelection) return "";

  const selected = editor.document.getText(editor.selection).trim();
  if (selected) return collapse(selected);
  if (requireSelection) return "";

  const range = editor.document.getWordRangeAtPosition(editor.selection.active);
  return range ? editor.document.getText(range) : "";
}

async function ask(value: string): Promise<string | undefined> {
  const query = await vscode.window.showInputBox({
    title: "Search the knowledge base",
    prompt: "A question, or a term to look for",
    value,
    valueSelection: value ? [0, value.length] : undefined,
  });
  return query?.trim() || undefined;
}

/** Returns whether a session was established, so the caller can act on it. */
async function signIn(client: KnowledgeBaseClient): Promise<boolean> {
  const config = vscode.workspace.getConfiguration("knowledgeBase");
  const username = await vscode.window.showInputBox({
    title: "Knowledge base",
    prompt: "Username",
    value: config.get<string>("username", ""),
    ignoreFocusOut: true,
  });
  if (!username) return false;

  const password = await vscode.window.showInputBox({
    title: "Knowledge base",
    prompt: `Password for ${username}`,
    password: true,
    ignoreFocusOut: true,
  });
  if (!password) return false;

  try {
    await client.signIn({ username, password });
  } catch (error) {
    vscode.window.showErrorMessage(`Could not sign in: ${(error as Error).message}`);
    return false;
  }

  // Written to the settings only after the credentials are known to work, so
  // a typo does not become the saved username.
  await config.update("username", username, vscode.ConfigurationTarget.Global);
  vscode.window.showInformationMessage(`Signed in as ${username}.`);
  return true;
}

async function chooseSources(
  client: KnowledgeBaseClient,
  context: vscode.ExtensionContext,
  authenticate: () => Promise<boolean>,
): Promise<void> {
  let available: { key: string; display_name: string; enabled: boolean }[];
  try {
    available = await client.sources();
  } catch (error) {
    await report(error, authenticate);
    return;
  }

  // A source an admin switched off cannot be searched, so it is shown as
  // unavailable rather than hidden — a missing row looks like a bug.
  const chosen = new Set(
    context.workspaceState.get<string[]>(SOURCES_KEY) ?? readSettings().sources,
  );
  const picks = available.map((source) => ({
    label: source.display_name || source.key,
    description: source.enabled ? source.key : `${source.key} · switched off`,
    key: source.key,
    picked: source.enabled && (chosen.size === 0 || chosen.has(source.key)),
  }));

  const selected = await vscode.window.showQuickPick(picks, {
    canPickMany: true,
    title: "Sources to search",
    placeHolder: "Nothing selected means every enabled source",
  });
  if (!selected) return;

  // All of them selected is the same as none: store none, so a source added
  // to the backend later is searched rather than silently left out.
  const keys = selected.map((pick) => pick.key);
  const all = keys.length === available.filter((source) => source.enabled).length;
  await context.workspaceState.update(SOURCES_KEY, all ? undefined : keys);
}

async function openHit(
  client: KnowledgeBaseClient,
  documents: KnowledgeBaseDocuments,
  hit: SearchHit,
  authenticate: () => Promise<boolean>,
): Promise<void> {
  const uri = hitUri(hit);
  try {
    const content = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Window, title: `Opening ${hit.title}` },
      () => client.content(hit.id),
    );
    const body = content.truncated
      ? `${content.text}\n\n[truncated — open the source for the rest]`
      : content.text;
    documents.set(uri, body);
    const document = await vscode.workspace.openTextDocument(uri);
    await applyLanguage(document, content.language);
    await vscode.window.showTextDocument(document, { preview: true });
  } catch (error) {
    documents.forget(uri);
    // A source with nothing more to show answers 404, and that is a fact
    // about the result rather than a failure: Gmail's search already returned
    // the message body, so there is nothing left to open. Fall back to the
    // link where there is one.
    if (error instanceof ApiError && error.status === 404 && hit.url) {
      await vscode.env.openExternal(vscode.Uri.parse(hit.url));
      return;
    }
    await report(error, authenticate);
  }
}

async function openAnswer(
  documents: KnowledgeBaseDocuments,
  response: SearchResponse,
): Promise<void> {
  const uri = answerUri();
  documents.set(uri, answerMarkdown(response));
  const document = await vscode.workspace.openTextDocument(uri);
  await applyLanguage(document, "markdown");
  await vscode.window.showTextDocument(document, {
    preview: true,
    preserveFocus: true,
    viewColumn: vscode.ViewColumn.Active,
  });
}

/**
 * One place that turns a thrown error into something worth reading.
 *
 * Returns whether the user signed in as a result, so the caller can do again
 * whatever it was that needed the session.
 */
async function report(error: unknown, authenticate: () => Promise<boolean>): Promise<boolean> {
  if (error instanceof AuthError) {
    const action = await vscode.window.showErrorMessage(error.message, "Sign In");
    return action === "Sign In" ? await authenticate() : false;
  }
  vscode.window.showErrorMessage((error as Error).message ?? String(error));
  return false;
}
