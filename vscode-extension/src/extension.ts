import * as vscode from "vscode";
import type { AgentMode } from "./agentMode";
import { chooseBackendUrl } from "./backendSettings";
import { KnowledgeBaseChatViewProvider } from "./chatView";
import { AuthError, KnowledgeBaseClient } from "./client";
import { registerCoderParticipant } from "./coder";
import { manageCredentials, offerMissing } from "./credentials";
import { KnowledgeBaseDocuments, SCHEME } from "./documents";
import { collapse } from "./format";
import { disposeMcpServers, mcpOutputChannel, reloadMcpServers } from "./mcp";
import { manageMcpServers } from "./mcpSettings";

/**
 * A coding agent, in the editor, backed by the team's corpus.
 *
 * The extension is a thin client on purpose: the backend runs the model and
 * the retrieval, and nothing here re-implements either. What it adds is the
 * two things an editor is for - the agent's tools run where the files and the
 * terminal actually are, and the conversation happens beside the code rather
 * than in a browser tab.
 */

/**
 * What the welcome screen switches on.
 *
 * A signed-out panel offering to work is a panel whose first action fails, so
 * the view says "sign in" instead - and needs a context key to know which of
 * the two states it is in.
 */
const SIGNED_IN_CONTEXT = "knowledgeBase.signedIn";

export function activate(context: vscode.ExtensionContext): void {
  const client = new KnowledgeBaseClient(context.secrets, readSettings);
  const documents = new KnowledgeBaseDocuments();

  async function setSignedIn(value: boolean): Promise<void> {
    await vscode.commands.executeCommand("setContext", SIGNED_IN_CONTEXT, value);
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

  const coderChat = registerCoder(
    context,
    client,
    () => readSettings().maxAgentTurns,
    () => readSettings().agentMode,
  );

  async function askInCoderChat(question: string): Promise<void> {
    await vscode.commands.executeCommand("workbench.action.chat.open", {
      // A trailing space leaves the caret after the mention rather than
      // inside it, so typing continues the question.
      query: question ? `@coder ${question}` : "@coder ",
    });
  }

  const chatView = new KnowledgeBaseChatViewProvider(context, client, documents, () => {
    const settings = readSettings();
    return { maxAgentTurns: settings.maxAgentTurns, agentMode: settings.agentMode };
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
    // A proposed `write_file` is served from here so the editor's own diff
    // view can be pointed at it.
    vscode.workspace.registerTextDocumentContentProvider(SCHEME, documents),

    // `@coder` in VS Code's own chat panel, alongside the view this extension
    // draws itself. Registration is guarded because the chat API is absent in
    // editors built without it, and an extension that fails to activate takes
    // its sidebar down with it.
    ...coderChat,

    vscode.commands.registerCommand("knowledgeBase.accounts", () => manageCredentials(client)),

    vscode.commands.registerCommand("knowledgeBase.setBackendUrl", async () => {
      const { url } = await chooseBackendUrl(context, readSettings().baseUrl);
      if (!url) return;
      // `baseUrl` is read per request, so the next call already goes to the
      // new backend - but the session in memory belongs to the old one and
      // would be presented to it.
      client.forgetSession();
      const signedIn = await client.hasCredentials();
      await setSignedIn(signedIn);
      await chatView.refreshSignedIn();
      const action = await vscode.window.showInformationMessage(
        `Backend is now ${url}.`,
        ...(signedIn ? [] : ["Sign in"]),
      );
      if (action === "Sign in") await authenticate();
    }),

    vscode.commands.registerCommand("knowledgeBase.mcp.manage", () => manageMcpServers()),
    vscode.commands.registerCommand("knowledgeBase.mcp.reload", () => {
      reloadMcpServers();
      vscode.window.showInformationMessage("MCP servers will reconnect on the next tool call.");
    }),
    vscode.commands.registerCommand("knowledgeBase.mcp.showLog", () => mcpOutputChannel().show()),

    // A server added, removed or edited outside the guided flow - by hand in
    // settings.json - should not need a window reload to take effect.
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("knowledgeBase.mcp.servers")) reloadMcpServers();
    }),

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
      await setSignedIn(false);
      vscode.window.showInformationMessage("Signed out of the knowledge base.");
    }),

    vscode.commands.registerCommand("knowledgeBase.askSelection", async () => {
      await askInCoderChat(prefill());
    }),
  );
}

/**
 * The `@coder` participant.
 *
 * Returns nothing when the editor has no chat API rather than throwing: this
 * runs during activation, and an extension that fails there loses its
 * commands and its view as well as the chat it could not have had anyway.
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
    return [registerCoderParticipant(context, client, maxAgentTurns, agentMode)];
  } catch (error) {
    console.warn("knowledge base: coder participant not registered", error);
    return [];
  }
}

export function deactivate(): void {
  // Every disposable in this window is on the context's subscriptions and the
  // session lives in memory only - except a custom MCP server's own process,
  // which is not a vscode.Disposable and would otherwise outlive the window.
  disposeMcpServers();
}

// --- the pieces --------------------------------------------------------------

interface Settings {
  baseUrl: string;
  username: string;
  maxAgentTurns: number;
  agentMode: AgentMode;
}

export function readSettings(): Settings {
  const config = vscode.workspace.getConfiguration("knowledgeBase");
  return {
    baseUrl: config.get<string>("baseUrl", "https://llmhell.borzo.ai"),
    username: config.get<string>("username", ""),
    maxAgentTurns: config.get<number>("coder.maxAgentTurns", 30),
    agentMode: config.get<AgentMode>("coder.mode", "manual"),
  };
}

/**
 * What to hand the coder before the user types.
 *
 * The selection when there is one, and otherwise the symbol under the cursor
 * - which is the case this is really for. Asking about `RRF_K` should not
 * require selecting it first.
 */
export function prefill(): string {
  const editor = vscode.window.activeTextEditor;
  if (!editor) return "";

  const selected = editor.document.getText(editor.selection).trim();
  if (selected) return collapse(selected);

  const range = editor.document.getWordRangeAtPosition(editor.selection.active);
  return range ? editor.document.getText(range) : "";
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

/**
 * One place that turns a thrown error into something worth reading.
 *
 * Returns whether the user signed in as a result, so the caller can do again
 * whatever it was that needed the session.
 */
export async function report(
  error: unknown,
  authenticate: () => Promise<boolean>,
): Promise<boolean> {
  if (error instanceof AuthError) {
    const action = await vscode.window.showErrorMessage(error.message, "Sign In");
    return action === "Sign In" ? await authenticate() : false;
  }
  vscode.window.showErrorMessage((error as Error).message ?? String(error));
  return false;
}
