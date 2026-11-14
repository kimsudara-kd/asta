// PWA acceptance check against a RUNNING production build (pnpm build && pnpm start), in real Chrome.
//   node scripts/pwa-check.mjs            (ASTA_WEB_URL overrides http://127.0.0.1:3640)
// Verifies: manifest + icons, apple/theme meta, service worker lifecycle and scope, that only public static
// files (and the offline page) are cached - never an API response or page HTML - the offline fallback, and
// Chrome's own installability verdict. Needs no login and no credentials.
import { chromium, devices } from "@playwright/test";
const BASE = process.env.ASTA_WEB_URL ?? "http://127.0.0.1:3640";
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
// 1. manifest + icons over HTTP
const m = await (await fetch(BASE + "/manifest.webmanifest")).json();
ok(m.name && m.short_name === "ASTA" && m.display === "standalone" && m.start_url === "/" && m.scope === "/", "manifest: name/short_name/standalone/start_url/scope");
ok(m.icons.some((i) => i.sizes === "192x192") && m.icons.some((i) => i.sizes === "512x512") && m.icons.some((i) => i.purpose === "maskable"), "manifest: 192, 512 and maskable icons declared");
for (const i of m.icons) { const r = await fetch(BASE + i.src); ok(r.status === 200 && r.headers.get("content-type").includes("image/png"), `icon served: ${i.src}`); }
const sw = await fetch(BASE + "/sw.js"); ok(sw.status === 200 && /no-cache|no-store/.test(sw.headers.get("cache-control") || "") && sw.headers.get("content-type").includes("javascript"), "sw.js: served as JS with no-cache");
const off = await fetch(BASE + "/offline.html"); ok(off.status === 200, "offline.html served");
const html = await (await fetch(BASE + "/login")).text();
ok(/rel="manifest"/.test(html) && /apple-mobile-web-app-capable|mobile-web-app-capable/.test(html) && /apple-touch-icon/.test(html) && /viewport-fit=cover/.test(html) && /name="theme-color"/.test(html), "html: manifest link, apple web-app meta, apple-touch-icon, viewport-fit=cover, theme-color");
// 2. real Chrome: SW lifecycle, caches, offline, installability
const browser = await chromium.launch({ channel: "chrome" });
const ctx = await browser.newContext({ ...devices["iPhone 13"], colorScheme: "dark" });
const page = await ctx.newPage();
await page.goto(BASE + "/login", { waitUntil: "networkidle" });
const reg = await page.evaluate(async () => { const r = await navigator.serviceWorker.ready; return { scope: r.scope, state: r.active?.state }; });
ok(reg.scope === BASE + "/" && reg.state === "activated", `service worker active (scope ${reg.scope})`);
await page.reload({ waitUntil: "networkidle" });
ok(await page.evaluate(() => !!navigator.serviceWorker.controller), "page is controlled by the worker after reload");
const cached = await page.evaluate(async () => { const out = []; for (const n of await caches.keys()) { const c = await caches.open(n); for (const r of await c.keys()) out.push(new URL(r.url).pathname); } return out; });
ok(cached.includes("/offline.html") && cached.some((p) => p.startsWith("/_next/static/")), `cache holds the offline page + static assets (${cached.length} entries)`);
ok(!cached.some((p) => p.startsWith("/api/")) && !cached.some((p) => ["/", "/login", "/paper", "/markets"].includes(p)), "no API response and no page HTML in any cache");
await ctx.setOffline(true);
await page.goto(BASE + "/markets").catch(() => {});
ok(await page.getByText("You're offline").isVisible(), "offline navigation shows the offline page");

await ctx.setOffline(false);
const cdp = await ctx.newCDPSession(page);
await page.goto(BASE + "/login", { waitUntil: "networkidle" });
const inst = await cdp.send("Page.getInstallabilityErrors");
const real = inst.installabilityErrors.filter((e) => e.errorId !== "in-incognito"); ok(real.length === 0, "Chrome installability errors (ignoring the automation-profile-only in-incognito): " + JSON.stringify(inst.installabilityErrors.map((e) => e.errorId)));
const am = await cdp.send("Page.getAppManifest");
ok(am.errors.length === 0, "Chrome manifest parse errors: " + JSON.stringify(am.errors.map((e) => e.message)));
await browser.close();
