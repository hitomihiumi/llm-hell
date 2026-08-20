"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";

/**
 * The pages a result was actually read from.
 *
 * This exists because of what the answers now claim. "The USB port is to the
 * left of the MCU" cannot be checked against a text snippet — it came from a
 * picture, so the picture is what has to be on screen. These are the same
 * renders that went into the model's prompt, served by `/api/preview`, not a
 * second rendering that might differ from what it saw.
 *
 * `loading="lazy"` and one request per page rather than a bundle of data
 * URIs: a card in a long result list should not download four JPEGs to sit
 * below the fold, and the browser caches each one on its own.
 */
export function PagePreview({
  hitId,
  pages,
  variant,
  className,
}: {
  hitId: string;
  pages: number;
  /** `thumb` is the single page a card shows; `full` is the viewer's strip. */
  variant: "thumb" | "full";
  className?: string;
}) {
  // A page that will not load is dropped rather than left as a broken frame.
  // The document is still there and its text still reads.
  const [broken, setBroken] = useState<Set<number>>(new Set());

  if (pages <= 0) return null;

  const shown = variant === "thumb" ? 1 : pages;
  const indices = Array.from({ length: shown }, (_, index) => index).filter(
    (index) => !broken.has(index),
  );
  if (indices.length === 0) return null;

  return (
    <div
      className={cn(
        variant === "thumb" ? "flex gap-2" : "flex flex-col gap-4",
        className,
      )}
    >
      {indices.map((index) => (
        <figure key={index} className={variant === "thumb" ? "m-0" : "m-0"}>
          {/* biome-ignore lint/performance/noImgElement: these are generated
              per request from a document, not static assets Next can optimise. */}
          <img
            src={`/api/preview/${encodeURIComponent(hitId)}/${index}`}
            alt={`Page ${index + 1} of the source document`}
            loading="lazy"
            onError={() => setBroken((current) => new Set(current).add(index))}
            className={cn(
              // A hairline on black, like every other framed thing here.
              "border border-hairline bg-white",
              variant === "thumb"
                ? "h-28 w-auto object-cover object-top"
                : "h-auto w-full",
            )}
          />
          {variant === "full" && (
            <figcaption className="mt-2 font-mono text-[10px] uppercase tracking-[0.24em] text-white/35">
              Page {index + 1}
            </figcaption>
          )}
        </figure>
      ))}
    </div>
  );
}
