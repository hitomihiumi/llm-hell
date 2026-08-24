import { escapeHtml, renderAnswer } from "../markdown.ts";
import type { ChatMessageView, ToolCallView } from "../webviewProtocol.ts";

/**
 * Turning one transcript entry into HTML, as a string.
 *
 * Kept apart from `main.ts` on purpose: this file touches no DOM API at all,
 * so `node --test` can check exactly what a streaming answer, a failed tool
 * call or a 404'd reference renders as - without a browser, and without the
 * one thing that would make those checks expensive, which is standing up a
 * webview to look at pixels.
 *
 * Every dynamic value that is not already-rendered Markdown goes through
 * `escapeHtml` before it reaches a template string. A tool's argument
 * summary, a reference title, a source name - all of it is either the
 * user's own words or a document this application indexed, and none of it
 * is a safe place to trust with raw HTML.
 */

const ROLE_LABEL: Record<ChatMessageView["role"], string> = {
  user: "You",
  assistant: "Assistant",
};

const TOOL_STATUS_LABEL: Record<ToolCallView["status"], string> = {
  running: "Running…",
  done: "Done",
  cancelled: "Cancelled",
};

export function renderMessage(message: ChatMessageView): string {
  const parts: string[] = [
    `<div class="message message-${message.role}" data-id="${escapeHtml(message.id)}">`,
    `<div class="message-role">${ROLE_LABEL[message.role]}${message.mode === "coder" ? " · @coder" : ""}</div>`,
  ];

  if (message.references?.length) {
    parts.push(renderReferences(message.references));
  }

  if (message.text) {
    parts.push(
      message.role === "assistant"
        ? `<div class="message-body">${renderAnswer(message.text, message.citations ?? [])}</div>`
        : `<div class="message-body message-body-plain">${escapeHtml(message.text)}</div>`,
    );
  } else if (message.status === "streaming" && !message.toolCalls?.length) {
    parts.push('<div class="message-body message-pending" aria-label="Working">⋯</div>');
  }

  if (message.toolCalls?.length) {
    parts.push(renderToolCalls(message.toolCalls));
  }

  if (message.citations?.length) {
    parts.push(renderCitations(message.citations));
  }

  if (message.status === "error" && message.error) {
    parts.push(`<div class="message-error">${escapeHtml(message.error)}</div>`);
  }

  if (message.status === "streaming") {
    parts.push('<span class="cursor" aria-hidden="true"></span>');
  }

  parts.push("</div>");
  return parts.join("");
}

function renderReferences(references: ChatMessageView["references"]): string {
  const chips = (references ?? [])
    .map(
      (reference) =>
        `<button type="button" class="chip" data-open-reference="${escapeHtml(reference.hitId)}" data-url="${escapeHtml(reference.url ?? "")}" title="${escapeHtml(reference.title)}">` +
        `<span class="chip-source">${escapeHtml(reference.source)}</span>${escapeHtml(truncateTitle(reference.title))}` +
        "</button>",
    )
    .join("");
  return `<div class="references">${chips}</div>`;
}

function renderCitations(citations: ChatMessageView["citations"]): string {
  // A `<ul>`, deliberately, with the number written out by hand: an `<ol>`
  // numbers its own items 1, 2, 3..., which collided with the citation's own
  // number and rendered "1. 1." for the first entry. The two are not always
  // the same thing either - `citation.n` is the number the answer's `[n]`
  // actually uses, and the API already drops the numbers a model invented,
  // so a real citation list can have gaps a plain ordinal would paper over.
  const rows = (citations ?? [])
    .map((citation) => {
      const label = `${escapeHtml(citation.title)} <span class="citation-source">${escapeHtml(citation.source)}</span>`;
      return `<li>${citation.n}. ${citation.url ? `<a href="${escapeHtml(citation.url)}" data-external="true">${label}</a>` : label}</li>`;
    })
    .join("");
  return `<ul class="citations">${rows}</ul>`;
}

function renderToolCalls(calls: ToolCallView[]): string {
  return `<div class="tool-calls">${calls.map(renderToolCall).join("")}</div>`;
}

function renderToolCall(call: ToolCallView): string {
  const statusClass = `tool-status-${call.status}`;
  const result = call.result
    ? `<pre class="tool-result">${escapeHtml(truncateResult(call.result))}</pre>`
    : "";
  return (
    `<details class="tool-call ${statusClass}">` +
    "<summary>" +
    `<span class="tool-icon" aria-hidden="true">⚙</span>` +
    `<span class="tool-name">${escapeHtml(call.name)}</span>` +
    `<span class="tool-args">${escapeHtml(call.argsSummary)}</span>` +
    `<span class="tool-status">${TOOL_STATUS_LABEL[call.status]}</span>` +
    "</summary>" +
    result +
    "</details>"
  );
}

function truncateTitle(title: string): string {
  return title.length > 60 ? `${title.slice(0, 60)}…` : title;
}

function truncateResult(result: string): string {
  return result.length > 4000 ? `${result.slice(0, 4000)}…` : result;
}
