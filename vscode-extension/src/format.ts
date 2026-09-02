import type { ChatTurn } from "./types";

/** A multi-line selection is a query, not a paragraph; the API caps at 2000. */
export function collapse(text: string): string {
  const single = text.replace(/\s+/g, " ").trim();
  return single.length > 2000 ? single.slice(0, 2000) : single;
}

// --- the chat transcript -------------------------------------------------------

/** How much of the transcript to carry. The API caps history at 20 turns. */
export const MAX_HISTORY_TURNS = 10;
const MAX_TURN_CHARS = 8000;

/**
 * A turn of the chat panel's transcript, described by what is read from it
 * rather than by importing the editor's classes - which is what lets this file
 * stay free of `vscode` and therefore testable.
 */
export interface TranscriptTurn {
  /** Present on a request turn: what the user typed. */
  readonly prompt?: string;
  /** Present on a response turn: the parts the participant streamed back. */
  readonly response?: ReadonlyArray<unknown>;
}

/**
 * The transcript, as the query planner wants it.
 *
 * This is what makes a follow-up work at all. The planner resolves "тут" and
 * "it" against the conversation and rewrites the search in the vocabulary the
 * documents use; given no history it searches for the words as typed, which
 * for a follow-up is close to searching for nothing.
 */
export function historyFor(history: readonly TranscriptTurn[]): ChatTurn[] {
  const turns: ChatTurn[] = [];
  for (const turn of history.slice(-MAX_HISTORY_TURNS)) {
    if (typeof turn.prompt === "string") {
      const prompt = turn.prompt.trim();
      if (prompt) turns.push({ role: "user", content: prompt.slice(0, MAX_TURN_CHARS) });
      continue;
    }
    // Only the prose. A response also carries references, anchors and
    // buttons, and none of those is something the model said.
    const text = (turn.response ?? []).map(markdownOf).join("").trim();
    if (text) turns.push({ role: "assistant", content: text.slice(0, MAX_TURN_CHARS) });
  }
  return turns;
}

/**
 * The text of a Markdown part, or "" for any other part.
 *
 * Checked by shape rather than by name: an anchor part also has a `value`,
 * and it holds a Uri. Asking whether that value has a string `value` of its
 * own is what separates the two.
 */
function markdownOf(part: unknown): string {
  const value = (part as { value?: { value?: unknown } } | null)?.value;
  return typeof value?.value === "string" ? value.value : "";
}
