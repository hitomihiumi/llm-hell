import type { CodeLanguage } from "@nmmty/dotmatrix";

const EXTENSION_TO_LANGUAGE: Record<string, CodeLanguage> = {
  ts: "typescript",
  tsx: "tsx",
  js: "javascript",
  jsx: "jsx",
  mjs: "javascript",
  cjs: "javascript",
  sh: "bash",
  bash: "bash",
  scss: "scss",
  css: "css",
  json: "json",
};

/** CodeBlock only ships highlighting for a handful of languages - anything
 * else (Python, Go, Rust, ...) still renders correctly as plain `text`. */
export function detectCodeLanguage(path: string): CodeLanguage {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return EXTENSION_TO_LANGUAGE[ext] ?? "text";
}
