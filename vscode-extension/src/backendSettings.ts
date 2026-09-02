import * as vscode from "vscode";
import { describeBackend, normaliseBackendUrl, rememberBackend } from "./backendUrl";

/**
 * Picking the backend from the command palette, rather than from
 * `settings.json`.
 *
 * The case this is for is switching: a local backend on :8000 while working
 * on it, the deployed one the rest of the time, and back again several times
 * an hour. Doing that through the settings editor means finding the setting,
 * retyping a URL, and remembering what the other one was - so the URLs you
 * have used are offered as a list and the current one is marked.
 */

const RECENT_KEY = "knowledgeBase.recentBackends";

export interface BackendSwitch {
  /** What was chosen, normalised. Undefined when the user backed out. */
  url?: string;
}

export async function chooseBackendUrl(
  context: vscode.ExtensionContext,
  current: string,
): Promise<BackendSwitch> {
  const recent = context.globalState.get<string[]>(RECENT_KEY) ?? [];
  const known = [current, ...recent.filter((url) => url !== current)];

  const OTHER = "$(edit) Enter a different URL…";
  const picked = await vscode.window.showQuickPick(
    [
      ...known.map((url) => ({
        label: url === current ? `$(check) ${url}` : `$(circle-large-outline) ${url}`,
        description: url === current ? "current" : describeBackend(url),
        url,
      })),
      { label: OTHER, description: "", url: undefined as string | undefined },
    ],
    { title: "Backend", placeHolder: "Which backend should the coder talk to?" },
  );
  if (!picked) return {};

  const url = picked.url ?? (await askForUrl(current));
  if (!url) return {};
  if (url === current) return {};

  await context.globalState.update(RECENT_KEY, rememberBackend(known, current));
  await saveBackendUrl(url);
  return { url };
}

async function askForUrl(current: string): Promise<string | undefined> {
  const typed = await vscode.window.showInputBox({
    title: "Backend URL",
    prompt: "The API, not the web app. A bare host:port is accepted.",
    value: current,
    valueSelection: [0, current.length],
    ignoreFocusOut: true,
    validateInput: (value) =>
      value.trim() && !normaliseBackendUrl(value)
        ? "Not a URL. An origin like https://example.com or localhost:8000 - no path."
        : undefined,
  });
  return typed ? normaliseBackendUrl(typed) : undefined;
}

/**
 * Written back to whichever scope already holds it.
 *
 * A workspace value shadows the user one entirely, so writing the change to
 * the wrong scope would look like it did nothing: the setting would be
 * updated somewhere the editor is not reading it from.
 */
async function saveBackendUrl(url: string): Promise<void> {
  const config = vscode.workspace.getConfiguration("knowledgeBase");
  const inspected = config.inspect<string>("baseUrl");
  const target =
    inspected?.workspaceValue !== undefined
      ? vscode.ConfigurationTarget.Workspace
      : vscode.ConfigurationTarget.Global;
  await config.update("baseUrl", url, target);
}
