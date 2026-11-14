// Same-origin proxy to the local API so the HttpOnly session cookie never crosses origins.
const API = process.env.ASTA_API_URL ?? "http://127.0.0.1:4640";
export default {
  poweredByHeader: false,
  async rewrites() {
    return [
      { source: "/api/health", destination: `${API}/health` },
      { source: "/api/v1/:path*", destination: `${API}/v1/:path*` },
    ];
  },
  async headers() {
    return [
      {
        // the worker script must never be served from a stale HTTP cache, or updates would not arrive
        source: "/sw.js",
        headers: [
          { key: "Cache-Control", value: "no-cache, no-store, must-revalidate" },
          { key: "Content-Type", value: "text/javascript; charset=utf-8" },
        ],
      },
      {
        source: "/(.*)",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};
