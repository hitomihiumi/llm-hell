import { escapeHtml } from "./markdown.ts";

/**
 * A small syntax highlighter, for diff lines and code blocks.
 *
 * Not a dependency, for the same reason the Markdown renderer is not one: a
 * webview would have to bundle and audit a general highlighter, and what this
 * needs is comments, strings, numbers and keywords over the handful of
 * languages this codebase actually writes.
 *
 * It is deliberately lexical and single-line. A diff shows one line at a time
 * with the surrounding file removed, so there is no reliable way to know
 * whether a line begins inside a block comment or a template literal - and a
 * highlighter that guesses wrong paints half a file as a string. Guessing per
 * line and being occasionally plain is the honest trade.
 *
 * **Escaping is not separable from tokenising here.** Every branch escapes its
 * own text before wrapping it, and nothing reaches the output unescaped - this
 * runs on file content the model just wrote, which is the least trustworthy
 * text in the transcript.
 */

const KEYWORDS = new Set([
  // Shared across the languages this repo writes, deliberately as one set:
  // per-language keyword lists would be four times the size to colour the
  // same words, and a Python keyword appearing in TypeScript is not a
  // correctness problem, it is a colour.
  "abstract",
  "and",
  "as",
  "async",
  "await",
  "break",
  "case",
  "catch",
  "class",
  "const",
  "constructor",
  "continue",
  "def",
  "default",
  "del",
  "delete",
  "do",
  "elif",
  "else",
  "enum",
  "except",
  "export",
  "extends",
  "False",
  "finally",
  "for",
  "from",
  "func",
  "function",
  "get",
  "global",
  "if",
  "implements",
  "import",
  "in",
  "instanceof",
  "interface",
  "is",
  "lambda",
  "let",
  "match",
  "new",
  "None",
  "nonlocal",
  "not",
  "or",
  "package",
  "pass",
  "private",
  "protected",
  "public",
  "raise",
  "readonly",
  "return",
  "select",
  "set",
  "static",
  "struct",
  "super",
  "switch",
  "this",
  "throw",
  "True",
  "try",
  "type",
  "typeof",
  "var",
  "void",
  "while",
  "with",
  "yield",
]);

const LITERALS = new Set(["true", "false", "null", "undefined", "nil", "NaN", "Infinity"]);

/**
 * One pass, ordered so that the greedy things win.
 *
 * Comments and strings come first: a `//` inside a string is not a comment,
 * and a keyword inside either is not a keyword. Everything after them only
 * ever sees code.
 */
const TOKEN =
  /(\/\/[^\n]*|#[^\n]*|--[^\n]*)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)|(\b\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?\b|\b0[xX][0-9a-fA-F]+\b)|([A-Za-z_$][\w$]*)/g;

/** Languages whose `#` starts a comment. Elsewhere it is a fragment or an id. */
const HASH_COMMENTS = new Set([
  "py",
  "python",
  "sh",
  "bash",
  "shell",
  "zsh",
  "yaml",
  "yml",
  "toml",
  "ini",
  "rb",
  "ruby",
  "conf",
  "dockerfile",
  "makefile",
  "perl",
  "r",
]);

/** Languages where `--` starts a comment rather than being an operator. */
const DASH_COMMENTS = new Set(["sql", "lua", "hs", "haskell", "ada"]);

export function highlight(code: string, language = ""): string {
  const lang = language.trim().toLowerCase();
  let out = "";
  let last = 0;

  TOKEN.lastIndex = 0;
  let match = TOKEN.exec(code);
  while (match !== null) {
    const [text, comment, string, number, word] = match;

    // A `#` or `--` that this language does not treat as a comment is just
    // text, so it is passed over rather than painted.
    const skip =
      (comment !== undefined &&
        ((text.startsWith("#") && !HASH_COMMENTS.has(lang)) ||
          (text.startsWith("--") && !DASH_COMMENTS.has(lang)))) ||
      false;

    if (!skip) {
      out += escapeHtml(code.slice(last, match.index));
      if (comment !== undefined) out += span("comment", text);
      else if (string !== undefined) out += span("string", text);
      else if (number !== undefined) out += span("number", text);
      else if (word !== undefined) {
        if (KEYWORDS.has(word)) out += span("keyword", word);
        else if (LITERALS.has(word.toLowerCase())) out += span("literal", word);
        // A name followed by `(` reads as a call, which is the one structural
        // thing worth colouring without a parser.
        else if (code[TOKEN.lastIndex] === "(") out += span("call", word);
        else out += escapeHtml(word);
      }
      last = TOKEN.lastIndex;
    }
    match = TOKEN.exec(code);
  }

  return out + escapeHtml(code.slice(last));
}

function span(kind: string, text: string): string {
  return `<span class="hl-${kind}">${escapeHtml(text)}</span>`;
}

/** The language a path implies, for a diff that only knows a filename. */
export function languageFromPath(path: string | undefined): string {
  if (!path) return "";
  const name = path.split(/[\\/]/).pop() ?? "";
  if (!name.includes(".")) return name.toLowerCase();
  return (name.split(".").pop() ?? "").toLowerCase();
}
