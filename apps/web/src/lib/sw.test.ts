// Security policy of the service worker (public/sw.js), executed against the real file in a sandbox.
// The worker must never intercept the API, writes or third parties, and must cache only public static files.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";

type Handler = (event: Record<string, unknown>) => void;
const SRC = readFileSync(path.join(import.meta.dirname, "../../public/sw.js"), "utf-8");
const ORIGIN = "https://asta.local";

class FakeCache {
  store = new Map<string, unknown>();
  async addAll(urls: string[]) { for (const u of urls) this.store.set(u, { url: u }); }
  async put(req: { url: string }, res: unknown) { this.store.set(req.url, res); }
}

function boot(fetchImpl: (r: unknown) => Promise<unknown> = async () => ({ ok: true, type: "basic", clone() { return this; } })) {
  const handlers: Record<string, Handler> = {};
  const caches = new Map<string, FakeCache>();
  const sandbox = {
    URL,
    Response: { error: () => ({ error: true }) },
    self: {
      location: { origin: ORIGIN },
      addEventListener: (t: string, h: Handler) => { handlers[t] = h; },
      skipWaiting: async () => undefined,
      clients: { claim: async () => undefined },
    },
    caches: {
      open: async (n: string) => { if (!caches.has(n)) caches.set(n, new FakeCache()); return caches.get(n)!; },
      keys: async () => [...caches.keys()],
      delete: async (n: string) => caches.delete(n),
      match: async (req: { url?: string } | string) => {
        const key = typeof req === "string" ? req : (req.url ?? "");
        const k = key.startsWith(ORIGIN) ? key.slice(ORIGIN.length) : key;
        for (const c of caches.values()) { const hit = c.store.get(k) ?? c.store.get(key); if (hit) return hit; }
        return undefined;
      },
    },
    fetch: fetchImpl,
  };
  vm.runInNewContext(SRC, sandbox);
  return { handlers, caches };
}

function fetchEvent(h: Handler, url: string, opts: { method?: string; mode?: string } = {}) {
  let responded: Promise<unknown> | undefined;
  const ev = {
    request: { method: opts.method ?? "GET", url: url.startsWith("http") ? url : ORIGIN + url, mode: opts.mode ?? "cors" },
    respondWith: (p: Promise<unknown>) => { responded = p; },
  };
  h(ev);
  return responded;
}

test("never intercepts the API, writes, third parties, the worker itself or non-navigation data fetches", () => {
  const { handlers } = boot();
  const f = handlers.fetch;
  assert.equal(fetchEvent(f, "/api/v1/auth/me"), undefined);
  assert.equal(fetchEvent(f, "/api/v1/markets"), undefined);
  assert.equal(fetchEvent(f, "/api/v1/auth/login", { method: "POST" }), undefined);
  assert.equal(fetchEvent(f, "/_next/static/chunk.js", { method: "POST" }), undefined);
  assert.equal(fetchEvent(f, "https://evil.example/_next/static/x.js"), undefined);
  assert.equal(fetchEvent(f, "/sw.js"), undefined);
  assert.equal(fetchEvent(f, "/paper?_rsc=abc"), undefined); // RSC/data payloads: untouched
});

test("static assets are cache-first and only successful same-origin responses are stored", async () => {
  let n = 0;
  const { handlers, caches } = boot(async () => {
    n++;
    return { ok: true, type: "basic", clone() { return { copy: true }; } };
  });
  const first = await fetchEvent(handlers.fetch, "/_next/static/a.js");
  assert.ok(first);
  await fetchEvent(handlers.fetch, "/_next/static/a.js"); // second call: from cache
  assert.equal(n, 1);
  const bad = boot(async () => ({ ok: false, type: "basic", clone() { return this; } }));
  await fetchEvent(bad.handlers.fetch, "/_next/static/err.js");
  const opaque = boot(async () => ({ ok: true, type: "opaque", clone() { return this; } }));
  await fetchEvent(opaque.handlers.fetch, "/_next/static/op.js");
  const stored = [...caches.values(), ...bad.caches.values(), ...opaque.caches.values()].flatMap((c) => [...c.store.keys()]);
  assert.ok(stored.some((k) => k.endsWith("/_next/static/a.js")));
  assert.ok(!stored.some((k) => k.includes("err.js") || k.includes("op.js")));
});

test("page navigations always go to the network; offline page only when it is unreachable; HTML is never stored", async () => {
  const online = boot(async () => ({ ok: true, type: "basic", html: "per-user page", clone() { return this; } }));
  await online.handlers.install({ waitUntil: (p: Promise<unknown>) => p });
  const res = (await fetchEvent(online.handlers.fetch, "/paper", { mode: "navigate" })) as { html?: string };
  assert.equal(res.html, "per-user page");
  const keys = [...online.caches.values()].flatMap((c) => [...c.store.keys()]);
  assert.ok(!keys.some((k) => k.includes("/paper")), "page HTML must never be cached");

  const offline = boot(async () => { throw new TypeError("network down"); });
  await offline.handlers.install({ waitUntil: (p: Promise<unknown>) => p });
  const fb = (await fetchEvent(offline.handlers.fetch, "/paper", { mode: "navigate" })) as { url?: string };
  assert.equal(fb.url, "/offline.html");
});

test("install precaches only the offline page and icons; activate deletes only OLD asta caches", async () => {
  const { handlers, caches } = boot();
  let p: Promise<unknown> | undefined;
  handlers.install({ waitUntil: (x: Promise<unknown>) => { p = x; } });
  await p;
  const pre = [...caches.values()].flatMap((c) => [...c.store.keys()]).sort();
  assert.deepEqual(pre, ["/icons/icon-192.png", "/icons/icon-512.png", "/offline.html"]);
  for (const n of ["asta-sw-v0-static", "asta-sw-v0-shell", "someone-elses-cache"]) caches.set(n, new FakeCache());
  let q: Promise<unknown> | undefined;
  handlers.activate({ waitUntil: (x: Promise<unknown>) => { q = x; } });
  await q;
  const left = [...caches.keys()].sort();
  assert.ok(left.includes("someone-elses-cache"), "foreign caches are never deleted");
  assert.ok(!left.includes("asta-sw-v0-static") && !left.includes("asta-sw-v0-shell"));
  assert.ok(left.some((k) => k.startsWith("asta-sw-v1-")));
});

test("the worker source contains no credential, push, sync or storage APIs", () => {
  for (const banned of ["PushManager", "showNotification", "SyncManager", "BackgroundSync", "localStorage", "indexedDB", "Authorization", "document.cookie"]) {
    assert.ok(!new RegExp(banned, "i").test(SRC.replace(/\/\*[\s\S]*?\*\//, "")), banned);
  }
});
