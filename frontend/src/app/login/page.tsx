"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { User } from "@/lib/types";

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(formEvent: React.FormEvent) {
    formEvent.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post<User>("/api/auth/login", { username, password });
      // Not router.push: the session cookie was just set, and a client-side
      // navigation would render the app shell from a cache that predates it.
      window.location.href = searchParams.get("next") ?? "/search";
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 401
          ? "Incorrect username or password."
          : "Could not sign in. Is the backend running?",
      );
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="w-full max-w-sm space-y-5">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Knowledge Base</h1>
        <p className="text-sm text-muted">
          Search Google Workspace, GitLab and the internal knowledge base at once.
        </p>
      </div>

      <div className="space-y-3">
        <label className="block space-y-1.5">
          <span className="text-sm font-medium">Username</span>
          <input
            value={username}
            onChange={(changeEvent) => setUsername(changeEvent.target.value)}
            autoComplete="username"
            autoFocus
            required
            className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
          />
        </label>

        <label className="block space-y-1.5">
          <span className="text-sm font-medium">Password</span>
          <input
            type="password"
            value={password}
            onChange={(changeEvent) => setPassword(changeEvent.target.value)}
            autoComplete="current-password"
            required
            className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
          />
        </label>
      </div>

      {error && (
        <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={busy}
        className="w-full rounded-md bg-accent px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
      >
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      {/* useSearchParams needs a Suspense boundary. */}
      <Suspense>
        <LoginForm />
      </Suspense>
    </main>
  );
}
