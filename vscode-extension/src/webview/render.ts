import type { DiffLine, FileDiff } from "../diff.ts";
import { highlight, languageFromPath } from "../highlight.ts";
import { escapeHtml, renderAnswer } from "../markdown.ts";
import type {
  ChatMessageView,
  ContextItemView,
  Mode,
  SourceStatusView,
  ToolCallView,
} from "../webviewProtocol.ts";

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
  assistant: "Knowledge Base",
};

const TOOL_STATUS_LABEL: Record<ToolCallView["status"], string> = {
  awaiting: "Needs approval",
  running: "Running…",
  done: "Done",
  cancelled: "Cancelled",
};

/**
 * Inline SVG rather than a codicon font.
 *
 * VS Code ships codicons, but a webview only gets them by bundling the font
 * file and a stylesheet - a real dependency, for two glyphs. These are the
 * two glyphs.
 */
const AVATAR: Record<ChatMessageView["role"], string> = {
  user: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="5" r="2.6" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M2.8 14a5.2 5.2 0 0 1 10.4 0" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
  assistant:
    '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M8 1.6l1.7 4.7 4.7 1.7-4.7 1.7L8 14.4l-1.7-4.7L1.6 8l4.7-1.7z" fill="currentColor"/></svg>',
};

const MESSAGE_ACTIONS: ReadonlyArray<readonly [string, string, string]> = [
  ["copy", "Copy this answer", "M4 2h6l2 2v8H4V2zm1 1v8h6V5H9V3H5z"],
  ["retry", "Ask again", "M13 8a5 5 0 1 1-1.5-3.5M13 2v3h-3"],
];

export function renderMessage(message: ChatMessageView): string {
  const parts: string[] = [
    `<div class="message message-${message.role}" data-id="${escapeHtml(message.id)}">`,
    `<div class="message-head">` +
      `<span class="message-avatar" aria-hidden="true">${AVATAR[message.role]}</span>` +
      `<span class="message-role">${ROLE_LABEL[message.role]}${message.role === "assistant" && message.mode === "coder" ? " · coder" : ""}</span>` +
      (message.role === "assistant" && message.status !== "streaming"
        ? `<span class="message-actions">${renderMessageActions()}</span>`
        : "") +
      "</div>",
  ];

  if (message.sourceStatus?.length) {
    parts.push(renderSourceStatuses(message.sourceStatus));
  }

  if (message.references?.length) {
    parts.push(renderReferences(message.references));
  }

  // Walked in order rather than gathered by type: a tool call belongs where
  // it happened, between the sentence that led to it and the sentence written
  // once it came back. Collecting them into a list at the bottom is what made
  // an agent turn unreadable.
  const citations = message.citations ?? [];
  message.parts.forEach((part, index) => {
    if (part.kind === "tool") {
      parts.push(`<div class="tool-calls">${renderToolCall(part.call)}</div>`);
    } else if (part.kind === "reasoning") {
      parts.push(
        renderReasoning(part.text, message.status === "streaming", `${message.id}:r${index}`),
      );
    } else if (part.text.trim()) {
      // A sentence the model abandoned to call a tool is marked as abandoned.
      // Measured against the live model: it stops emitting content mid-word -
      // "що виводить приві" - and switches to tool_calls, and every delta
      // before that point does arrive. Rendered plainly, that reads as the
      // panel eating the end of a word, which is the one thing it is not.
      if (message.role !== "assistant") {
        parts.push(`<div class="message-body message-body-plain">${escapeHtml(part.text)}</div>`);
        return;
      }
      const truncated = message.parts[index + 1]?.kind === "tool" && looksTruncated(part.text);
      const rendered = renderAnswer(part.text, citations);
      parts.push(
        `<div class="message-body">${truncated ? withCutMarker(rendered) : rendered}</div>`,
      );
    }
  });

  if (message.status === "streaming" && !message.parts.length) {
    parts.push('<div class="message-body message-pending" aria-label="Working">⋯</div>');
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

/**
 * Whether the model stopped mid-thought rather than finishing.
 *
 * Terminal punctuation, a closing bracket or a code fence all mean it got to
 * the end of what it wanted to say. Anything else - a bare word, a comma, a
 * half-written one - means it did not.
 */
const FINISHED = /[.!?:;)\]}…"'`*\n]$/;

export function looksTruncated(text: string): boolean {
  const trimmed = text.trimEnd();
  return trimmed.length > 0 && !FINISHED.test(trimmed);
}

const CUT_MARKER =
  '<span class="cut-marker" title="The model stopped here to call the tool below. Nothing was lost on the way to this panel.">…</span>';

/**
 * Put the marker at the end of the last paragraph, not after it.
 *
 * `renderAnswer` returns block-level HTML, so appending the span to the
 * rendered string would drop the ellipsis onto its own line - which reads as
 * a new thought rather than as the end of the truncated one.
 */
function withCutMarker(html: string): string {
  const closing = html.lastIndexOf("</p>");
  if (closing === -1) return html + CUT_MARKER;
  return html.slice(0, closing) + CUT_MARKER + html.slice(closing);
}

function renderMessageActions(): string {
  return MESSAGE_ACTIONS.map(
    ([action, label, path]) =>
      `<button type="button" class="message-action" data-message-action="${action}" title="${label}" aria-label="${label}">` +
      `<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="${path}" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>` +
      "</button>",
  ).join("");
}

/** The suggestions offered on an empty transcript, per mode. */
const SUGGESTIONS: Record<Mode, readonly string[]> = {
  kb: [
    "Які розміри у t motor u7?",
    "Що написано в README репозиторію auth-service?",
    "Які рішення ми ухвалили щодо шасі?",
  ],
  coder: [
    "Поясни, що робить цей файл",
    "Додай тест на цю функцію",
    "Знайди, де в базі знань описано цей формат",
  ],
};

const MODE_BLURB: Record<Mode, string> = {
  kb: "Питання по Google Workspace, GitLab і внутрішній базі знань. Відповідь приходить з посиланнями на джерела, які її підтверджують.",
  coder:
    "Агент, що читає і пише файли у твоєму воркспейсі, запускає команди й шукає в базі знань — по колу, доки задача не зроблена.",
};

/**
 * What an empty transcript shows.
 *
 * Not decoration: an assistant with no history and no placeholder is a blank
 * rectangle that says nothing about what it can be asked, and the two modes
 * do genuinely different jobs. The suggestions are per mode for that reason.
 */
export function renderWelcome(mode: Mode): string {
  const chips = SUGGESTIONS[mode]
    .map(
      (suggestion) =>
        `<button type="button" class="suggestion" data-suggestion="${escapeHtml(suggestion)}">${escapeHtml(suggestion)}</button>`,
    )
    .join("");
  return (
    '<div class="welcome">' +
    '<div class="welcome-icon" aria-hidden="true">' +
    '<svg viewBox="0 0 16 16" width="28" height="28"><path d="M8 1.6l1.7 4.7 4.7 1.7-4.7 1.7L8 14.4l-1.7-4.7L1.6 8l4.7-1.7z" fill="currentColor"/></svg>' +
    "</div>" +
    `<div class="welcome-title">${mode === "kb" ? "Knowledge Base" : "Coder"}</div>` +
    `<p class="welcome-blurb">${escapeHtml(MODE_BLURB[mode])}</p>` +
    `<div class="suggestions">${chips}</div>` +
    "</div>"
  );
}

const CONTEXT_ICON: Record<ContextItemView["kind"], string> = {
  file: "M4 2h5l3 3v9H4V2zm5 0v3h3",
  selection: "M3 3h4M3 3v10M3 13h4M13 3H9M13 3v10M13 13H9",
  pinned: "M4 2h5l3 3v9H4V2zm5 0v3h3",
};

/**
 * The chips above the composer: what the next request will actually carry.
 *
 * `file` and `selection` are what the editor is doing right now and go into
 * the prompt whether or not anybody asked - `agentContext.ts` has always sent
 * them. Showing them is the honest version of that, and it is the reason only
 * a `pinned` item gets a remove button: the other two are not this panel's to
 * take away.
 */
export function renderContextItems(items: ContextItemView[]): string {
  if (!items.length) return "";
  return items
    .map((item) => {
      const removable =
        item.kind === "pinned"
          ? `<button type="button" class="context-remove" data-remove-context="${escapeHtml(item.id)}" title="Remove" aria-label="Remove ${escapeHtml(item.label)}">×</button>`
          : "";
      return (
        `<span class="context-chip context-chip-${item.kind}" title="${escapeHtml(item.description ?? item.label)}">` +
        `<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true"><path d="${CONTEXT_ICON[item.kind]}" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>` +
        `<span class="context-label">${escapeHtml(item.label)}</span>${removable}</span>`
      );
    })
    .join("");
}

/**
 * What each source did, next to the answer it did or did not contribute to.
 *
 * Three outcomes, because they mean different things and a single "failed"
 * would flatten them: a source that answered, one that answered partially
 * (degraded - it responded, but not with everything it holds), and one that
 * could not be reached at all. The last carries its reason, since "Drive
 * returned nothing" and "Drive timed out" lead somewhere different.
 */
function renderSourceStatuses(statuses: SourceStatusView[]): string {
  const chips = statuses
    .map((status) => {
      const state = !status.ok ? "unavailable" : status.degraded ? "partial" : "answered";
      const detail = status.error
        ? ` — ${escapeHtml(status.error)}`
        : ` · ${status.hits} ${status.hits === 1 ? "hit" : "hits"}`;
      return (
        `<span class="source-status source-status-${state}" title="${escapeHtml(status.displayName)}${escapeHtml(status.error ?? "")}">` +
        `${escapeHtml(status.displayName)}${detail}</span>`
      );
    })
    .join("");
  return `<div class="source-statuses">${chips}</div>`;
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

/**
 * The model's own thinking, folded away.
 *
 * `delta.reasoning` is a separate channel from `delta.content` and reads like
 * one - "The user asks to read package.json. Let me read it." Rendered as
 * ordinary prose it is indistinguishable from the answer, which is what made
 * the transcript confusing. Open while it is the only thing happening, shut
 * once the answer itself starts arriving.
 */
function renderReasoning(text: string, streaming: boolean, key: string): string {
  return (
    `<details class="reasoning" data-toggle-key="${escapeHtml(key)}"${streaming ? " open" : ""}>` +
    '<summary><span class="reasoning-icon" aria-hidden="true">' +
    '<svg viewBox="0 0 16 16" width="12" height="12"><path d="M8 1.8a4.2 4.2 0 0 0-2.6 7.5c.4.3.6.8.6 1.3v.4h4v-.4c0-.5.2-1 .6-1.3A4.2 4.2 0 0 0 8 1.8zM6 13h4M6.5 14.6h3" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
    "</span>Thinking</summary>" +
    `<div class="reasoning-body">${escapeHtml(text)}</div>` +
    "</details>"
  );
}

function renderToolCall(call: ToolCallView): string {
  const statusClass = `tool-status-${call.status}`;

  if (call.status === "awaiting") {
    // Open by default and not collapsible: this is the one state where the
    // detail is the point, and a question folded shut is a question nobody
    // answers.
    const diff =
      call.name === "write_file"
        ? `<button type="button" class="tool-approve-action" data-show-diff="${escapeHtml(call.id)}">View diff</button>`
        : "";
    return (
      `<div class="tool-call ${statusClass}" data-tool-id="${escapeHtml(call.id)}">` +
      '<div class="tool-call-head">' +
      `<span class="tool-icon" aria-hidden="true">⚙</span>` +
      `<span class="tool-name">${escapeHtml(call.name)}</span>` +
      `<span class="tool-status">${TOOL_STATUS_LABEL[call.status]}</span>` +
      "</div>" +
      // A write shows what it would change; everything else shows its
      // argument. Approving "write sorting.ts" without seeing the change is
      // approving a byte count.
      (call.diff
        ? renderDiff(call.diff, call.path, `diff:${call.id}`)
        : `<pre class="tool-approve-args" data-scroll-key="args:${escapeHtml(call.id)}">${escapeHtml(call.argsFull ?? call.argsSummary)}</pre>`) +
      '<div class="tool-approve">' +
      diff +
      `<button type="button" class="tool-approve-action tool-approve-deny" data-confirm-tool="${escapeHtml(call.id)}" data-allow="false">Cancel</button>` +
      `<button type="button" class="tool-approve-action" data-confirm-tool="${escapeHtml(call.id)}" data-allow="true" data-always="true">Allow for this chat</button>` +
      `<button type="button" class="tool-approve-action tool-approve-primary" data-confirm-tool="${escapeHtml(call.id)}" data-allow="true">Allow</button>` +
      "</div></div>"
    );
  }

  const result = call.result
    ? `<pre class="tool-result" data-scroll-key="result:${escapeHtml(call.id)}">${renderToolResultBody(call)}</pre>`
    : "";
  const diff = call.diff ? renderDiff(call.diff, call.path, `diff:${call.id}`) : "";
  return (
    `<details class="tool-call ${statusClass}" data-toggle-key="tool:${escapeHtml(call.id)}">` +
    "<summary>" +
    `<span class="tool-icon" aria-hidden="true">⚙</span>` +
    `<span class="tool-name">${escapeHtml(call.name)}</span>` +
    `<span class="tool-args">${escapeHtml(call.argsSummary)}</span>` +
    `<span class="tool-status">${TOOL_STATUS_LABEL[call.status]}</span>` +
    (call.diff ? renderDiffCount(call.diff) : "") +
    "</summary>" +
    diff +
    result +
    "</details>"
  );
}

const DIFF_PREFIX: Record<DiffLine["kind"], string> = {
  add: "+",
  remove: "-",
  context: " ",
  gap: "",
};

/** `+12 -3`, in the card's summary line, so a folded card still says how big. */
function renderDiffCount(diff: FileDiff): string {
  if (!diff.added && !diff.removed) return '<span class="diff-count">no change</span>';
  return (
    '<span class="diff-count">' +
    (diff.added ? `<span class="diff-added">+${diff.added}</span>` : "") +
    (diff.removed ? `<span class="diff-removed">−${diff.removed}</span>` : "") +
    "</span>"
  );
}

/**
 * The change itself, as a unified-style block.
 *
 * Every line is escaped - this is file content, which is the least
 * trustworthy text in the transcript: it is whatever the model just decided
 * to write, and `<script>` is a perfectly ordinary thing to put in an HTML
 * file.
 */
function renderDiff(diff: FileDiff, path: string | undefined, scrollKey?: string): string {
  if (!diff.lines.length) {
    return `<div class="diff diff-empty">${escapeHtml(path ?? "")} — written unchanged</div>`;
  }
  // `highlight` escapes every branch itself - the text never reaches the
  // output raw - so this stays as safe as the plain `escapeHtml` it replaced.
  const language = languageFromPath(path);
  const rows = diff.lines
    .map((line) =>
      line.kind === "gap"
        ? `<div class="diff-line diff-gap">⋯ ${escapeHtml(line.text)}</div>`
        : `<div class="diff-line diff-${line.kind}"><span class="diff-sign" aria-hidden="true">${DIFF_PREFIX[line.kind]}</span>${highlight(line.text, language) || "&nbsp;"}</div>`,
    )
    .join("");
  return (
    '<div class="diff">' +
    (path ? `<div class="diff-path">${escapeHtml(path)}${renderDiffCount(diff)}</div>` : "") +
    `<div class="diff-body"${scrollKey ? ` data-scroll-key="${escapeHtml(scrollKey)}"` : ""}>${rows}</div>` +
    (diff.truncated
      ? '<div class="diff-line diff-gap">⋯ too large to compare line by line</div>'
      : "") +
    "</div>"
  );
}

function truncateTitle(title: string): string {
  return title.length > 60 ? `${title.slice(0, 60)}…` : title;
}

function truncateResult(result: string): string {
  return result.length > 4000 ? `${result.slice(0, 4000)}…` : result;
}

/** Which tools hand back a file's own content, keyed to where its path lives in the card. */
const FILE_CONTENT_TOOLS: Record<string, (call: ToolCallView) => string | undefined> = {
  read_file: (call) => call.argsSummary,
};

/**
 * A tool result, coloured when it is a file's own content and a path is
 * known to pick a language from - plain otherwise.
 *
 * `read_file` is the case this exists for: its result is exactly what would
 * be open in an editor tab, and an editor tab is not plain text. Errors are
 * left alone deliberately - "Error reading file: ENOENT" run through a
 * lexer whose one structural rule is "a name before `(` is a call" adds
 * nothing and risks painting a stack trace as though it meant something.
 */
function renderToolResultBody(call: ToolCallView): string {
  const result = truncateResult(call.result ?? "");
  const pathFor = FILE_CONTENT_TOOLS[call.name];
  if (!pathFor || result.startsWith("Error")) return escapeHtml(result);
  const language = languageFromPath(pathFor(call));
  return highlight(result, language);
}
