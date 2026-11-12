import { expect, test } from "@playwright/test";
import { login, PAGES, SHOTS } from "./helpers";

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "mobile", width: 390, height: 844 },
];

test.describe("@live responsive layout", () => {
  for (const vp of VIEWPORTS) {
    test(`${vp.name} ${vp.width}x${vp.height}: no horizontal page overflow, banner + nav usable`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await login(page, "rev1");
      for (const p of PAGES) {
        await page.goto(p);
        await expect(page.getByTestId("banner")).toBeVisible();
        await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
        await expect(page.getByRole("link", { name: "Markets" })).toBeVisible();
        await expect(page.locator("h1")).toBeVisible();
        const o = await page.evaluate(() => ({
          sw: document.documentElement.scrollWidth,
          cw: document.documentElement.clientWidth,
        }));
        expect(o.sw, `${p} @${vp.width}: scrollWidth ${o.sw} > clientWidth ${o.cw}`).toBeLessThanOrEqual(o.cw + 1);
      }
      if (vp.name === "mobile") {
        await page.goto("/markets");
        await expect(page.getByTestId("market-row")).toHaveCount(6);
        await page.screenshot({ path: `${SHOTS}/07-markets-mobile.png`, fullPage: true });
        await page.goto("/");
        await expect(page.getByTestId("venue-card").first()).toBeVisible();
        await page.screenshot({ path: `${SHOTS}/08-overview-mobile.png`, fullPage: true });
      }
      if (vp.name === "tablet") {
        await page.goto("/risk");
        await page.screenshot({ path: `${SHOTS}/09-risk-tablet.png`, fullPage: true });
      }
    });
  }
});
