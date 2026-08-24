/**
 * The messages that cross the postMessage boundary between the extension
 * host and the chat panel's webview.
 *
 * Kept in its own file, vscode-free, so `node --test` can import it directly
 * and so the shape is defined exactly once - the host and the webview script
 * are two separate bundles (a webview cannot `import` from the extension
 * host; it runs in its own isolated page) and would drift apart silently if
 * each side declared its own copy of what a message looks like.
 */

export type Mode = "kb" | "coder";

/** One line in the transcript, as the webview renders it. */
export interface ChatMessageView {
  id: string;
  role: "user" | "assistant";
  /** Which backend answered - only meaningful for an assistant message. */
  mode: Mode;
  /** Raw Markdown/plain text, rendered client-side. Grows while streaming. */
  text: string;
  status: "streaming" | "done" | "error";
  error?: string;
  /** `@kb`: the sources it read. Present once the `hits` event has arrived. */
  references?: ReferenceView[];
  /** `@kb`: the numbered citations under the answer. */
  citations?: CitationView[];
  /** `@coder`: each tool call this turn made, in order, updated as they resolve. */
  toolCalls?: ToolCallView[];
}

export interface ReferenceView {
  title: string;
  source: string;
  url: string | null;
  hitId: string;
}

export interface CitationView {
  n: number;
  title: string;
  url: string | null;
  source: string;
}

export interface ToolCallView {
  id: string;
  name: string;
  argsSummary: string;
  status: "running" | "done" | "cancelled";
  result?: string;
}

/** Host -> webview. */
export type HostMessage =
  | { type: "init"; signedIn: boolean; mode: Mode }
  | { type: "signedIn"; value: boolean }
  | { type: "messages"; messages: ChatMessageView[] }
  | { type: "error"; text: string };

/** Webview -> host. */
export type WebviewMessage =
  | { type: "ready" }
  | { type: "send"; text: string; mode: Mode }
  | { type: "stop" }
  | { type: "setMode"; mode: Mode }
  | { type: "signIn" }
  | { type: "openAccounts" }
  | { type: "openReference"; hitId: string; url: string | null }
  | { type: "openLink"; url: string }
  | { type: "confirmTool"; id: string; allow: boolean };

/** A short, single-line summary of a tool call's arguments, for the card. */
export function summariseToolArgs(name: string, argsJson: string): string {
  let args: Record<string, unknown>;
  try {
    args = JSON.parse(argsJson) as Record<string, unknown>;
  } catch {
    return truncateOneLine(argsJson, 80);
  }

  switch (name) {
    case "read_file":
    case "list_directory":
      return String(args.path ?? "");
    case "write_file":
      return String(args.path ?? "");
    case "run_terminal":
      return truncateOneLine(String(args.command ?? ""), 100);
    case "search_knowledge_base":
      return String(args.query ?? "");
    case "read_knowledge_base_result":
      return String(args.id ?? "");
    default:
      return truncateOneLine(
        Object.entries(args)
          .map(([key, value]) => `${key}=${String(value)}`)
          .join(", "),
        100,
      );
  }
}

function truncateOneLine(text: string, limit: number): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > limit ? `${flat.slice(0, limit)}…` : flat;
}
