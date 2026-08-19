"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AnswerMarkdown } from "@/components/AnswerMarkdown";
import { useCopy } from "@/i18n/LocaleProvider";
import { api } from "@/lib/api";

interface Content {
  hit_id: string;
  title: string;
  text: string;
  language: string | null;
  truncated: boolean;
}

/**
 * The full text behind a result, over the page.
 *
 * A card carries a 400-character excerpt centred on the match: enough to
 * judge relevance, not enough to use. The external permalink is the honest
 * answer for "show me the rest", but it means leaving the app — and for a
 * container-hosted GitLab or a Drive account the reader may not be signed
 * into it in this browser at all.
 *
 * A dialog rather than a route: reading a source is a detour from a result
 * list, and coming back should not mean re-running the search.
 */
export function ContentViewer({
  hitId,
  onClose,
}: {
  hitId: string;
  onClose: () => void;
}) {
  const { copy } = useCopy();
  const [content, setContent] = useState<Content | null>(null);
  const [error, setError] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .get<Content>(`/api/content/${hitId}`)
      .then((loaded) => {
        if (!cancelled) setContent(loaded);
      })
      .catch(() => {
        if (!cancelled) setError(copy.viewer.nothing);
      });
    return () => {
      cancelled = true;
    };
  }, [hitId, copy.viewer.nothing]);

  // Escape closes, and focus starts on the close button so the dialog is
  // dismissable from the keyboard before anything has loaded.
  const onKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    },
    [onClose],
  );

  useEffect(() => {
    document.addEventListener("keydown", onKeyDown);
    closeRef.current?.focus();
    // The page behind must not scroll while a full-height panel is over it.
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previous;
    };
  }, [onKeyDown]);

  // Markdown gets rendered; anything else is shown as-is in monospace,
  // because a source file that has been prettified is no longer the file.
  const isMarkdown = content?.language === "markdown";

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
      />

      <dialog
        open
        aria-label={content?.title ?? copy.viewer.loading}
        className="relative m-0 flex h-full w-full max-w-3xl flex-col border-l border-hairline bg-black p-0 text-white"
      >
        <header className="flex items-start justify-between gap-6 border-b border-hairline px-6 py-5 md:px-8">
          <div className="min-w-0">
            <p className="font-display text-[10px] uppercase tracking-[0.42em] text-white/40">
              {copy.viewer.eyebrow}
            </p>
            {/* Not uppercased: this is a file path, and case is information. */}
            <h2 className="mt-2 truncate font-display text-xl tracking-tight text-white">
              {content?.title ?? "…"}
            </h2>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            className="shrink-0 border border-hairline px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.24em] text-white/60 transition-colors duration-300 hover:border-white hover:text-white"
          >
            {copy.viewer.close}
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-6 py-6 md:px-8">
          {error && (
            <p role="alert" className="text-sm text-danger">
              {error}
            </p>
          )}

          {!error &&
            !content &&
            // Same hairline placeholders the answer panel uses while streaming.
            [80, 60, 70, 45].map((width) => (
              <span
                key={width}
                className="mb-3 block h-px animate-pulse bg-white/15"
                style={{ width: `${width}%` }}
              />
            ))}

          {content &&
            (isMarkdown ? (
              <AnswerMarkdown
                text={content.text}
                citations={[]}
                onJump={() => {}}
              />
            ) : (
              <pre className="font-mono text-[12px] leading-relaxed whitespace-pre-wrap break-words text-white/75">
                {content.text}
              </pre>
            ))}

          {content?.truncated && (
            <p className="mt-6 border-t border-hairline pt-4 font-mono text-[10px] uppercase tracking-[0.24em] text-white/35">
              {copy.viewer.truncated}
            </p>
          )}
        </div>
      </dialog>
    </div>
  );
}
