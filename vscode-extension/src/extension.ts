import * as vscode from "vscode";
import { ApiError, AuthError, KnowledgeBaseClient } from "./client";
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

export function activate(context: vscode.ExtensionContext): void {
  const client = new KnowledgeBaseClient(context.secrets, readSettings);
  const documents = new KnowledgeBaseDocuments();
  const results = new ResultsProvider();

  const view = vscode.window.createTreeView("knowledgeBase.results", {
    treeDataProvider: results,
    showCollapseAll: true,
  });

  context.subscriptions.push(
    view,
    vscode.workspace.registerTextDocumentContentProvider(SCHEME, documents),

    vscode.commands.registerCommand("knowledgeBase.signIn", () => signIn(client)),

    vscode.commands.registerCommand("knowledgeBase.signOut", async () => {
      await client.signOut();
      results.show(undefined);
      vscode.window.showInformationMessage("Signed out of the knowledge base.");
    }),

    vscode.commands.registerCommand("knowledgeBase.search", async () => {
      const query = await ask(prefill(false));
      if (query) await run(query);
    }),

    vscode.commands.registerCommand("knowledgeBase.searchSelection", async () => {
      // The selection is the query, not a suggestion: this command exists to
      // skip the box. An empty selection still asks, rather than searching
      // for nothing.
      const selected = prefill(true);
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
      chooseSources(client, context),
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
      openHit(client, documents, hit),
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

  async function run(query: string): Promise<void> {
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
      await report(error, client);
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

async function signIn(client: KnowledgeBaseClient): Promise<void> {
  const config = vscode.workspace.getConfiguration("knowledgeBase");
  const username = await vscode.window.showInputBox({
    title: "Knowledge base",
    prompt: "Username",
    value: config.get<string>("username", ""),
    ignoreFocusOut: true,
  });
  if (!username) return;

  const password = await vscode.window.showInputBox({
    title: "Knowledge base",
    prompt: `Password for ${username}`,
    password: true,
    ignoreFocusOut: true,
  });
  if (!password) return;

  try {
    await client.signIn({ username, password });
  } catch (error) {
    vscode.window.showErrorMessage(`Could not sign in: ${(error as Error).message}`);
    return;
  }

  // Written to the settings only after the credentials are known to work, so
  // a typo does not become the saved username.
  await config.update("username", username, vscode.ConfigurationTarget.Global);
  vscode.window.showInformationMessage(`Signed in as ${username}.`);
}

async function chooseSources(
  client: KnowledgeBaseClient,
  context: vscode.ExtensionContext,
): Promise<void> {
  let available: { key: string; display_name: string; enabled: boolean }[];
  try {
    available = await client.sources();
  } catch (error) {
    await report(error, client);
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
): Promise<void> {
  const uri = hitUri(hit);
  try {
    const content = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Window, title: `Opening ${hit.title}` },
      () => client.content(hit),
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
    await report(error, client);
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

/** One place that turns a thrown error into something worth reading. */
async function report(error: unknown, client: KnowledgeBaseClient): Promise<void> {
  if (error instanceof AuthError) {
    const action = await vscode.window.showErrorMessage(error.message, "Sign In");
    if (action === "Sign In") await signIn(client);
    return;
  }
  vscode.window.showErrorMessage((error as Error).message ?? String(error));
}
