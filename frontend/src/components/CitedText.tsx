"use client";

import type { ReactNode } from "react";
import type { Citation } from "@/lib/types";

const CITATION = /\[(\d{1,3})\]/g;

/**
 * Split a run of text into nodes, turning `[n]` into a clickable reference.
 *
 * The rule that matters lives here, in one place: only citations the backend
 * verified are in `citations`, and only those become interactive. A number
 * the model invented is left as plain text — the UI cannot manufacture a link
 * to a source that was not in the prompt.
 */
export function splitCitations(
  text: string,
  citations: Citation[],
  onJump: (citation: Citation) => void,
  keyPrefix = "c",
): ReactNode[] {
  const byNumber = new Map(citations.map((citation) => [citation.n, citation]));
  const parts: ReactNode[] = [];
  let cursor = 0;
  let key = 0;

  // matchAll rather than a while-loop over exec(): no assignment inside the
  // condition, and no shared lastIndex state to reset between renders.
  for (const match of text.matchAll(CITATION)) {
    const at = match.index ?? 0;
    if (at > cursor) parts.push(text.slice(cursor, at));

    const citation = byNumber.get(Number(match[1]));
    parts.push(
      citation ? (
        <button
          key={`${keyPrefix}-${key++}`}
          type="button"
          onClick={() => onJump(citation)}
          title={`${citation.source}: ${citation.title}`}
          className="mx-0.5 border-b border-accent/50 align-baseline font-mono text-[11px] text-accent transition-colors duration-300 hover:border-accent hover:text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
        >
          {match[1]}
        </button>
      ) : (
        match[0]
      ),
    );
    cursor = at + match[0].length;
  }

  if (cursor < text.length) parts.push(text.slice(cursor));
  return parts;
}

/** Plain-text answer with `[n]` made clickable. */
export function CitedText({
  text,
  citations,
  onJump,
}: {
  text: string;
  citations: Citation[];
  onJump: (citation: Citation) => void;
}) {
  return <>{splitCitations(text, citations, onJump)}</>;
}

/** Scroll a result card into view and flash it, so the jump is followable. */
export function jumpToHit(hitId: string) {
  const element = document.getElementById(`hit-${hitId}`);
  if (!element) return;
  element.scrollIntoView({ behavior: "smooth", block: "center" });
  element.classList.remove("citation-flash");
  // Force a reflow so the animation restarts on a repeat click.
  void element.offsetWidth;
  element.classList.add("citation-flash");
}
