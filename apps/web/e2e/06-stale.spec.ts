import { expect, test } from "@playwright/test";
import { login, SHOTS } from "./helpers";

test.describe("@nodata stale-data warnings: no market data at all (fail-closed)", () => {
  test("every row is NO DATA + STALE, the banner is the red risk halt", async ({ page }) => {
    await login(page, "viewer1");
    await expect(page.getByTestId("banner")).toContainText("RISK HALT", { timeout: 30_000 });
    await expect(page.getByTestId("banner")).toContainText("STALE_MARKET_DATA");
    await expect(page.locator(".banner.halt")).toBeVisible();
    await page.getByRole("link", { name: "Markets" }).click();
    const rows = page.getByTestId("market-row");
    await expect(rows).toHaveCount(6);
    await expect(page.getByTestId("stale-pill")).toHaveCount(6);
    for (let i = 0; i < 6; i++) {
      await expect(rows.nth(i)).toContainText("NO DATA");
      await expect(rows.nth(i)).not.toContainText("REAL-TIME");
    }
    await page.getByRole("link", { name: "Risk" }).click();
    await expect(page.getByTestId("risk-newbuys")).toContainText("HALTED");
    await expect(page.getByTestId("risk-gateway")).toContainText("DENY");
    await page.screenshot({ path: `${SHOTS}/10-stale-nodata-risk.png`, fullPage: true });
    await page.goto("/");
    await expect(page.getByTestId("venue-card").first()).toContainText(/Connecting|Stale|Unreachable/);
    await page.screenshot({ path: `${SHOTS}/11-stale-nodata-overview.png`, fullPage: true });
  });
});

test.describe("@aging fresh data becomes stale when updates stop", () => {
  test("a row goes fresh -> STALE while still labelled FIXTURE", async ({ page }) => {
    await login(page, "viewer1");
    await page.getByRole("link", { name: "Markets" }).click();
    const rows = page.getByTestId("market-row");
    await expect(rows).toHaveCount(6);
    // fixture ticks every 5 s; staleness threshold is 1 s => a fresh row must age into STALE
    const first = rows.first();
    await expect(first).toContainText("FIXTURE (not real data)", { timeout: 30_000 });
    await expect(page.getByTestId("stale-pill").first()).toBeVisible({ timeout: 30_000 });
    for (let i = 0; i < 6; i++) await expect(rows.nth(i)).not.toContainText("REAL-TIME");
    await page.screenshot({ path: `${SHOTS}/12-stale-aging-markets.png`, fullPage: true });
  });
});
