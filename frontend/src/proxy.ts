import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

/**
 * Cheap auth gate.
 *
 * This is `proxy.ts`, not `middleware.ts` - the middleware convention is
 * deprecated in Next 16 and renamed, with the exported function named
 * `proxy`.
 *
 * It checks only that a session cookie is PRESENT. It deliberately does not
 * try to validate it: sessions live in Postgres, and proxy code is meant to
 * run detached from the app (potentially at a CDN edge) with no database
 * access. The real check is in the (app) layout, which calls
 * /api/auth/me server-side and redirects on 401. A revoked cookie therefore
 * gets past this and is caught there.
 */
const SESSION_COOKIE = "kb_session";

export function proxy(request: NextRequest) {
  const hasSession = request.cookies.has(SESSION_COOKIE);
  const { pathname, search } = request.nextUrl;

  if (!hasSession) {
    const login = new URL("/login", request.url);
    // Remember where they were headed, so login can return them there.
    if (pathname !== "/") login.searchParams.set("next", pathname + search);
    return NextResponse.redirect(login);
  }

  return NextResponse.next();
}

export const config = {
  /**
   * Everything except the login page, the API (which authenticates itself
   * and must return 401 rather than a redirect - an HTML login page in
   * answer to a fetch is far more confusing than a status code), and static
   * assets.
   */
  matcher: ["/((?!login|api|_next/static|_next/image|favicon.ico|.*\\.svg).*)"],
};
