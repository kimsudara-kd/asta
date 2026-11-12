import { expect, test } from "@playwright/test";
import { login, SHOTS } from "./helpers";

const DISCLAIMER = /do not enable, authorise or imply live trading/;

test.describe("@live overview and markets with real Binance public data", () => {
  test("overview: banner, disclaimer, execution disabled, honest venue labels", async ({ page }) => {
    await login(page, "viewer1");
    await expect(page.getByTestId("banner")).toContainText(
      "PAPER · PUBLIC MARKET DATA ONLY · NO TRADING · NO REAL FUNDS",
    );
    await expect(page.getByTestId("data-disclaimer")).toContainText(DISCLAIMER);
    await expect(page.getByText("DISABLED", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Gateway probe: DENY")).toBeVisible();

    const binance = page.getByTestId("venue-card").filter({ hasText: "BINANCE" });
    const okx = page.getByTestId("venue-card").filter({ hasText: "OKX" });
    await expect(binance).toContainText("Real-time public data (read-only)");
    await expect(binance).toContainText("(ok)"); // clock drift within limit
    await expect(okx).toContainText("FIXTURE data (not real)");
    await expect(okx).toContainText("clock: n/a (fixture data)");
    await expect(okx).not.toContainText("NOT ok");
    // the bare word "live" must not label data anywhere on the page
    const body = await page.locator("main").innerText();
    expect(body).not.toMatch(/Live public data/i);
    await page.screenshot({ path: `${SHOTS}/03-overview-desktop.png`, fullPage: true });
  });

  test("markets: Binance = real-time public data (cross-checked against the public API), OKX = fixture", async ({
    page,
  }) => {
    await login(page, "viewer1");
    await page.getByRole("link", { name: "Markets" }).click();
    await expect(page.getByRole("heading", { name: "Markets" })).toBeVisible();
    await expect(page.getByTestId("data-disclaimer")).toContainText(DISCLAIMER);

    const rows = page.getByTestId("market-row");
    await expect(rows).toHaveCount(6);
    const binanceBtc = rows.filter({ hasText: "BINANCE" }).filter({ hasText: "BTC-USDT" });
    // wait for the first real quote (bid is numeric)
    await expect(binanceBtc.locator("td").nth(2)).toHaveText(/^\d+\.\d+$/, { timeout: 40_000 });
    for (const sym of ["BTC-USDT", "ETH-USDT", "SOL-USDT"]) {
      const r = rows.filter({ hasText: "BINANCE" }).filter({ hasText: sym });
      await expect(r).toContainText("REAL-TIME PUBLIC DATA", { timeout: 40_000 });
      await expect(r).toContainText("fresh");
      await expect(r.locator("td").nth(2)).toHaveText(/^\d+\.\d+$/);
      await expect(r.locator("td").nth(3)).toHaveText(/^\d+\.\d+$/);
    }
    const okxRows = rows.filter({ hasText: "OKX" });
    await expect(okxRows).toHaveCount(3);
    for (let i = 0; i < 3; i++) await expect(okxRows.nth(i)).toContainText("FIXTURE (not real data)");
    await expect(okxRows.filter({ hasText: "REAL-TIME" })).toHaveCount(0);

    // cross-check the displayed Binance BTC price against Binance's own public endpoint
    const shown = parseFloat(await binanceBtc.locator("td").nth(2).innerText());
    const ref = await (
      await fetch("https://data-api.binance.vision/api/v3/ticker/bookTicker?symbol=BTCUSDT")
    ).json();
    const refBid = parseFloat(ref.bidPrice);
    expect(Math.abs(shown - refBid) / refBid).toBeLessThan(0.01);
    await page.screenshot({ path: `${SHOTS}/04-markets-desktop.png`, fullPage: true });
  });

  test("risk: gateway DENY, live trading DISABLED, no breach label does not imply execution", async ({
    page,
  }) => {
    await login(page, "viewer1");
    await page.getByRole("link", { name: "Risk" }).click();
    await expect(page.getByTestId("risk-gateway")).toContainText("DENY");
    await expect(page.getByTestId("risk-gateway")).toContainText("M0_EXECUTION_DISABLED");
    await expect(page.getByTestId("risk-live")).toContainText("DISABLED (hard-coded)");
    await expect(page.getByTestId("risk-newbuys")).toContainText("execution still disabled");
    await expect(page.getByRole("cell", { name: "starting_equity" })).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/05-risk-desktop.png`, fullPage: true });
  });
});
