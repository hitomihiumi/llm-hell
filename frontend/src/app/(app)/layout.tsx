import { cookies } from "next/headers";
import Link from "next/link";
import { redirect } from "next/navigation";
import { LogoutButton } from "@/components/LogoutButton";
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

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-border">
        <div className="mx-auto flex w-full max-w-5xl items-center gap-6 px-6 py-3">
          <Link href="/search" className="text-sm font-semibold tracking-tight">
            Knowledge Base
          </Link>
          <nav className="flex items-center gap-4 text-sm text-muted">
            <Link href="/search" className="hover:text-foreground">
              Search
            </Link>
            <Link href="/sources" className="hover:text-foreground">
              Sources
            </Link>
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm text-muted">
            <span>
              {user.display_name ?? user.username}
              {user.role === "admin" && (
                <span className="ml-1.5 rounded bg-accent-soft px-1.5 py-0.5 text-xs text-accent">
                  admin
                </span>
              )}
            </span>
            <LogoutButton />
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-8">{children}</main>
    </div>
  );
}
