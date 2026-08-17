import type { NextRequest } from "next/server";

/**
 * SSE passthrough to the backend.
 *
 * This exists as an explicit Route Handler rather than going through the
 * `rewrites()` in next.config.ts because a rewrite does not reliably stream
 * `text/event-stream` - buffering varies by runtime and adapter, and the
 * symptom is the whole answer arriving at once at the end, which looks like
 * the backend not streaming at all. Returning `upstream.body` directly does
 * stream.
 *
 * `rewrites()` returning a plain array is applied as `afterFiles`, i.e.
 * after filesystem routes, so this file wins for this one path and
 * everything else under /api still falls through to the rewrite.
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";

export async function POST(request: NextRequest) {
  const upstream = await fetch(`${backendUrl}/api/search/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      // Forwarded explicitly: the session cookie is what authenticates the
      // request, and the CSRF header is what the backend checks against it.
      cookie: request.headers.get("cookie") ?? "",
      "x-csrf-token": request.headers.get("x-csrf-token") ?? "",
    },
    body: await request.text(),
    // Undici refuses to stream a request body without this, and Next's
    // fetch is undici.
    duplex: "half",
  } as RequestInit & { duplex: "half" });

  if (!upstream.ok || !upstream.body) {
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "content-type": "text/event-stream",
      // no-transform matters as much as no-cache: a proxy that "optimises"
      // the body will buffer it.
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    },
  });
}
