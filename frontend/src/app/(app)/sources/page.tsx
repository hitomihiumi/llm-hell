"use client";

import { useCallback, useEffect, useState } from "react";
import { SpaceButton } from "@/components/SpaceButton";
import { ApiError, api } from "@/lib/api";
import type { Source } from "@/lib/types";
import { cn } from "@/lib/utils";

interface HealthOut {
  key: string;
  ok: boolean;
  checked_at: string;
  result: Record<string, unknown>;
}

export default function SourcesPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [checking, setChecking] = useState<string | null>(null);
  const [health, setHealth] = useState<Record<string, HealthOut>>({});
  const [notice, setNotice] = useState<string | null>(null);

  // useCallback so it is a stable dependency of the effect below, rather
  // than a new function on every render.
  const load = useCallback(
    () =>
      api
        .get<Source[]>("/api/sources")
        .then(setSources)
        .catch(() => setNotice("Could not load sources.")),
    [],
  );

  useEffect(() => {
    load();
  }, [load]);

  async function check(key: string) {
    setChecking(key);
    setNotice(null);
    try {
      const result = await api.post<HealthOut>(`/api/sources/${key}/check`);
      setHealth((current) => ({ ...current, [key]: result }));
      await load();
    } catch (caught) {
      setNotice(
        caught instanceof ApiError && caught.status === 403
          ? "Checking a source requires an admin account."
          : "The check failed.",
      );
    } finally {
      setChecking(null);
    }
  }

  async function toggle(source: Source) {
    setNotice(null);
    try {
      await api.patch(`/api/sources/${source.key}`, {
        enabled: !source.enabled,
      });
      await load();
    } catch (caught) {
      setNotice(
        caught instanceof ApiError && caught.status === 403
          ? "Changing a source requires an admin account."
          : "The update failed.",
      );
    }
  }

  return (
    <div className="space-y-10">
      <header>
        <p className="font-display text-[11px] uppercase tracking-[0.42em] text-white/40">
          Configuration
        </p>
        <h1 className="mt-3 font-display text-4xl font-semibold uppercase leading-[0.98] tracking-tight text-white sm:text-5xl">
          Sources
        </h1>
        <p className="mt-4 max-w-xl text-sm leading-relaxed text-white/55">
          What a source can actually do is not knowable from configuration —
          whether a GitLab instance supports code search, for instance. Run a
          check to ask its server directly.
        </p>
      </header>

      {notice && (
        <p
          role="alert"
          className="border-l-2 border-danger bg-danger-soft px-4 py-3 text-sm text-danger"
        >
          {notice}
        </p>
      )}

      {/* One row per source, in the site's stacked-rule style rather than as
          separate cards — these are a list of settings, not a gallery. */}
      <ul className="border-t border-hairline">
        {sources.map((source) => {
          const latest =
            health[source.key]?.result ?? source.last_check_result ?? null;
          const tools = Array.isArray(latest?.tools)
            ? (latest.tools as string[])
            : null;
          const ok = latest ? Boolean(latest.ok) : null;

          return (
            <li key={source.key} className="border-b border-hairline py-6">
              <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
                <span
                  aria-hidden="true"
                  className={cn(
                    "h-1.5 w-1.5",
                    source.enabled ? "bg-accent" : "bg-white/20",
                  )}
                />

                <h2 className="font-display text-2xl uppercase tracking-tight text-white">
                  {source.display_name}
                </h2>

                <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-white/35">
                  {source.kind}
                </span>
                <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-white/35">
                  weight {source.weight}
                </span>
                <span
                  className={cn(
                    "font-mono text-[10px] uppercase tracking-[0.2em]",
                    source.enabled ? "text-white/60" : "text-white/30",
                  )}
                >
                  {source.enabled ? "enabled" : "disabled"}
                </span>

                <div className="ml-auto flex gap-3">
                  <SpaceButton
                    variant="ghost"
                    size="sm"
                    withArrow={false}
                    onClick={() => toggle(source)}
                    className="border-hairline text-white/70"
                  >
                    {source.enabled ? "Disable" : "Enable"}
                  </SpaceButton>
                  <SpaceButton
                    variant="outline"
                    size="sm"
                    withArrow={false}
                    disabled={checking === source.key}
                    onClick={() => check(source.key)}
                  >
                    {checking === source.key ? "Checking" : "Check"}
                  </SpaceButton>
                </div>
              </div>

              {latest && (
                <div className="mt-4 space-y-2 pl-6">
                  <p className="font-mono text-[10px] uppercase tracking-[0.2em]">
                    <span className={ok ? "text-accent" : "text-danger"}>
                      {ok ? "reachable" : "unreachable"}
                    </span>
                    {source.last_checked_at && (
                      <span className="text-white/30">
                        {" · checked "}
                        {new Date(source.last_checked_at).toLocaleString()}
                      </span>
                    )}
                  </p>

                  {typeof latest.error === "string" && (
                    <p className="font-mono text-[11px] leading-relaxed break-words text-danger">
                      {latest.error}
                    </p>
                  )}
                  {typeof latest.warning === "string" && (
                    <p className="font-mono text-[11px] leading-relaxed break-words text-accent">
                      {latest.warning}
                    </p>
                  )}

                  {tools && (
                    <details className="group">
                      <summary className="cursor-pointer font-display text-[10px] uppercase tracking-[0.28em] text-white/40 transition-colors duration-300 hover:text-white">
                        {tools.length} tools exposed
                      </summary>
                      <p className="mt-2 font-mono text-[11px] leading-relaxed break-words text-white/40">
                        {tools.join(", ")}
                      </p>
                    </details>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
