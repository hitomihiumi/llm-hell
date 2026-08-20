import * as vscode from "vscode";
import { fileName } from "./format";
import type { SearchHit } from "./types";

/**
 * What a result looks like when you open it.
 *
 * Everything opens as a read-only virtual document rather than a webview, and
 * that is a deliberate trade. A code hit lands in a real editor: syntax
 * highlighting, find, go-to-line, split view, and copy that produces the file
 * rather than the rendering of the file. An answer lands in a Markdown buffer,
 * which the editor previews with its own renderer — so this extension carries
 * no HTML, no CSP and no styling to keep in step with a theme.
 *
 * The scheme is `kb:`. The path is a readable name so the tab says
 * `auth-service/README.md` rather than an id, and the hit id rides in the
 * query, where it keeps two same-named results apart.
 */

export const SCHEME = "kb";

const ANSWER_PATH = "/Answer.md";

export class KnowledgeBaseDocuments implements vscode.TextDocumentContentProvider {
  private readonly changed = new vscode.EventEmitter<vscode.Uri>();
  readonly onDidChange = this.changed.event;

  /** uri.toString() -> text. */
  private readonly texts = new Map<string, string>();

  provideTextDocumentContent(uri: vscode.Uri): string {
    return this.texts.get(uri.toString()) ?? "";
  }

  /** Publish text at `uri`, telling any editor already showing it to reload. */
  set(uri: vscode.Uri, text: string): void {
    this.texts.set(uri.toString(), text);
    this.changed.fire(uri);
  }

  forget(uri: vscode.Uri): void {
    this.texts.delete(uri.toString());
  }
}

export function hitUri(hit: SearchHit): vscode.Uri {
  return vscode.Uri.from({ scheme: SCHEME, path: `/${fileName(hit)}`, query: hit.id });
}

export function answerUri(): vscode.Uri {
  return vscode.Uri.from({ scheme: SCHEME, path: ANSWER_PATH });
}

/**
 * Set the language when the API named one the editor knows.
 *
 * Unknown ids throw rather than degrade, and a missing colour scheme is not
 * worth an error message about a document that opened perfectly well.
 */
export async function applyLanguage(
  document: vscode.TextDocument,
  language: string | null,
): Promise<void> {
  if (!language || document.languageId === language) return;
  const known = await vscode.languages.getLanguages();
  if (!known.includes(language)) return;
  await vscode.languages.setTextDocumentLanguage(document, language);
}
