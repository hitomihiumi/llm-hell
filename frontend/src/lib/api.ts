/**
 * Browser-side fetch wrapper.
 *
 * Two jobs: echo the CSRF cookie back as a header on every non-GET, and turn
 * a 401 into a redirect to /login rather than letting a page render half
 * broken.
 */

const CSRF_COOKIE = "kb_csrf";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

function readCookie(name: string): string | null {
  // The session cookie is httpOnly and unreadable here, which is the point.
  // The CSRF cookie deliberately is not: proving we can read a cookie for
  // this origin is exactly what the double-submit check tests.
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

export function csrfHeaders(): Record<string, string> {
  const token = readCookie(CSRF_COOKIE);
  return token ? { "X-CSRF-Token": token } : {};
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = init.method ?? "GET";
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(method === "GET" ? {} : csrfHeaders()),
      ...init.headers,
    },
  });

  if (response.status === 401) {
    // The session expired or was revoked. Reload through /login rather than
    // showing an empty page with no explanation.
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
    throw new ApiError(401, "not authenticated");
  }

  if (!response.ok) {
    let message = `request failed with ${response.status}`;
    try {
      const body = await response.json();
      message = body.detail ?? body.error?.message ?? message;
    } catch {
      // Non-JSON error body; the status is all we have.
    }
    throw new ApiError(response.status, message);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
};
