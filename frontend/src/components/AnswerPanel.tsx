"use client";

import { useState } from "react";
import { AnswerMarkdown } from "@/components/AnswerMarkdown";
import { jumpToHit } from "@/components/CitedText";
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
    <section className="border border-hairline bg-black p-6 md:p-8">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-hairline pb-4">
        <h2 className="font-display text-[11px] uppercase tracking-[0.42em] text-white">
          Answer
        </h2>
        {model && (
          <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-white/35">
            {model}
          </span>
        )}
        {stats?.hits_used !== undefined && (
          <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-white/35">
            from {stats.hits_used} of{" "}
            {stats.hits_used + (stats.hits_dropped ?? 0)} results
          </span>
        )}
        {streaming && (
          <span className="ml-auto flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.2em] text-accent">
            <span
              aria-hidden="true"
              className="h-1.5 w-1.5 animate-pulse bg-accent"
            />
            Writing
          </span>
        )}
      </div>

      {reasoning && (
        <div className="mt-5">
          <button
            type="button"
            onClick={() => setShowReasoning(!showReasoning)}
            className="font-display text-[10px] uppercase tracking-[0.28em] text-white/40 transition-colors duration-300 hover:text-white"
          >
            {showReasoning ? "− Hide reasoning" : "+ Show reasoning"}
          </button>
          {showReasoning && (
            <pre className="mt-3 max-h-64 overflow-y-auto border border-hairline p-4 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-white/45">
              {reasoning}
            </pre>
          )}
        </div>
      )}

      {text ? (
        <div className="mt-5">
          <AnswerMarkdown
            text={text}
            citations={citations}
            onJump={(citation) => jumpToHit(citation.hit_id)}
          />
        </div>
      ) : (
        streaming && (
          <output
            aria-label="Writing the answer"
            className="mt-5 block space-y-3"
          >
            <span className="block h-px w-4/5 animate-pulse bg-white/15" />
            <span className="block h-px w-3/5 animate-pulse bg-white/15" />
            <span className="block h-px w-2/3 animate-pulse bg-white/15" />
          </output>
        )
      )}
    </section>
  );
}
