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
