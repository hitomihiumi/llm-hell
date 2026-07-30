// Lightweight .gitignore-style matcher used to filter files client-side
// before upload. Not a full gitignore spec implementation - covers the
// common cases (comments, `/` anchoring, `*`/`**` wildcards, directory-only
// trailing slash, `!` negation) which is what real-world .gitignore files
// mostly use.

export const DEFAULT_IGNORE_DIRS = [
  "node_modules",
  ".git",
  "dist",
  "build",
  "venv",
  ".venv",
  "__pycache__",
  "target",
  ".next",
  ".nuxt",
  "coverage",
  ".pytest_cache",
  ".mypy_cache",
  ".idea",
  ".vscode",
];

export const BINARY_EXTENSIONS = new Set([
  "png", "jpg", "jpeg", "gif", "bmp", "ico", "webp", "svg",
  "pdf", "zip", "tar", "gz", "7z", "rar", "bz2",
  "exe", "dll", "so", "dylib", "class", "pyc", "pyo", "wasm",
  "woff", "woff2", "ttf", "eot", "otf",
  "mp3", "mp4", "wav", "avi", "mov", "mkv",
  "bin", "dat", "db", "sqlite", "sqlite3",
  "jar", "war", "whl",
]);

export const MAX_FILE_BYTES = 1_000_000;
export const MAX_TOTAL_BYTES = 50_000_000;
export const MAX_FILES = 3000;

interface CompiledRule {
  regex: RegExp;
  negate: boolean;
  dirOnly: boolean;
}

function globToRegExp(pattern: string): RegExp {
  let re = "";
  for (let i = 0; i < pattern.length; i++) {
    const c = pattern[i];
    if (c === "*" && pattern[i + 1] === "*") {
      re += ".*";
      i++;
      if (pattern[i + 1] === "/") i++;
    } else if (c === "*") {
      re += "[^/]*";
    } else if (c === "?") {
      re += "[^/]";
    } else if (".+^${}()|[]\\".includes(c)) {
      re += "\\" + c;
    } else {
      re += c;
    }
  }
  return new RegExp(`^${re}$`);
}

export function compileIgnoreRules(gitignoreContent: string): CompiledRule[] {
  return gitignoreContent
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"))
    .map((rawLine) => {
      let line = rawLine;
      let negate = false;
      if (line.startsWith("!")) {
        negate = true;
        line = line.slice(1);
      }
      const dirOnly = line.endsWith("/");
      if (dirOnly) line = line.slice(0, -1);
      const anchored = line.startsWith("/");
      if (anchored) line = line.slice(1);

      const pattern = anchored ? line : `**/${line}`;
      return { regex: globToRegExp(pattern), negate, dirOnly };
    });
}

export function isIgnoredByRules(relativePath: string, rules: CompiledRule[]): boolean {
  let ignored = false;
  for (const rule of rules) {
    const candidates = rule.dirOnly
      ? [relativePath + "/"]
      : [relativePath, relativePath + "/"];
    const matches = candidates.some((c) => rule.regex.test(c));
    if (matches) ignored = !rule.negate;
  }
  return ignored;
}

export function isDefaultIgnored(relativePath: string): boolean {
  const parts = relativePath.split("/");
  return parts.some((part) => DEFAULT_IGNORE_DIRS.includes(part));
}

export function hasBinaryExtension(relativePath: string): boolean {
  const ext = relativePath.split(".").pop()?.toLowerCase();
  return !!ext && BINARY_EXTENSIONS.has(ext);
}

export async function looksBinaryContent(file: File): Promise<boolean> {
  const sniffSize = Math.min(file.size, 8000);
  if (sniffSize === 0) return false;
  const buffer = await file.slice(0, sniffSize).arrayBuffer();
  const bytes = new Uint8Array(buffer);
  return bytes.includes(0);
}
