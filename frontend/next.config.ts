import type { NextConfig } from "next";

/**
 * The browser only ever talks to this origin; everything under /api is
 * forwarded to the FastAPI backend from the server side.
 *
 * That choice is what makes cookie auth simple: no CORS, no
 * `credentials: "include"`, no SameSite edge cases, because there is only one
 * origin involved. The backend still sets a real CORS policy for anyone
 * hitting :8000 directly, but the app never relies on it.
 *
 * Note this is `rewrites()` returning an array, which Next treats as
 * `afterFiles` - it runs AFTER filesystem routes. That ordering is load
 * bearing: the SSE endpoint at src/app/api/search/stream/route.ts must win
 * over this rewrite, because a rewrite does not reliably stream
 * text/event-stream.
 */
const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/api/:path*` }];
  },
};

export default nextConfig;
