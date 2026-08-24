"use client";

import { useState } from "react";
import { useCopy } from "@/i18n/LocaleProvider";
import type { SourceStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Per-source outcome. Failures are shown, not hidden: a search that quietly
 * returns fewer results because a backend is down is worse than one that says
 * GitLab is unreachable, because the user has no reason to distrust it.
 *
 * Reads as one of the site's metadata rows — mono, wide uppercase, hairline
 * separation — with the status carried by a small square rather than by
 * colouring the whole chip, so a failed source does not shout over the
 * results themselves.
 *
 * Doubles as a filter chip when `onToggleFilter` is given: a source that
 * actually returned something becomes clickable, and clicking it narrows the
 * result list below to that source alone. A source with nothing to show has
 * nothing to filter to, so it stays informational rather than becoming a
 * button that does nothing.
 */
export function SourceBadge({
  status,
  cited = false,
  selected = false,
  onToggleFilter,
}: {
  status: SourceStatus;
  /** Whether the answer actually cited a result from this source. */
  cited?: boolean;
  /** Whether this is the source the result list is currently narrowed to. */
  selected?: boolean;
  /** Present only for a source worth filtering to - one with hits. */
  onToggleFilter?: () => void;
}) {
  const { copy, plural } = useCopy();
  const [open, setOpen] = useState(false);
  const sql = typeof status.detail?.sql === "string" ? status.detail.sql : null;
  const mode =
    typeof status.detail?.mode === "string" ? status.detail.mode : null;
  const warning =
    typeof status.detail?.warning === "string" ? status.detail.warning : null;
  const expandable = Boolean(status.error || sql || warning);
  const filterable = Boolean(onToggleFilter);

  const label = status.display_name || status.source;

  const dot = (
    <span
      aria-hidden="true"
      className={cn(
        "h-1.5 w-1.5",
        !status.ok
          ? "bg-danger"
          : status.degraded
            ? "bg-accent"
            : "bg-white/40",
      )}
    />
  );

  const outcome = status.ok ? (
    // A template literal, not adjacent JSX text: the line-wrapped form of
    // the latter puts a newline between {elapsed_ms} and the literal "ms",
    // and JSX turns that into a rendered space - "120 ms" instead of "120ms".
    <span className="text-white/35">
      {`${status.hits} ${plural(status.hits, copy.badge.hits)} · ${status.elapsed_ms}ms`}
    </span>
  ) : (
    <span className="text-danger">{copy.badge.unavailable}</span>
  );

  return (
    <div
      className={cn(
        "border border-hairline transition-colors duration-300",
        !status.ok && "border-danger/40",
        selected && "border-white/60",
      )}
    >
      <div className="flex items-center gap-3 px-3 py-2 font-mono text-[10px] uppercase tracking-[0.2em]">
        {filterable ? (
          <button
            type="button"
            onClick={onToggleFilter}
            aria-pressed={selected}
            title={copy.sourceFilter.onlyTitle(label)}
            className={cn(
              "flex items-center gap-3 transition-colors duration-300",
              selected ? "text-white" : "text-white/80 hover:text-white",
            )}
          >
            {dot}
            <span>{label}</span>
            {outcome}
          </button>
        ) : (
          <div className="flex items-center gap-3 text-white/80">
            {dot}
            <span>{label}</span>
            {outcome}
          </div>
        )}

        {cited && (
          <span className="text-white/50" title={copy.badge.citedTitle}>
            {copy.badge.cited}
          </span>
        )}
        {status.degraded && (
          <span className="text-accent">{copy.badge.partial}</span>
        )}
        {mode === "fallback" && (
          <span className="text-accent" title={copy.badge.fallbackTitle}>
            {copy.badge.fallback}
          </span>
        )}

        {expandable && (
          <button
            type="button"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            className="ml-auto cursor-pointer text-white/30 hover:text-white/60"
          >
            {open ? "−" : "+"}
          </button>
        )}
      </div>

      {open && (
        <div className="space-y-3 border-t border-hairline px-3 py-3">
          {status.error && (
            <p className="font-mono text-[11px] leading-relaxed break-words text-danger">
              {status.error}
            </p>
          )}
          {warning && (
            <p className="font-mono text-[11px] leading-relaxed break-words text-accent">
              {warning}
            </p>
          )}
          {sql && (
            <div>
              <p className="mb-2 font-display text-[10px] uppercase tracking-[0.28em] text-white/40">
                {copy.badge.generatedSql}
                {mode ? ` · ${mode}` : ""}
              </p>
              <pre className="overflow-x-auto border border-hairline p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-white/70">
                {sql}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
