/**
 * Clean up LaTeX math the model sometimes emits so it reads as plain text.
 *
 * The answer model is instructed not to use LaTeX, but it still occasionally
 * writes $\varnothing$ or $\times$. Rather than ship a full math renderer
 * for a handful of symbols, we unwrap inline math and map the common
 * commands to their Unicode equivalents.
 */

const LATEX_REPLACEMENTS: Record<string, string> = {
  varnothing: "⌀",
  emptyset: "∅",
  approx: "≈",
  sim: "∼",
  times: "×",
  cdot: "·",
  pm: "±",
  mp: "∓",
  leq: "≤",
  le: "≤",
  geq: "≥",
  ge: "≥",
  neq: "≠",
  ne: "≠",
  degree: "°",
  alpha: "α",
  beta: "β",
  gamma: "γ",
  delta: "δ",
  mu: "µ",
  Omega: "Ω",
  omega: "ω",
  Sigma: "Σ",
  sigma: "σ",
  theta: "θ",
  phi: "φ",
  Phi: "Φ",
  pi: "π",
  infty: "∞",
  infinity: "∞",
  ldots: "…",
  dots: "…",
};

/**
 * Unwrap inline/block math delimiters, strip \text{...} wrappers, and replace
 * known LaTeX commands with Unicode. Unknown commands are left as-is so the
 * text is still readable and we do not silently hide information.
 */
export function sanitizeAnswerText(text: string): string {
  return (
    text
      // Block math, then inline math. Keep the body.
      .replace(/\$\$([^$]*?)\$\$/g, "$1")
      .replace(/\$([^$]*?)\$/g, "$1")
      // \text{foo} -> foo
      .replace(/\\text\{([^}]*)\}/g, "$1")
      // \circ in superscript -> °
      .replace(/\^\{\\circ\}/g, "°")
      .replace(/\^\\circ/g, "°")
      // Known commands; unknown ones stay unchanged.
      .replace(/\\([a-zA-Z]+)/g, (match, command) => {
        return LATEX_REPLACEMENTS[command] ?? match;
      })
  );
}
