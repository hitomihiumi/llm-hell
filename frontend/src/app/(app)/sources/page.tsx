"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Source } from "@/lib/types";

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

  const load = () =>
    api
      .get<Source[]>("/api/sources")
      .then(setSources)
      .catch(() => setNotice("Could not load sources."));

  useEffect(() => {
    load();
  }, []);

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
      await api.patch(`/api/sources/${source.key}`, { enabled: !source.enabled });
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
    <div className="space-y-4">
      <header>
        <h1 className="text-lg font-semibold">Sources</h1>
        <p className="text-sm text-muted">
          What a source can actually do is not knowable from configuration — whether a
          GitLab instance supports code search, for instance. Run a check to ask its
          server directly.
        </p>
      </header>

      {notice && (
        <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {notice}
        </p>
      )}

      <ul className="space-y-3">
        {sources.map((source) => {
          const latest = health[source.key]?.result ?? source.last_check_result ?? null;
          const tools = Array.isArray(latest?.tools) ? (latest.tools as string[]) : null;
          const ok = latest ? Boolean(latest.ok) : null;

          return (
            <li key={source.key} className="rounded-lg border border-border bg-surface p-4">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-medium">{source.display_name}</span>
                <span className="text-xs text-muted">{source.kind}</span>
                <span
                  className={`rounded px-1.5 py-0.5 text-xs ${
                    source.enabled ? "bg-accent-soft text-accent" : "bg-background text-muted"
                  }`}
                >
                  {source.enabled ? "enabled" : "disabled"}
                </span>
                <span className="text-xs text-muted">weight {source.weight}</span>

                <div className="ml-auto flex gap-2">
                  <button
                    type="button"
                    onClick={() => toggle(source)}
                    className="rounded-md border border-border px-2.5 py-1 text-xs hover:border-accent"
                  >
                    {source.enabled ? "Disable" : "Enable"}
                  </button>
                  <button
                    type="button"
                    onClick={() => check(source.key)}
                    disabled={checking === source.key}
                    className="rounded-md border border-border px-2.5 py-1 text-xs hover:border-accent disabled:opacity-60"
                  >
                    {checking === source.key ? "Checking…" : "Check"}
                  </button>
                </div>
              </div>

              {latest && (
                <div className="mt-3 space-y-1.5 border-t border-border pt-3 text-xs">
                  <p>
                    <span className={ok ? "text-accent" : "text-danger"}>
                      {ok ? "reachable" : "unreachable"}
                    </span>
                    {source.last_checked_at && (
                      <span className="text-muted">
                        {" "}
                        · checked {new Date(source.last_checked_at).toLocaleString()}
                      </span>
                    )}
                  </p>
                  {typeof latest.error === "string" && (
                    <p className="font-mono break-words text-danger">{latest.error}</p>
                  )}
                  {typeof latest.warning === "string" && (
                    <p className="break-words text-danger">{latest.warning}</p>
                  )}
                  {tools && (
                    <details>
                      <summary className="cursor-pointer text-muted">
                        {tools.length} tools exposed
                      </summary>
                      <p className="mt-1 font-mono break-words text-muted">{tools.join(", ")}</p>
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
