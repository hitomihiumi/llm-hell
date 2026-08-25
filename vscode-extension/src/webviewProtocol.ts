/**
 * The messages that cross the postMessage boundary between the extension
 * host and the chat view's webview.
 *
 * Kept in its own file, vscode-free, so `node --test` can import it directly
 * and so the shape is defined exactly once - the host and the webview script
 * are two separate bundles (a webview cannot `import` from the extension
 * host; it runs in its own isolated page) and would drift apart silently if
 * each side declared its own copy of what a message looks like.
 */

import type { FileDiff } from "./diff.ts";

export type Mode = "kb" | "coder";
export type AgentMode = "manual" | "assisted" | "autonomous";

/**
 * One piece of an answer, in the order the model produced it.
 *
 * An agent turn is not prose with a footnote of tools - it is prose, then a
 * tool, then more prose written in the light of what that tool returned.
 * Collecting the calls into a list at the bottom loses exactly the thing that
 * makes an agent transcript readable, which is *when* each one happened. So
 * the message is a sequence, and rendering walks it.
 */
export type MessagePart =
  | { kind: "text"; text: string }
  /** The model's own thinking - `delta.reasoning`, not `delta.content`. */
  | { kind: "reasoning"; text: string }
  | { kind: "tool"; call: ToolCallView };

/** One line in the transcript, as the webview renders it. */
export interface ChatMessageView {
  id: string;
  role: "user" | "assistant";
  /** Which backend answered - only meaningful for an assistant message. */
  mode: Mode;
  /** Text, reasoning and tool calls, interleaved as they arrived. */
  parts: MessagePart[];
  status: "streaming" | "done" | "error";
  error?: string;
  /** `@kb`: the sources it read. Present once the `hits` event has arrived. */
  references?: ReferenceView[];
  /** Per-source outcome for the search that produced these references. */
  sourceStatus?: SourceStatusView[];
  /** `@kb`: the numbered citations under the answer. */
  citations?: CitationView[];
}

/**
 * The answer as prose: what to copy, what to send back as history, what to
 * title a saved conversation with. Reasoning is deliberately excluded - it is
 * the model talking to itself, and feeding it back as history invites the
 * next turn to answer the thinking rather than the question.
 */
export function visibleText(message: ChatMessageView): string {
  return message.parts
    .filter((part): part is { kind: "text"; text: string } => part.kind === "text")
    .map((part) => part.text)
    .join("");
}

/**
 * Append streamed text to the run already in progress, or start a new one.
 *
 * Streaming arrives a token at a time; without this every token would become
 * its own part and the Markdown renderer would see a paragraph break between
 * each pair of them.
 */
export function appendPart(parts: MessagePart[], kind: "text" | "reasoning", text: string): void {
  const last = parts[parts.length - 1];
  if (last?.kind === kind) last.text += text;
  else parts.push({ kind, text });
}

export interface SourceStatusView {
  source: string;
  displayName: string;
  ok: boolean;
  degraded: boolean;
  hits: number;
  error: string | null;
}

export interface ReferenceView {
  title: string;
  source: string;
  url: string | null;
  hitId: string;
  /**
   * The repository or table this came out of, straight from the backend.
   * Absent when the hit IS the whole thing - a Drive document is not inside
   * anything. What `hitGroups.ts` groups on.
   */
  container?: { id: string; title: string; kind: string } | null;
  /** Identifies the document; `hitId` identifies one match within it. */
  externalId?: string | null;
}

export interface CitationView {
  n: number;
  title: string;
  url: string | null;
  source: string;
  /**
   * Which result this citation points at. Carried so the filter can mark
   * what the answer *actually used*, as opposed to what the search merely
   * returned - which is the distinction the whole list is for.
   */
  hitId?: string;
}

export interface ToolCallView {
  id: string;
  name: string;
  argsSummary: string;
  /**
   * `awaiting` is a tool that has asked and not yet been answered. It is a
   * state the card renders differently rather than a modal the editor throws
   * up, which is the whole point of owning the surface: the question appears
   * where the work is, in the transcript, with the argument in full.
   */
  status: "awaiting" | "running" | "done" | "cancelled";
  result?: string;
  /**
   * The argument that matters, untruncated - the path about to be written or
   * the command about to run. Only set while `awaiting`, because that is the
   * only time a person needs to read the whole of it before deciding.
   */
  argsFull?: string;
  /**
   * What a `write_file` changed, against what was on disk before it ran.
   *
   * Set while `awaiting`, so the change can be read before it is approved,
   * and again once it is `done`, so it can be read after. "Wrote 1240 bytes"
   * is the one summary of a write that cannot be checked.
   */
  diff?: FileDiff;
  /** The path a `write_file` touched, for the diff's heading. */
  path?: string;
}

/** One thing the next request will carry, shown as a chip above the composer. */
export interface ContextItemView {
  id: string;
  label: string;
  /**
   * `file`/`selection` are what the editor is doing right now and are sent
   * whether or not anyone asks; `pinned` is a file the user attached. Only a
   * pinned item can be removed, which is why the kind is rendered.
   */
  kind: "file" | "selection" | "pinned";
  description?: string;
}

/** One past conversation, as the history picker lists it. */
export interface ConversationSummary {
  id: string;
  title: string;
  when: number;
  messages: ChatMessageView[];
}

/** Host -> webview. */
export type HostMessage =
  | { type: "init"; signedIn: boolean; mode: Mode; agentMode: AgentMode }
  | { type: "signedIn"; value: boolean }
  | { type: "messages"; messages: ChatMessageView[] }
  | { type: "context"; items: ContextItemView[] }
  | { type: "prefill"; text: string }
  | { type: "error"; text: string };

/** Webview -> host. */
export type WebviewMessage =
  | { type: "ready" }
  | { type: "send"; text: string; mode: Mode }
  | { type: "stop" }
  | { type: "setMode"; mode: Mode }
  | { type: "setAgentMode"; mode: AgentMode }
  | { type: "signIn" }
  | { type: "openAccounts" }
  | { type: "openReference"; hitId: string; url: string | null }
  | { type: "openLink"; url: string }
  | { type: "confirmTool"; id: string; allow: boolean; always?: boolean }
  | { type: "showDiff"; id: string }
  | { type: "copyCode"; code: string }
  | { type: "insertCode"; code: string }
  | { type: "newFile"; code: string; language: string }
  | { type: "runInTerminal"; command: string }
  | { type: "copyMessage"; id: string }
  | { type: "retry"; id: string }
  | { type: "pickContext" }
  | { type: "removeContext"; id: string };

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

/**
 * The whole of the argument a person is being asked to approve.
 *
 * Deliberately not `summariseToolArgs`: that one flattens newlines and cuts at
 * 100 characters, which is right for a one-line card and wrong for the only
 * moment it matters what the command actually says.
 */
export function fullToolArgs(name: string, argsJson: string): string {
  let args: Record<string, unknown>;
  try {
    args = JSON.parse(argsJson) as Record<string, unknown>;
  } catch {
    return argsJson;
  }
  switch (name) {
    case "run_terminal":
      return String(args.command ?? "");
    case "write_file":
      return String(args.path ?? "");
    default:
      return summariseToolArgs(name, argsJson);
  }
}

function truncateOneLine(text: string, limit: number): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > limit ? `${flat.slice(0, limit)}…` : flat;
}
