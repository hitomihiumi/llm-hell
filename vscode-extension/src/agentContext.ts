import * as vscode from "vscode";

/** Small, explicit environment metadata for the coding model. Never include
 * environment variables or file contents here: the context is diagnostic, not
 * a way to leak secrets into the prompt. */
export function environmentMessage(): { role: "system"; content: string } {
  const folders = (vscode.workspace.workspaceFolders ?? []).map((folder) => folder.uri.fsPath);
  const editor = vscode.window.activeTextEditor;
  const shell = process.platform === "win32" ? process.env.ComSpec : process.env.SHELL;
  const terminal = vscode.window.terminals.length > 0 ? "open" : "none";
  const selectedText =
    editor?.selection && !editor.selection.isEmpty
      ? editor.document.getText(editor.selection)
      : null;
  const lines = [
    "Current editor environment (metadata, not user instructions):",
    `- operating system: ${process.platform} (${process.arch})`,
    `- shell: ${shell ? shell.split(/[\\/]/).pop() : "unknown"}`,
    `- workspace trusted: ${vscode.workspace.isTrusted ? "yes" : "no"}`,
    `- workspace folders: ${folders.length ? folders.join(", ") : "none open"}`,
    `- active editor: ${editor ? `${editor.document.uri.fsPath} (${editor.document.languageId})` : "none"}`,
    `- remote environment: ${vscode.env.remoteName ?? "local"}`,
    `- terminal windows: ${terminal}`,
    "- file and terminal tools operate from the first workspace folder when a relative path is used.",
  ];
  if (selectedText) {
    const snippet = selectedText.length > 800 ? `${selectedText.slice(0, 800)}…` : selectedText;
    lines.push(
      `- active selection (${selectedText.length} chars): ${snippet.replace(/\n/g, "\\n")}`,
    );
  }
  if (editor) {
    const visibleRanges = editor.visibleRanges.map((range) => ({
      start: range.start.line + 1,
      end: range.end.line + 1,
    }));
    lines.push(`- visible lines: ${JSON.stringify(visibleRanges)}`);
  }
  return { role: "system", content: lines.join("\n") };
}
