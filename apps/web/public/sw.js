/* ASTA service worker. Deliberately minimal and conservative: this is an authenticated console.
 *
 *  - NEVER touches anything but same-origin GET requests.
 *  - NEVER caches or intercepts /api/* (session, market, risk, audit data): always straight to the network.
 *  - NEVER caches HTML pages or RSC payloads (they can contain per-user data): navigations go to the
 *    network; only when the network is unreachable is the static offline page shown.
 *  - Caches only public, immutable static files: /_next/static/* (content-hashed) and /icons/*.
 *  - No push, no background sync, no stored credentials. Nothing here can place or trigger an order.
 *
 * Bump VERSION to invalidate every cache (old ones are deleted on activate).
 */
const VERSION = "asta-sw-v1";
const SHELL_CACHE = `${VERSION}-shell`;
const STATIC_CACHE = `${VERSION}-static`;
const OFFLINE_URL = "/offline.html";
const PRECACHE = [OFFLINE_URL, "/icons/icon-192.png", "/icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k.startsWith("asta-sw-") && !k.startsWith(VERSION))
            .map((k) => caches.delete(k)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

function isStaticAsset(url) {
  return url.pathname.startsWith("/_next/static/") || url.pathname.startsWith("/icons/");
}

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  // only successful, same-origin ("basic") responses are stored
  if (response.ok && response.type === "basic") {
    const cache = await caches.open(STATIC_CACHE);
    cache.put(request, response.clone());
  }
  return response;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return; // writes are never intercepted
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return; // third parties are never intercepted
  if (url.pathname.startsWith("/api/")) return; // API/session data: network only, never cached
  if (url.pathname === "/sw.js") return; // the worker itself is always fetched fresh
  if (isStaticAsset(url)) {
    event.respondWith(cacheFirst(request));
    return;
  }
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() => caches.match(OFFLINE_URL).then((r) => r || Response.error())),
    );
  }
  // anything else (RSC payloads, data fetches): not intercepted
});
