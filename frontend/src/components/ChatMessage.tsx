"use client";

import { useMemo, useState } from "react";
import { AnswerMarkdown } from "@/components/AnswerMarkdown";
import { jumpToHit } from "@/components/CitedText";
import { ResultCard } from "@/components/ResultCard";
import { SourceBadge } from "@/components/SourceBadge";
import { useCopy } from "@/i18n/LocaleProvider";
import type { Citation } from "@/lib/types";
import type { SearchRun } from "@/lib/useSearch";

export function ChatMessage({ run }: { run: SearchRun }) {
  const { copy, plural } = useCopy();
  const [showSources, setShowSources] = useState(false);
  const [showReasoning, setShowReasoning] = useState(false);
  // Which source's hits the list below is narrowed to, or null for all of
  // them. Reset whenever the run itself changes - a filter chosen for one
  // question has nothing to do with the next one's sources.
  const [sourceFilter, setSourceFilter] = useState<string | null>(null);

  function jump(citation: Citation) {
    // The sources panel is collapsed by default in this layout, so a
    // citation has to open it before it can scroll to the card inside.
    setShowSources(true);
    requestAnimationFrame(() => jumpToHit(citation.hit_id));
  }

  const failing = run.status.filter((entry) => !entry.ok);

  // Which sources the answer actually leaned on, as opposed to every source
  // that merely returned something. A search can hit four sources and cite
  // one - showing all four as equals hides which one the answer is actually
  // standing on.
  const citedSources = useMemo(
    () => new Set(run.citations.map((citation) => citation.source)),
    [run.citations],
  );

  const visibleHits = sourceFilter
    ? run.hits.filter((hit) => hit.source === sourceFilter)
    : run.hits;

  return (
    <article className="border-t border-hairline pt-8">
      {/* The question, set as a heading rather than a chat bubble - the site
          has no bubbles. Not uppercased: these are the user's own words, and
          shouting them back is both wrong and slightly rude. */}
      <h2 className="font-display text-2xl leading-tight tracking-tight text-white sm:text-3xl">
        {run.query}
      </h2>

      <div className="mt-6">
        {run.reasoning && (
          <div className="mb-4">
            <button
              type="button"
              onClick={() => setShowReasoning(!showReasoning)}
              className="font-display text-[10px] uppercase tracking-[0.28em] text-white/40 transition-colors duration-300 hover:text-white"
            >
              {showReasoning
                ? copy.answer.hideReasoning
                : copy.answer.showReasoning}
            </button>
            {showReasoning && (
              <pre className="mt-3 max-h-64 overflow-y-auto border border-hairline p-4 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-white/45">
                {run.reasoning}
              </pre>
            )}
          </div>
        )}

        {run.error && (
          <p
            role="alert"
            className="border-l-2 border-danger bg-danger-soft px-4 py-3 text-sm text-danger"
          >
            {run.error}
          </p>
        )}

        {run.answer ? (
          <AnswerMarkdown
            text={run.answer}
            citations={run.citations}
            onJump={jump}
          />
        ) : (
          (run.searching || run.streaming) && (
            // <output> is the semantic element for a live result region, so a
            // screen reader announces this rather than sitting on a div with
            // a label no role supports.
            <output
              aria-label={copy.message.searchingLabel}
              className="block space-y-3"
            >
              <span className="block h-px w-3/4 animate-pulse bg-white/15" />
              <span className="block h-px w-1/2 animate-pulse bg-white/15" />
            </output>
          )
        )}

        {/* Where it came from. Collapsed by default — the point of this
            layout is the conversation — but never removed, because an answer
            without its sources is the thing this app exists not to give. */}
        {!run.searching && (
          <div className="mt-6">
            <button
              type="button"
              onClick={() => setShowSources(!showSources)}
              className="group flex items-center gap-2 font-display text-[10px] uppercase tracking-[0.28em] text-white/40 transition-colors duration-300 hover:text-white"
            >
              <span aria-hidden="true">{showSources ? "−" : "+"}</span>
              {run.hits.length} {plural(run.hits.length, copy.message.sources)}
              {run.hitsUsed !== undefined &&
                run.hitsDropped !== undefined &&
                run.hitsDropped > 0 &&
                copy.message.answeredFrom(run.hitsUsed)}
              {failing.length > 0 && copy.message.unavailable(failing.length)}
            </button>

            {showSources && (
              <div className="mt-4 space-y-4">
                {run.status.length > 0 && (
                  <div className="flex flex-wrap items-center gap-2">
                    {run.status.map((entry) => (
                      <SourceBadge
                        key={entry.source}
                        status={entry}
                        cited={citedSources.has(entry.source)}
                        selected={sourceFilter === entry.source}
                        // Only a source with something in it is worth
                        // filtering to - one with zero hits has nothing to
                        // narrow the list down to.
                        onToggleFilter={
                          entry.ok && entry.hits > 0
                            ? () =>
                                setSourceFilter((current) =>
                                  current === entry.source
                                    ? null
                                    : entry.source,
                                )
                            : undefined
                        }
                      />
                    ))}
                    {sourceFilter && (
                      <button
                        type="button"
                        onClick={() => setSourceFilter(null)}
                        className="font-mono text-[10px] uppercase tracking-[0.2em] text-white/40 underline decoration-white/20 underline-offset-4 transition-colors duration-300 hover:text-white"
                      >
                        {copy.sourceFilter.all}
                      </button>
                    )}
                  </div>
                )}
                {visibleHits.length > 0 && (
                  <ol className="space-y-3">
                    {visibleHits.map((hit, index) => (
                      <ResultCard key={hit.id} hit={hit} index={index + 1} />
                    ))}
                  </ol>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </article>
  );
}
