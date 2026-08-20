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
 */
export function SourceBadge({ status }: { status: SourceStatus }) {
  const { copy, plural } = useCopy();
  const [open, setOpen] = useState(false);
  const sql = typeof status.detail?.sql === "string" ? status.detail.sql : null;
  const mode =
    typeof status.detail?.mode === "string" ? status.detail.mode : null;
  const warning =
    typeof status.detail?.warning === "string" ? status.detail.warning : null;
  const expandable = Boolean(status.error || sql || warning);

  return (
    <div
      className={cn(
        "border border-hairline transition-colors duration-300",
        !status.ok && "border-danger/40",
      )}
    >
      <button
        type="button"
        onClick={() => expandable && setOpen(!open)}
        aria-expanded={expandable ? open : undefined}
        className={cn(
          "flex items-center gap-3 px-3 py-2 font-mono text-[10px] uppercase tracking-[0.2em]",
          expandable ? "cursor-pointer" : "cursor-default",
        )}
      >
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
        <span className="text-white/80">
          {status.display_name || status.source}
        </span>

        {status.ok ? (
          <span className="text-white/35">
            {status.hits} {plural(status.hits, copy.badge.hits)} ·{" "}
            {status.elapsed_ms}ms
          </span>
        ) : (
          <span className="text-danger">{copy.badge.unavailable}</span>
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
          <span aria-hidden="true" className="text-white/30">
            {open ? "−" : "+"}
          </span>
        )}
      </button>

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
