"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { SpaceButton } from "@/components/SpaceButton";
import { Wordmark } from "@/components/Wordmark";
import { useCopy } from "@/i18n/LocaleProvider";
import { ApiError, api } from "@/lib/api";
import type { User } from "@/lib/types";

/**
 * The site has no forms anywhere, so the field treatment is an extension of
 * its language rather than a copy: no filled box, just a hairline rule that
 * brightens on focus, with the label as one of its wide uppercase eyebrows.
 */
const FIELD =
  "w-full border-b border-hairline bg-transparent px-0 py-3 text-base text-white outline-none " +
  "transition-colors duration-300 ease-out-expo placeholder:text-white/25 " +
  "focus:border-white";

function LoginForm() {
  const searchParams = useSearchParams();
  const { copy } = useCopy();
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
      // Not a client navigation: the session cookie was just set, and the app
      // shell is rendered on the server from that cookie.
      window.location.href = searchParams.get("next") ?? "/search";
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 401
          ? copy.login.wrongCredentials
          : copy.login.backendDown,
      );
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="reveal reveal-in w-full max-w-md">
      <Wordmark className="h-7" />

      <p className="mt-10 font-display text-[11px] uppercase tracking-[0.42em] text-white/40">
        {copy.login.eyebrow}
      </p>
      <h1 className="mt-3 font-display text-4xl font-semibold uppercase leading-[0.98] tracking-tight text-white sm:text-5xl">
        {copy.login.title}
      </h1>
      <p className="mt-4 max-w-sm text-sm leading-relaxed text-white/60">
        {copy.login.lede}
      </p>

      <div className="mt-10 flex flex-col gap-7">
        <label className="block">
          <span className="font-display text-[11px] uppercase tracking-[0.28em] text-white/40">
            {copy.login.username}
          </span>
          <input
            value={username}
            onChange={(changeEvent) => setUsername(changeEvent.target.value)}
            autoComplete="username"
            // biome-ignore lint/a11y/noAutofocus: the sole input on a single-purpose screen; focusing it is what every user wants first
            autoFocus
            required
            className={FIELD}
          />
        </label>

        <label className="block">
          <span className="font-display text-[11px] uppercase tracking-[0.28em] text-white/40">
            {copy.login.password}
          </span>
          <input
            type="password"
            value={password}
            onChange={(changeEvent) => setPassword(changeEvent.target.value)}
            autoComplete="current-password"
            required
            className={FIELD}
          />
        </label>
      </div>

      {error && (
        <p
          role="alert"
          className="mt-6 border-l-2 border-danger bg-danger-soft px-4 py-3 text-sm text-danger"
        >
          {error}
        </p>
      )}

      <SpaceButton
        type="submit"
        variant="solid"
        disabled={busy}
        className="mt-10 w-full"
      >
        {busy ? copy.login.submitting : copy.login.submit}
      </SpaceButton>
    </form>
  );
}

export default function LoginPage() {
  return (
    <main className="relative flex min-h-screen items-center justify-center px-6 py-20">
      {/* Same backdrop idea as the site's media placeholder: a wash and a
          faint grid, so a near-empty black screen still has depth. */}
      <div aria-hidden="true" className="media-glow absolute inset-0" />
      <div aria-hidden="true" className="media-grid absolute inset-0" />

      <div className="relative z-10 flex w-full justify-center">
        {/* useSearchParams needs a Suspense boundary. */}
        <Suspense>
          <LoginForm />
        </Suspense>
      </div>
    </main>
  );
}
