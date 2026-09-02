/**
 * A small, safe Markdown-to-HTML renderer, for the webview.
 *
 * Not a dependency, deliberately. Pulling in `markdown-it` or similar for a
 * webview means bundling and auditing a renderer with a much larger surface
 * than anything the backend ever sends — this app's answers are prose,
 * citations, a handful of code fences and the occasional table of sources. A
 * renderer built for exactly that is auditable in one read, which a general
 * one is not, and this is HTML being injected into a page.
 *
 * Escaping happens first and unconditionally, on the whole input, before any
 * markup is recognised. Nothing constructed here can smuggle a tag through -
 * a title containing `<script>` becomes text, not a script, because by the
 * time any pattern below runs, `<` is already `&lt;`.
 */

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** A fenced code block, pulled out before anything else touches the text. */
interface CodeBlock {
  language: string;
  code: string;
}

const FENCE = /```([a-zA-Z0-9_+-]*)\n([\s\S]*?)```/g;
const INLINE_CODE = /`([^`\n]+)`/g;
const BOLD = /\*\*([^*]+)\*\*/g;
const ITALIC = /(?<!\*)\*([^*\n]+)\*(?!\*)/g;
// The label allows one level of nested brackets - `[[1]](url)` is exactly
// what a linked citation looks like, since its visible text is `[1]` and
// that text keeps its own brackets. A plain `[^\]]+` label stops at the
// citation's own inner `]` and never matches the link at all.
const LINK = /\[((?:[^[\]]|\[[^[\]]*\])+)\]\((https?:\/\/[^\s)]+)\)/g;
const HEADING = /^(#{1,4})\s+(.+)$/;
const BULLET = /^[-*]\s+(.+)$/;

/**
 * One row's cells, with the optional outer pipes dropped.
 *
 * `\|` is a literal pipe in GFM rather than a cell boundary, so the split
 * skips it and then unescapes it. `escapeHtml` has already run over the
 * source by this point and leaves backslashes alone, so it is still here to
 * be read.
 */
function splitRow(line: string): string[] {
  const inner = line.trim().replace(/^[|]/, "").replace(/[|]$/, "");
  return inner.split(/(?<!\\)[|]/).map((cell) => cell.replace(/\\[|]/g, "|").trim());
}

/**
 * The alignments a delimiter row declares - `|---|:---:|---:|` - or nothing
 * at all when the line is not a delimiter row.
 *
 * Returning `undefined` is what tells the caller the line above was never a
 * table header, which is the whole test: a paragraph mentioning `a | b` has
 * no such row under it and stays a paragraph.
 */
function tableAlignments(line: string): Array<"left" | "center" | "right" | null> | undefined {
  if (!line.includes("|") && !line.includes("-")) return undefined;
  const cells = splitRow(line);
  if (!cells.length) return undefined;
  const alignments: Array<"left" | "center" | "right" | null> = [];
  for (const cell of cells) {
    if (!/^:?-+:?$/.test(cell)) return undefined;
    const left = cell.startsWith(":");
    const right = cell.endsWith(":");
    alignments.push(left && right ? "center" : right ? "right" : left ? "left" : null);
  }
  return alignments;
}

function alignAttribute(alignment: "left" | "center" | "right" | null | undefined): string {
  return alignment ? ` style="text-align:${alignment}"` : "";
}

/**
 * A GFM table.
 *
 * The header decides the width: a short row is padded with empty cells and a
 * long one is cut, which is what GFM says and, more to the point, keeps the
 * columns lined up under their headings when a model miscounts pipes.
 */
function renderTable(
  header: string,
  alignments: Array<"left" | "center" | "right" | null>,
  rows: string[],
): string {
  const headings = splitRow(header);
  const head = headings
    .map((cell, column) => `<th${alignAttribute(alignments[column])}>${inline(cell)}</th>`)
    .join("");
  const body = rows
    .map((row) => {
      const cells = splitRow(row);
      const tds = headings
        .map(
          (_, column) =>
            `<td${alignAttribute(alignments[column])}>${inline(cells[column] ?? "")}</td>`,
        )
        .join("");
      return `<tr>${tds}</tr>`;
    })
    .join("");
  return `<div class="md-table-wrap"><table class="md-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

/**
 * Turn Markdown into HTML fragments, safe to set as a message's innerHTML.
 *
 * Line-oriented rather than a real parser: paragraphs are blank-line
 * separated, a run of bullet lines becomes one list, a heading is its own
 * line. That covers everything this app's answers and tool output actually
 * produce, and nothing beyond it needs to be right.
 */
export function renderMarkdown(source: string): string {
  const blocks: CodeBlock[] = [];
  const withPlaceholders = source.replace(FENCE, (_match, language: string, code: string) => {
    const index = blocks.length;
    blocks.push({ language, code });
    // Newline-delimited, not space-delimited: a leading/trailing newline
    // guarantees the placeholder becomes its own line once split below, no
    // matter what sits on either side of the fence in the source. A
    // space-delimited version does not - and does not survive `trim()`
    // either, which is exactly the bug this replaced.
    return `\nCODEBLOCK_PLACEHOLDER_${index}\n`;
  });

  const escaped = escapeHtml(withPlaceholders);
  const lines = escaped.split("\n");
  const html: string[] = [];
  let paragraph: string[] = [];
  let list: string[] = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      html.push(`<p>${inline(paragraph.join(" "))}</p>`);
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list.length) {
      html.push(`<ul>${list.map((item) => `<li>${inline(item)}</li>`).join("")}</ul>`);
      list = [];
    }
  };

  for (let index = 0; index < lines.length; index++) {
    const line = lines[index].trim();

    const codePlaceholder = /^CODEBLOCK_PLACEHOLDER_(\d+)$/.exec(line);
    if (codePlaceholder) {
      flushParagraph();
      flushList();
      const block = blocks[Number(codePlaceholder[1])];
      if (block) html.push(renderCodeBlock(block));
      continue;
    }

    if (!line) {
      flushParagraph();
      flushList();
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      flushParagraph();
      flushList();
      const level = heading[1].length;
      html.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }

    // A table is the one construct here that needs to see the next line
    // before it can commit: `| a | b |` is only a header if a delimiter row
    // follows it, and is otherwise an ordinary paragraph that happens to
    // contain pipes. Looking ahead is why this loop is indexed.
    const alignments =
      line.includes("|") && index + 1 < lines.length
        ? tableAlignments(lines[index + 1].trim())
        : undefined;
    if (alignments) {
      flushParagraph();
      flushList();
      const rows: string[] = [];
      let next = index + 2;
      while (next < lines.length && lines[next].trim().includes("|")) {
        rows.push(lines[next].trim());
        next++;
      }
      html.push(renderTable(line, alignments, rows));
      index = next - 1;
      continue;
    }

    const bullet = BULLET.exec(line);
    if (bullet) {
      flushParagraph();
      list.push(bullet[1]);
      continue;
    }

    flushList();
    paragraph.push(line);
  }
  flushParagraph();
  flushList();

  return html.join("\n");
}

/**
 * Languages where "run this" is a coherent offer.
 *
 * A fence with no language is included: a model writing a bare ``` block in
 * answer to "how do I build this" is almost always writing a command, and the
 * action only ever *types* it into a terminal - it never runs it - so the
 * cost of offering it on something that turns out to be prose is a button
 * nobody presses.
 */
const SHELL_LANGUAGES = new Set([
  "",
  "bash",
  "cmd",
  "console",
  "powershell",
  "ps1",
  "sh",
  "shell",
  "zsh",
]);

export function isShellLanguage(language: string): boolean {
  return SHELL_LANGUAGES.has(language.trim().toLowerCase());
}

/** The action buttons on a code block, as `[action, label, svg path]`. */
const CODE_ACTIONS: ReadonlyArray<readonly [string, string, string]> = [
  ["copy", "Copy", "M4 2h6l2 2v8H4V2zm1 1v8h6V5H9V3H5z"],
  ["insert", "Insert at cursor", "M8 2v12M3 7l5-5 5 5"],
  ["new-file", "Create new file", "M4 2h5l3 3v9H4V2zm5 0v3h3M6 8h4M6 11h4"],
  ["terminal", "Run in terminal", "M2 3h12v10H2V3zm2 2l3 3-3 3m5 0h4"],
];

function renderCodeBlock(block: CodeBlock): string {
  // The code was captured before HTML-escaping ran on the rest of the
  // document, so it is escaped here on its own - once, and only once.
  const language = block.language.replace(/[^a-zA-Z0-9_+-]/g, "");
  const classAttr = language ? ` class="language-${language}"` : "";
  const code = escapeHtml(block.code.replace(/\n$/, ""));

  const buttons = CODE_ACTIONS.filter(
    ([action]) => action !== "terminal" || isShellLanguage(language),
  )
    .map(
      ([action, label, path]) =>
        `<button type="button" class="code-action" data-code-action="${action}" title="${label}" aria-label="${label}">` +
        `<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="${path}" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>` +
        "</button>",
    )
    .join("");

  // The language is carried as an attribute as well as shown, because
  // "create a new file from this" needs to know what to open it as, and the
  // `language-x` class on the <code> is the webview's business rather than
  // something the click handler should be parsing back out.
  return (
    `<div class="code-block" data-language="${language}">` +
    `<div class="code-block-header"><span class="code-block-language">${language || "text"}</span>` +
    `<div class="code-block-actions">${buttons}</div></div>` +
    `<pre><code${classAttr}>${code}</code></pre>` +
    "</div>"
  );
}

/** Bold, italic, inline code and links, within one already-escaped line. */
function inline(text: string): string {
  // Inline code first, and its contents are excluded from every later
  // pass - **bold** written inside a code span must stay literal asterisks,
  // not become a nested <strong>.
  const spans: string[] = [];
  let withPlaceholders = text.replace(INLINE_CODE, (_match, code: string) => {
    const index = spans.length;
    spans.push(`<code>${code}</code>`);
    return ` SPAN${index} `;
  });

  withPlaceholders = withPlaceholders
    .replace(LINK, (_match, label: string, url: string) => `<a href="${url}">${label}</a>`)
    .replace(BOLD, "<strong>$1</strong>")
    .replace(ITALIC, "<em>$1</em>");

  return withPlaceholders.replace(
    / SPAN(\d+) /g,
    (_match, index: string) => spans[Number(index)] ?? "",
  );
}

/**
 * `[1]` -> a link to the citation it names, the same rule `format.ts` applies
 * to the answer document. Run before `renderMarkdown` sees the text, because
 * afterward the numbers are inside already-escaped HTML and a citation whose
 * label happened to contain a bracket would be at risk of double-processing.
 */
export function linkCitationsForWebview(
  text: string,
  citations: ReadonlyArray<{ n: number; url: string | null }>,
): string {
  const byNumber = new Map(citations.map((citation) => [citation.n, citation]));
  return text.replace(/\[(\d+)\]/g, (whole, digits: string) => {
    const citation = byNumber.get(Number(digits));
    return citation?.url ? `[${whole}](${citation.url})` : whole;
  });
}

/**
 * The one call the webview actually makes: citations linked, then rendered.
 * Order matters - citation linking has to run on the raw text, before
 * escaping turns `[1]` into something the link pattern no longer recognises
 * as bracketed at all in some edge case, and before a code fence's own
 * bracket-shaped contents could be mistaken for a citation.
 */
export function renderAnswer(
  text: string,
  citations: ReadonlyArray<{ n: number; url: string | null }>,
): string {
  return renderMarkdown(linkCitationsForWebview(text, citations));
}
