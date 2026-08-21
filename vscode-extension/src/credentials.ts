import * as vscode from "vscode";
import type { KnowledgeBaseClient } from "./client";
import type { CredentialStatus } from "./types";

/**
 * Connecting the accounts a search actually reads.
 *
 * Signing in to the knowledge base says who you are. It does not say what you
 * may read: Drive and GitLab belong to Google and to your GitLab instance,
 * and each wants its own consent. Without that second step a search runs on
 * whatever tokens the deployment happens to hold — which for one person is
 * fine and for a team is somebody else's Drive.
 *
 * The two providers ask for different things and the difference is visible
 * here on purpose. A GitLab token is something the user already has, so it is
 * pasted and verified. Google consent is something only Google can grant, so
 * this opens a browser and then asks the backend whether it worked, rather
 * than assuming a closed tab meant yes.
 *
 * **No token is ever stored on this side.** The extension's secret storage
 * holds the knowledge-base password and nothing else; a GitLab token is
 * posted once, encrypted server-side, and never read back. That is why there
 * is no "show token" anywhere in this file — there is nothing to show.
 */

const GITLAB_TOKEN_URL = "/-/user_settings/personal_access_tokens";

export function describe(status: CredentialStatus): string {
  if (!status.storage_available) return "unavailable on this server";
  if (!status.connected) return "not connected";
  if (status.expired) return `expired${status.account ? ` — ${status.account}` : ""}`;
  return status.account ?? "connected";
}

/** The one-line summary the sidebar and the status bar both want. */
export function summarise(statuses: CredentialStatus[]): string {
  const missing = statuses.filter((status) => !status.connected || status.expired);
  if (!statuses.length) return "";
  if (!missing.length) return "All accounts connected";
  return `Not connected: ${missing.map((status) => status.provider).join(", ")}`;
}

/**
 * Show the state, and let the user act on it.
 *
 * One list rather than a command per provider, because the question a person
 * actually has is "why is Drive empty" and the answer is in the comparison.
 */
export async function manageCredentials(client: KnowledgeBaseClient): Promise<void> {
  let statuses: CredentialStatus[];
  try {
    statuses = await client.credentials();
  } catch (error) {
    vscode.window.showErrorMessage(`Could not read your accounts: ${(error as Error).message}`);
    return;
  }

  if (statuses.every((status) => !status.storage_available)) {
    vscode.window.showWarningMessage(
      "This server stores no per-user accounts — searches use its own tokens. " +
        "Set CREDENTIALS_ENCRYPTION_KEY on the backend to change that.",
    );
    return;
  }

  const picks = statuses.map((status) => ({
    label: status.provider === "google" ? "Google Workspace" : "GitLab",
    description: describe(status),
    detail: status.connected
      ? "Select to reconnect or disconnect"
      : "Select to connect this account",
    status,
  }));

  const chosen = await vscode.window.showQuickPick(picks, {
    title: "Accounts the knowledge base searches on your behalf",
    placeHolder: summarise(statuses),
  });
  if (!chosen) return;

  if (chosen.status.connected) {
    const action = await vscode.window.showQuickPick(["Reconnect", "Disconnect"], {
      title: `${chosen.label} — ${describe(chosen.status)}`,
    });
    if (action === "Disconnect") {
      await client.disconnect(chosen.status.provider);
      vscode.window.showInformationMessage(`Disconnected ${chosen.label}.`);
      return;
    }
    if (action !== "Reconnect") return;
  }

  if (chosen.status.provider === "gitlab") await connectGitLab(client);
  else await connectGoogle(client);
}

/**
 * Paste a personal access token.
 *
 * `password: true` on the box, so the token does not sit in plain sight in a
 * screen share, and `ignoreFocusOut` because getting it means leaving the
 * editor for GitLab's settings page and coming back.
 */
export async function connectGitLab(client: KnowledgeBaseClient): Promise<boolean> {
  const open = "Open GitLab settings";
  const paste = "I have a token";
  const first = await vscode.window.showInformationMessage(
    "GitLab needs a personal access token with the `read_api` scope.",
    { modal: false },
    open,
    paste,
  );
  if (!first) return false;
  if (first === open) {
    const base = await vscode.window.showInputBox({
      title: "GitLab",
      prompt: "Your GitLab URL",
      value: "http://localhost:32772",
      ignoreFocusOut: true,
    });
    if (!base) return false;
    await vscode.env.openExternal(vscode.Uri.parse(base.replace(/\/+$/, "") + GITLAB_TOKEN_URL));
  }

  const token = await vscode.window.showInputBox({
    title: "GitLab",
    prompt: "Paste the personal access token",
    password: true,
    ignoreFocusOut: true,
  });
  if (!token) return false;

  const apiUrl = await vscode.window.showInputBox({
    title: "GitLab",
    prompt: "API URL — leave empty to use the server's own",
    placeHolder: "http://gitlab.example.com/api/v4",
    ignoreFocusOut: true,
  });
  if (apiUrl === undefined) return false;

  try {
    const status = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "Verifying the token with GitLab…" },
      () => client.connectGitLab(token.trim(), apiUrl.trim() || undefined),
    );
    vscode.window.showInformationMessage(
      `GitLab connected${status.account ? ` as ${status.account}` : ""}.`,
    );
    return true;
  } catch (error) {
    // The backend checked the token against the instance, so this message is
    // GitLab's own reason rather than a generic failure.
    vscode.window.showErrorMessage(`GitLab refused that token: ${(error as Error).message}`);
    return false;
  }
}

/**
 * Google consent, in three steps because that is what OAuth is.
 *
 * The confirmation step is not a formality. The backend asks Google's own
 * server whether the account authenticated; a user who closed the tab gets
 * told so here, rather than getting an empty Drive a week later.
 */
export async function connectGoogle(client: KnowledgeBaseClient): Promise<boolean> {
  let start: Awaited<ReturnType<KnowledgeBaseClient["startGoogle"]>>;
  try {
    start = await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: "Asking Google for a consent link…",
      },
      () => client.startGoogle(),
    );
  } catch (error) {
    vscode.window.showErrorMessage(`Could not start Google sign-in: ${(error as Error).message}`);
    return false;
  }

  if (!start.authenticated) {
    // The Workspace MCP server cannot run consent headless: its authenticate
    // operation opens a browser and waits for a local callback, so in a
    // container it hangs until the tool timeout with no URL ever emitted.
    // The server's own message says where to add the account instead, and
    // that is more useful than anything this could invent.
    const document = await vscode.workspace.openTextDocument({
      content: start.message || `Google has no credentials for ${start.email}.`,
      language: "markdown",
    });
    await vscode.window.showTextDocument(document, { preview: true });
    return false;
  }

  // Reserved: a server that emits a consent URL rather than opening one
  // itself would be handled here without changing anything else.
  if (start.url) {
    await vscode.env.openExternal(vscode.Uri.parse(start.url));
    const done = await vscode.window.showInformationMessage(
      `Approve access for ${start.email} in the browser, then come back.`,
      { modal: true },
      "I have approved it",
    );
    if (done !== "I have approved it") return false;
  }

  try {
    const status = await client.confirmGoogle();
    vscode.window.showInformationMessage(`Google connected as ${status.account ?? start.email}.`);
    return true;
  } catch (error) {
    vscode.window.showErrorMessage(
      `Google does not show that account as connected yet: ${(error as Error).message}`,
    );
    return false;
  }
}

/**
 * After signing in, offer to connect whatever is missing.
 *
 * Offered once and never nagged: a deployment that keeps its own tokens is a
 * legitimate setup, and so is a user who only cares about GitLab.
 */
export async function offerMissing(client: KnowledgeBaseClient): Promise<void> {
  let statuses: CredentialStatus[];
  try {
    statuses = await client.credentials();
  } catch {
    // The sign-in worked; a failure here is not worth a second dialog.
    return;
  }

  const missing = statuses.filter((status) => status.storage_available && !status.connected);
  if (!missing.length) return;

  const connect = "Connect accounts";
  const answer = await vscode.window.showInformationMessage(
    `Signed in. ${missing.map((status) => status.provider).join(" and ")} ` +
      "not connected yet — searches will use the server's own access.",
    connect,
    "Later",
  );
  if (answer === connect) await manageCredentials(client);
}
