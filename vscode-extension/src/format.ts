import type { Answer, ChatTurn, SearchHit, SearchResponse } from "./types";

/**
 * Turning API payloads into text.
 *
 * Kept apart from `documents.ts` because nothing in here touches the editor —
 * which makes it the part that can be tested with `node --test`, outside an
 * extension host. The rules about what an answer should say are worth testing;
 * the plumbing that opens a tab is not.
 */

/**
 * A tab label worth reading.
 *
 * The title is used as it comes wherever it can be — a GitLab hit is already
 * a path, and `services/auth/main.go` in a tab is exactly right. Only the
 * characters a path cannot hold are replaced.
 */
export function fileName(hit: SearchHit): string {
  const cleaned = (hit.title || hit.id)
    .replace(/[\\?%*:|"<>]/g, "-")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 120);
  if (!cleaned) return hit.id;
  // A name the editor can guess a language from keeps its own highlighting
  // when the API has no hint to give.
  return /\.[A-Za-z0-9]{1,8}$/.test(cleaned) ? cleaned : `${cleaned}.txt`;
}

/**
 * The answer as Markdown, with its citations resolved to links.
 *
 * `[1]` in the model's prose becomes a link to the result it refers to, so a
 * claim can be followed to its source without hunting for the number in a
 * list. Citations that resolve to nothing are left as plain text — the API
 * already drops the ones the model invented, and rendering a dead link for
 * whatever slipped through would be worse than rendering the number.
 */
export function answerMarkdown(response: SearchResponse): string {
  const answer = response.answer;
  if (!answer) return "";

  const lines: string[] = [`# ${response.query}`, ""];
  lines.push(linkCitations(answer.text, answer.citations), "");

  if (answer.citations.length) {
    lines.push("## Sources", "", ...citationLinks(answer.citations), "");
  }

  lines.push("---", "", summarise(response, answer), "");
  return lines.join("\n");
}

export function linkCitations(text: string, citations: Answer["citations"]): string {
  const byNumber = new Map(citations.map((citation) => [citation.n, citation]));
  return text.replace(/\[(\d+)\]/g, (whole, digits) => {
    const citation = byNumber.get(Number(digits));
    return citation?.url ? `[${whole}](${citation.url})` : whole;
  });
}

/**
 * The numbered source list, one Markdown line each.
 *
 * Shared between the answer document and the chat panel so a citation reads
 * the same in both. A result with no permalink - a knowledge-base row - is
 * still listed, as text: dropping it would leave a gap in the numbering, and
 * a number with nothing beside it is worse than a line that cannot be clicked.
 */
export function citationLinks(citations: Answer["citations"]): string[] {
  return citations.map((citation) => {
    const label = `${citation.title} — \`${citation.source}\``;
    return `${citation.n}. ${citation.url ? `[${label}](${citation.url})` : label}`;
  });
}

function summarise(response: SearchResponse, answer: Answer): string {
  const parts = [`${answer.hits_used} of ${response.hits.length} results`];
  if (answer.hits_dropped) parts.push(`${answer.hits_dropped} did not fit`);
  if (answer.model) parts.push(answer.model);
  // Every phrasing that ran, because a search rewritten by the model is
  // otherwise mysterious: the user typed one thing and got results for four.
  if (response.queries.length > 1) {
    parts.push(`searched: ${response.queries.map((query) => `\`${query}\``).join(", ")}`);
  }
  const failed = response.source_status.filter((status) => !status.ok);
  if (failed.length) {
    parts.push(`unavailable: ${failed.map((status) => status.source).join(", ")}`);
  }
  return `*${parts.join(" · ")}*`;
}

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
