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

  for (const rawLine of lines) {
    const line = rawLine.trim();

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

function renderCodeBlock(block: CodeBlock): string {
  // The code was captured before HTML-escaping ran on the rest of the
  // document, so it is escaped here on its own - once, and only once.
  const language = block.language.replace(/[^a-zA-Z0-9_+-]/g, "");
  const classAttr = language ? ` class="language-${language}"` : "";
  return `<pre><code${classAttr}>${escapeHtml(block.code.replace(/\n$/, ""))}</code></pre>`;
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
