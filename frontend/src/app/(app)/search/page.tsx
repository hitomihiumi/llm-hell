"use client";

import { useCallback, useEffect, useState } from "react";
import { AnswerPanel } from "@/components/AnswerPanel";
import { ResultCard } from "@/components/ResultCard";
import { SourceBadge } from "@/components/SourceBadge";
import { SpaceButton } from "@/components/SpaceButton";
import { useCopy } from "@/i18n/LocaleProvider";
import { api } from "@/lib/api";
import type { Source } from "@/lib/types";
import { useSearchRun } from "@/lib/useSearch";
import { cn } from "@/lib/utils";

/**
 * The layout that shows the machinery: per-source timings, the generated SQL,
 * the whole ranked list. `/chat` is the same pipeline in a conversational
 * shape; both share the SSE handling in useSearchRun.
 */
export default function SearchPage() {
  const { copy, plural } = useCopy();
  const [sources, setSources] = useState<Source[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [withAnswer, setWithAnswer] = useState(true);
  const [sourcesError, setSourcesError] = useState<string | null>(null);

  const { run, start } = useSearchRun();

  useEffect(() => {
    api
      .get<Source[]>("/api/sources")
      .then((loaded) => {
        setSources(loaded);
        setSelected(new Set(loaded.filter((s) => s.enabled).map((s) => s.key)));
      })
      .catch(() => setSourcesError(copy.search.sourcesError));
  }, [copy.search.sourcesError]);

  const toggle = useCallback((key: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  function onSubmit(formEvent: React.FormEvent) {
    formEvent.preventDefault();
    if (!query.trim()) return;
    start(query.trim(), {
      sources:
        selected.size && selected.size !== sources.length
          ? [...selected]
          : null,
      answer: withAnswer,
    });
  }

  const error = sourcesError ?? run?.error ?? null;
  const failing = run?.status.filter((entry) => !entry.ok) ?? [];

  return (
    <div className="space-y-10">
      <header>
        <p className="font-display text-[11px] uppercase tracking-[0.42em] text-white/40">
          {copy.search.eyebrow}
        </p>
        <h1 className="mt-3 font-display text-4xl font-semibold uppercase leading-[0.98] tracking-tight text-white sm:text-5xl">
          {copy.search.title}
        </h1>
      </header>

      <form onSubmit={onSubmit} className="space-y-6">
        <div className="flex items-end gap-6 border-b border-hairline pb-1 transition-colors duration-300 focus-within:border-white">
          <input
            value={query}
            onChange={(changeEvent) => setQuery(changeEvent.target.value)}
            placeholder={copy.search.placeholder}
            // biome-ignore lint/a11y/noAutofocus: the sole input on a single-purpose screen; focusing it is what every user wants first
            autoFocus
            // Display face for scale, but NOT uppercase: text-transform would
            // show something different from what was typed, which in a field
            // is disorienting even though the submitted value is unchanged.
            className="min-w-0 flex-1 bg-transparent py-4 font-display text-2xl tracking-tight text-white outline-none placeholder:text-white/20 sm:text-3xl"
          />
          <SpaceButton
            type="submit"
            variant="outline"
            size="sm"
            disabled={run?.searching || !query.trim()}
            className="mb-3 shrink-0"
          >
            {run?.searching ? copy.search.searching : copy.search.submit}
          </SpaceButton>
        </div>

        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <span className="font-display text-[10px] uppercase tracking-[0.28em] text-white/30">
            {copy.search.sources}
          </span>

          {sources.map((source) => {
            const on = selected.has(source.key);
            return (
              <button
                key={source.key}
                type="button"
                onClick={() => toggle(source.key)}
                disabled={!source.enabled}
                title={source.enabled ? undefined : copy.search.switchedOff}
                className={cn(
                  "group flex items-center gap-2 font-display text-[11px] uppercase tracking-[0.24em] transition-colors duration-300 disabled:opacity-30",
                  on ? "text-white" : "text-white/40 hover:text-white/70",
                )}
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    "h-1.5 w-1.5 border transition-colors duration-300",
                    on
                      ? "border-accent bg-accent"
                      : "border-white/30 bg-transparent",
                  )}
                />
                {source.display_name}
              </button>
            );
          })}

          <label className="ml-auto flex cursor-pointer items-center gap-2 font-display text-[10px] uppercase tracking-[0.24em] text-white/40 transition-colors duration-300 hover:text-white/70">
            <input
              type="checkbox"
              checked={withAnswer}
              onChange={(changeEvent) =>
                setWithAnswer(changeEvent.target.checked)
              }
              className="h-3 w-3 accent-[color:var(--accent)]"
            />
            {copy.search.generateAnswer}
          </label>
        </div>
      </form>

      {error && (
        <p
          role="alert"
          className="border-l-2 border-danger bg-danger-soft px-4 py-3 text-sm text-danger"
        >
          {error}
        </p>
      )}

      {run && run.status.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {run.status.map((entry) => (
            <SourceBadge key={entry.source} status={entry} />
          ))}
        </div>
      )}

      {run && withAnswer && (
        <AnswerPanel
          text={run.answer}
          reasoning={run.reasoning}
          citations={run.citations}
          streaming={run.streaming}
          model={run.model}
          stats={
            run.hitsUsed === undefined
              ? null
              : { hits_used: run.hitsUsed, hits_dropped: run.hitsDropped }
          }
        />
      )}

      {run && run.hits.length > 0 ? (
        <section>
          <p className="mb-5 font-display text-[11px] uppercase tracking-[0.42em] text-white/40">
            {run.hits.length} {plural(run.hits.length, copy.search.results)}
          </p>
          <ol className="space-y-4">
            {run.hits.map((hit, index) => (
              <ResultCard key={hit.id} hit={hit} index={index + 1} />
            ))}
          </ol>
        </section>
      ) : (
        run &&
        !run.searching && (
          <p className="border-t border-hairline pt-6 text-sm text-white/50">
            {copy.search.noResults}
            {failing.length > 0 && (
              <>
                {" "}
                {failing.length} of {run.status.length} sources could not be
                reached — the badges above say why.
              </>
            )}
          </p>
        )
      )}
    </div>
  );
}
