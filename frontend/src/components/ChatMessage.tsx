"use client";

import { useState } from "react";
import { CitedText, jumpToHit } from "@/components/CitedText";
import { ResultCard } from "@/components/ResultCard";
import { SourceBadge } from "@/components/SourceBadge";
import type { Citation } from "@/lib/types";
import type { SearchRun } from "@/lib/useSearch";

export function ChatMessage({ run }: { run: SearchRun }) {
  const [showSources, setShowSources] = useState(false);
  const [showReasoning, setShowReasoning] = useState(false);

  function jump(citation: Citation) {
    // The sources panel is collapsed by default in this layout, so a
    // citation has to open it before it can scroll to the card inside.
    setShowSources(true);
    requestAnimationFrame(() => jumpToHit(citation.hit_id));
  }

  const failing = run.status.filter((entry) => !entry.ok);

  return (
    <div className="space-y-4">
      {/* The question */}
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-accent-soft px-4 py-2.5 text-sm text-foreground">
          {run.query}
        </div>
      </div>

      {/* The answer */}
      <div className="space-y-2">
        {run.reasoning && (
          <div>
            <button
              type="button"
              onClick={() => setShowReasoning(!showReasoning)}
              className="text-xs text-muted hover:text-foreground"
            >
              {showReasoning ? "▴ Hide reasoning" : "▾ Show reasoning"}
            </button>
            {showReasoning && (
              <pre className="mt-2 max-h-64 overflow-y-auto rounded-lg bg-surface p-3 text-xs whitespace-pre-wrap text-muted">
                {run.reasoning}
              </pre>
            )}
          </div>
        )}

        {run.error && (
          <p
            role="alert"
            className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger"
          >
            {run.error}
          </p>
        )}

        {run.answer ? (
          <div className="text-sm leading-relaxed whitespace-pre-wrap">
            <CitedText
              text={run.answer}
              citations={run.citations}
              onJump={jump}
            />
          </div>
        ) : (
          (run.searching || run.streaming) && (
            // <output> is the semantic element for a live result region, so a
            // screen reader announces this rather than sitting on a div with
            // a label no role supports.
            <output aria-label="Searching" className="block space-y-2">
              <div className="h-3 w-3/4 animate-pulse rounded bg-border" />
              <div className="h-3 w-1/2 animate-pulse rounded bg-border" />
            </output>
          )
        )}

        {/* Where it came from. Collapsed by default - the point of this
            layout is the conversation - but never removed, because an answer
            without its sources is the thing this app exists not to give. */}
        {!run.searching && (
          <div className="pt-1">
            <button
              type="button"
              onClick={() => setShowSources(!showSources)}
              className="text-xs text-muted hover:text-foreground"
            >
              {showSources ? "▴" : "▾"} {run.hits.length}{" "}
              {run.hits.length === 1 ? "source" : "sources"}
              {run.hitsUsed !== undefined &&
                run.hitsDropped !== undefined &&
                run.hitsDropped > 0 &&
                ` · answered from ${run.hitsUsed}`}
              {failing.length > 0 && ` · ${failing.length} unavailable`}
            </button>

            {showSources && (
              <div className="mt-3 space-y-3">
                {run.status.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {run.status.map((entry) => (
                      <SourceBadge key={entry.source} status={entry} />
                    ))}
                  </div>
                )}
                {run.hits.length > 0 && (
                  <ol className="space-y-2">
                    {run.hits.map((hit, index) => (
                      <ResultCard key={hit.id} hit={hit} index={index + 1} />
                    ))}
                  </ol>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
