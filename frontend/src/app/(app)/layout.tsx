import { cookies } from "next/headers";
import Link from "next/link";
import { redirect } from "next/navigation";
import { LayoutSwitch } from "@/components/LayoutSwitch";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import { LogoutButton } from "@/components/LogoutButton";
import { Wordmark } from "@/components/Wordmark";
import { DICTIONARY } from "@/i18n/dictionary";
import { getLocale } from "@/i18n/getLocale";
import { LocaleProvider } from "@/i18n/LocaleProvider";
import type { User } from "@/lib/types";

const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";

/**
 * The real auth check. `proxy.ts` only saw whether a cookie exists; this
 * asks the backend whether it is still valid, which is the only thing that
 * catches a revoked or expired session.
 */
async function currentUser(): Promise<User | null> {
  // cookies() is async in Next 16 - synchronous access was removed.
  const cookieStore = await cookies();
  const header = cookieStore
    .getAll()
    .map((cookie) => `${cookie.name}=${cookie.value}`)
    .join("; ");

  try {
    const response = await fetch(`${backendUrl}/api/auth/me`, {
      headers: { cookie: header },
      cache: "no-store",
    });
    if (!response.ok) return null;
    return (await response.json()) as User;
  } catch {
    // Backend down. Treated as unauthenticated rather than crashing the
    // shell, so the user gets the login page instead of a stack trace.
    return null;
  }
}

export default async function AppLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const user = await currentUser();
  if (!user) redirect("/login");

  // Resolved once, here, and read from context by everything below - see
  // the note in LocaleProvider on why this app uses context where the site
  // passes a prop.
  const locale = await getLocale();
  const copy = DICTIONARY[locale];

  return (
    <LocaleProvider locale={locale}>
      <div className="flex min-h-screen flex-col">
        {/* Sticky rather than the site's fixed-and-hiding header: this one sits
          over a scrolling result list, and a bar that slid away mid-read
          would take the layout switch with it. */}
        <header className="sticky top-0 z-50 border-b border-hairline bg-black/80 backdrop-blur-md">
          <div className="mx-auto flex h-16 w-full max-w-[1600px] items-center gap-8 px-6 md:px-10">
            <Link
              href="/chat"
              className="focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-white"
            >
              <Wordmark className="h-5" />
            </Link>

            <LayoutSwitch />

            <nav
              aria-label={copy.nav.primaryLabel}
              className="hidden items-center gap-9 sm:flex"
            >
              <Link
                href="/sources"
                className="group relative font-display text-[11px] uppercase tracking-[0.24em] text-white/70 transition-colors duration-300 hover:text-white focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-white"
              >
                {copy.nav.sources}
                <span
                  aria-hidden="true"
                  className="absolute -bottom-2 left-0 h-px w-full origin-left scale-x-0 bg-white transition-transform duration-500 ease-out-expo group-hover:scale-x-100"
                />
              </Link>
            </nav>

            <div className="ml-auto flex items-center gap-5">
              <span className="hidden items-center gap-2 font-mono text-[10px] uppercase tracking-[0.24em] text-white/40 sm:flex">
                {user.display_name ?? user.username}
                {user.role === "admin" && (
                  <span className="border border-hairline px-1.5 py-0.5 text-accent">
                    {copy.nav.admin}
                  </span>
                )}
              </span>
              <LocaleSwitcher locale={locale} />
              <LogoutButton />
            </div>
          </div>
        </header>

        <main className="mx-auto w-full max-w-[1600px] flex-1 px-6 py-10 md:px-10">
          {children}
        </main>
      </div>
    </LocaleProvider>
  );
}
