"use client";

import Link from "next/link";
import { useState } from "react";
import { ContentViewer } from "@/components/ContentViewer";
import { PagePreview } from "@/components/PagePreview";
import { useCopy } from "@/i18n/LocaleProvider";
import type { SearchHit } from "@/lib/types";

// Above this an excerpt stops being a caption and starts being a document.
const SNIPPET_FOLD_CHARS = 320;

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
  const [showText, setShowText] = useState(false);
  // Short excerpts read as a caption and belong in the open. Only the long
  // ones - a transcribed page, a whole README - need folding away.
  const long = (hit.snippet?.length ?? 0) > SNIPPET_FOLD_CHARS;
  // A spreadsheet is a grid, and a grid reflowed into a paragraph is a wall
  // of `B=... G=X` with its rows run together. Monospaced, unwrapped and
  // scrollable keeps one sheet row on one line, which is the only form in
  // which it can be read.
  const grid = hit.snippet_format === "grid";
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

      {/* The page itself, beside the excerpt. A claim taken from a diagram is
          not checkable against text, so the picture belongs on the card and
          not only behind a click. */}
      {hit.preview_pages ? (
        <div className="mt-3 pl-9">
          <PagePreview
            hitId={hit.id}
            pages={hit.preview_pages}
            variant="thumb"
          />
        </div>
      ) : null}

      {hit.snippet &&
        (long ? (
          /* A document read from its pages produces a wall of text - a whole
             page of pin labels, in reading order. That is worth having and
             worth checking, and it is not worth half a card by default: the
             picture above says what the result is far faster than the
             transcription does. */
          <div className="mt-3 pl-9">
            <button
              type="button"
              onClick={() => setShowText(!showText)}
              className="font-display text-[10px] uppercase tracking-[0.28em] text-white/40 transition-colors duration-300 hover:text-white"
            >
              {showText ? copy.card.hideText : copy.card.showText}
            </button>
            {showText &&
              (grid ? (
                <pre className="mt-3 overflow-x-auto whitespace-pre border border-hairline p-3 font-mono text-[11px] leading-relaxed text-white/55">
                  {hit.snippet}
                </pre>
              ) : (
                <p className="mt-3 text-sm leading-relaxed text-white/55">
                  {hit.snippet}
                </p>
              ))}
          </div>
        ) : (
          <p className="mt-3 pl-9 text-sm leading-relaxed text-white/55">
            {hit.snippet}
          </p>
        ))}

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
