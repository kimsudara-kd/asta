import { expect, test } from "@playwright/test";
import { login, PAGES } from "./helpers";

const FORBIDDEN = /\b(buy|sell|place|order|trade|withdraw|transfer|deposit|execute|leverage|margin|borrow|enable live|go live)\b/i;

test.describe("@live no execution controls and no live-trading routes", () => {
  test("no page offers any trading/funds control", async ({ page }) => {
    await login(page, "rev1"); // reviewer sees the most pages
    for (const p of PAGES) {
      await page.goto(p);
      await expect(page.locator("main")).toBeVisible();
      const controls = await page
        .locator("button, a, input, select, textarea, [role=button], [role=link], form")
        .evaluateAll((els) =>
          els.map((e) => {
            const el = e as HTMLElement & { value?: string; placeholder?: string };
            return [el.innerText, el.getAttribute("aria-label"), el.getAttribute("name"),
              el.placeholder, el.getAttribute("href"), el.getAttribute("action")]
              .filter(Boolean).join(" | ");
          }),
        );
      const bad = controls.filter((c) => FORBIDDEN.test(c));
      expect(bad, `interactive controls on ${p}`).toEqual([]);
      // the only buttons present are sign-out (and none are form submits for orders)
      const buttons = await page.getByRole("button").allInnerTexts();
      expect(buttons.every((b) => b.trim() === "Sign out"), `${p}: ${buttons}`).toBe(true);
    }
  });

  test("API exposes no order/live/transfer routes, even to an authenticated reviewer", async ({
    page,
  }) => {
    await login(page, "rev1");
    const probes = ["orders", "order", "live", "trade", "execute", "withdraw", "transfer",
      "controls/resume", "controls/halt", "demo-runs", "paper-runs", "backtests"];
    for (const p of probes) {
      for (const method of ["get", "post"] as const) {
        const r = await page.request[method](`/api/v1/${p}`, {
          headers: { "X-CSRF-Token": "x" }, failOnStatusCode: false,
        });
        expect([404, 405], `${method.toUpperCase()} /v1/${p} -> ${r.status()}`).toContain(r.status());
      }
    }
    // interactive API docs are disabled
    for (const p of ["/api/docs", "/api/redoc", "/api/openapi.json"]) {
      expect((await page.request.get(p, { failOnStatusCode: false })).status()).toBe(404);
    }
  });

  test("security headers present on pages", async ({ page }) => {
    const r = await page.goto("/login");
    const h = r!.headers();
    expect(h["x-frame-options"]).toBe("DENY");
    expect(h["x-content-type-options"]).toBe("nosniff");
    expect(h["referrer-policy"]).toBe("no-referrer");
    expect(h["x-powered-by"]).toBeUndefined();
  });
});
