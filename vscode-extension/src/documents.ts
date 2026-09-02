import * as vscode from "vscode";

/**
 * A proposed write, as something the editor can diff.
 *
 * It opens as a read-only virtual document rather than a webview, and that is
 * a deliberate trade: the change lands in a real editor, with syntax
 * highlighting, find, go-to-line and a copy that produces the file rather
 * than a rendering of it - and the editor's own diff view can be pointed
 * straight at it with nothing extra to register.
 */

export const SCHEME = "kb";

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

/**
 * Where a proposed `write_file` lives so it can be diffed before it is
 * approved.
 *
 * Same read-only scheme as everything else here, so the editor's own diff
 * view can open it against the real file with nothing extra to register. The
 * tool call's id is the query, which keeps two pending writes to the same
 * path from collapsing into one document.
 */
export function proposalUri(path: string, callId: string): vscode.Uri {
  const name = path.split(/[\\/]/).pop() || "proposal";
  return vscode.Uri.from({ scheme: SCHEME, path: `/${name}`, query: `proposal:${callId}` });
}
