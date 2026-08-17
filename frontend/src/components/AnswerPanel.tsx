"use client";

import { useState } from "react";
import { CitedText, jumpToHit } from "@/components/CitedText";
import type { Citation } from "@/lib/types";

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

  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <div className="mb-2 flex items-center gap-2 text-xs text-muted">
        <span className="font-medium text-foreground">Answer</span>
        {model && <span>{model}</span>}
        {stats?.hits_used !== undefined && (
          <span>
            from {stats.hits_used} of{" "}
            {stats.hits_used + (stats.hits_dropped ?? 0)} results
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
          <CitedText
            text={text}
            citations={citations}
            onJump={(c) => jumpToHit(c.hit_id)}
          />
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
