"use client";

import Link from "next/link";
import { useState } from "react";
import { ContentViewer } from "@/components/ContentViewer";
import { useCopy } from "@/i18n/LocaleProvider";
import type { SearchHit } from "@/lib/types";

function formatDate(value: string | null): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleDateString();
}

/**
 * Modelled on the site's CaseCard: a hairline box on pure black, metadata in
 * wide uppercase mono, and a rule that draws itself along the bottom edge on
 * hover. No raised grey panel — the border does the separating.
 */
export function ResultCard({ hit, index }: { hit: SearchHit; index: number }) {
  const { copy } = useCopy();
  const [viewing, setViewing] = useState(false);
  const date = formatDate(hit.timestamp);
  // Internal record links route inside the app; everything else is an
  // external permalink and opens in a new tab.
  const internal = hit.url?.startsWith("/") ?? false;

  // Display face and tracking, but NOT uppercase. The site uppercases copy it
  // wrote; this is data. A file path is case-sensitive, so rendering
  // `search/federation.py` as `SEARCH/FEDERATION.PY` destroys information and
  // makes it uncopyable by eye.
  const titleClasses =
    "font-display text-xl leading-tight tracking-tight text-white transition-transform duration-500 ease-out-expo group-hover:translate-x-1";

  return (
    <li
      id={`hit-${hit.id}`}
      className="group relative scroll-mt-24 border border-hairline bg-black p-6 transition-colors duration-500 hover:bg-white/[0.02]"
    >
      <div className="flex items-baseline gap-4">
        <span className="font-mono text-[10px] tabular-nums tracking-[0.2em] text-white/35">
          {String(index).padStart(2, "0")}
        </span>

        {hit.url ? (
          internal ? (
            <Link href={hit.url} className={titleClasses}>
              {hit.title}
            </Link>
          ) : (
            <a
              href={hit.url}
              target="_blank"
              rel="noreferrer noopener"
              className={titleClasses}
            >
              {hit.title}
            </a>
          )
        ) : (
          // No link rather than a dead one: some sources genuinely have no
          // addressable location for a hit.
          <span className="font-display text-xl leading-tight tracking-tight text-white/70">
            {hit.title}
          </span>
        )}
      </div>

      {hit.snippet && (
        <p className="mt-3 pl-9 text-sm leading-relaxed text-white/55">
          {hit.snippet}
        </p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 pl-9 font-mono text-[10px] uppercase tracking-[0.24em] text-white/35">
        <span className="border border-hairline px-1.5 py-0.5 text-white/50">
          {copy.card.kinds[hit.kind] ?? hit.kind}
        </span>
        <span>{hit.source}</span>
        {hit.author && <span>{hit.author}</span>}
        {date && <span>{date}</span>}
      {/* Reading the source without leaving the results. The excerpt above
            is centred on the match and cut to 400 characters, which answers
            "is this relevant" and not "what does it say". */}
        <button
          type="button"
          onClick={() => setViewing(true)}
          className="ml-auto border border-hairline px-2 py-0.5 text-white/50 transition-colors duration-300 hover:border-white hover:text-white"
        >
          {copy.card.view}
        </button>
      </div>

      {viewing && (
        <ContentViewer hitId={hit.id} onClose={() => setViewing(false)} />
      )}

      <span
        aria-hidden="true"
        className="absolute inset-x-0 bottom-0 h-px origin-left scale-x-0 bg-white transition-transform duration-700 ease-out-expo group-hover:scale-x-100"
      />
    </li>
  );
}
