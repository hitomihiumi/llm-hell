"use client";

import { useState } from "react";
import type { Citation } from "@/lib/types";

/**
 * Renders the answer with `[n]` turned into links to the corresponding
 * result card.
 *
 * Only citations the backend verified are interactive. A number the model
 * invented never reaches this component's `citations` list, so it renders as
 * plain text - a fabricated reference cannot become a link.
 */
function renderWithCitations(
  text: string,
  citations: Citation[],
  onJump: (citation: Citation) => void,
) {
  const byNumber = new Map(citations.map((citation) => [citation.n, citation]));
  const parts: React.ReactNode[] = [];
  const pattern = /\[(\d{1,3})\]/g;
  let cursor = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) parts.push(text.slice(cursor, match.index));
    const citation = byNumber.get(Number(match[1]));
    if (citation) {
      parts.push(
        <button
          key={`c-${key++}`}
          type="button"
          onClick={() => onJump(citation)}
          title={citation.title}
          className="mx-0.5 rounded bg-accent-soft px-1 text-xs font-medium text-accent align-baseline hover:underline"
        >
          {match[1]}
        </button>,
      );
    } else {
      parts.push(match[0]);
    }
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return parts;
}

export function AnswerPanel({
  text,
  reasoning,
  citations,
  streaming,
  model,
  stats,
}: {
  text: string;
  reasoning: string;
  citations: Citation[];
  streaming: boolean;
  model: string | null;
  stats: { hits_used?: number; hits_dropped?: number } | null;
}) {
  const [showReasoning, setShowReasoning] = useState(false);

  if (!text && !reasoning && !streaming) return null;

  function jump(citation: Citation) {
    const element = document.getElementById(`hit-${citation.hit_id}`);
    if (!element) return;
    element.scrollIntoView({ behavior: "smooth", block: "center" });
    element.classList.remove("citation-flash");
    // Force a reflow so the animation restarts on a repeat click.
    void element.offsetWidth;
    element.classList.add("citation-flash");
  }

  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <div className="mb-2 flex items-center gap-2 text-xs text-muted">
        <span className="font-medium text-foreground">Answer</span>
        {model && <span>{model}</span>}
        {stats?.hits_used !== undefined && (
          <span>
            from {stats.hits_used} of {stats.hits_used + (stats.hits_dropped ?? 0)} results
          </span>
        )}
        {streaming && <span className="text-accent">writing…</span>}
      </div>

      {reasoning && (
        <div className="mb-3">
          <button
            type="button"
            onClick={() => setShowReasoning(!showReasoning)}
            className="text-xs text-muted hover:text-foreground"
          >
            {showReasoning ? "▴ Hide reasoning" : "▾ Show reasoning"}
          </button>
          {showReasoning && (
            <pre className="mt-2 max-h-64 overflow-y-auto rounded bg-background p-2 text-xs whitespace-pre-wrap text-muted">
              {reasoning}
            </pre>
          )}
        </div>
      )}

      {text ? (
        <div className="text-sm leading-relaxed whitespace-pre-wrap">
          {renderWithCitations(text, citations, jump)}
        </div>
      ) : (
        streaming && (
          <div className="space-y-2" aria-hidden>
            <div className="h-3 w-4/5 animate-pulse rounded bg-border" />
            <div className="h-3 w-3/5 animate-pulse rounded bg-border" />
          </div>
        )
      )}
    </section>
  );
}
