import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import { login, SHOTS } from "./helpers";

const FORBIDDEN = /\b(buy|sell|place|order|trade|withdraw|transfer|deposit|execute|leverage|margin|borrow|enable live|go live)\b/i;
const PAGES = ["/research/datasets", "/research/lab", "/research/results", "/research/compare"];
const LABELS = ["SIMULATED", "HISTORICAL DATA", "ESTIMATED FILLS", "NOT LIVE TRADING"];

async function csrf(page: Page): Promise<string> {
  return (await (await page.request.get("/api/v1/auth/me")).json()).csrf_token;
}
async function gotoResearch(page: Page, path: string) {
  await page.goto(path);
  await expect(page.getByTestId("sim-banner")).toBeVisible();
}

test.describe("@research viewer: read-only research", () => {
  test("viewer can read datasets/lab but has no import, derive, validate or run controls", async ({ page }) => {
    await login(page, "viewer1");
    await page.getByRole("link", { name: "Research", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Historical datasets" })).toBeVisible();
    for (const word of LABELS) await expect(page.getByTestId("sim-banner")).toContainText(word);
    await expect(page.getByRole("button", { name: "Import dataset" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Derive timeframe" })).toHaveCount(0);
    await expect(page.getByTestId("import-note")).toContainText("view datasets but not import them");
    await page.goto("/research/lab");
    await expect(page.getByTestId("run-backtest")).toBeDisabled();
    // the server enforces it too, not just the UI
    const c = await csrf(page);
    for (const [path, body] of [["/api/v1/datasets/import", { file_name: "synthetic_BTC-USDT_1h.csv" }], ["/api/v1/backtests", {}]] as const) {
      const r = await page.request.post(path, { data: body, headers: { "X-CSRF-Token": c, "Idempotency-Key": "viewer-attempt-0001" }, failOnStatusCode: false });
      expect(r.status()).toBe(403);
    }
  });
});

test.describe.serial("@research operator workflow: import -> derive -> run -> compare -> results", () => {
  test("import synthetic datasets: quality, provenance and honest labels", async ({ page }) => {
    await login(page, "op1");
    await page.getByRole("link", { name: "Research", exact: true }).click();
    await expect(page.getByTestId("importable-row")).toHaveCount(3);
    for (let i = 0; i < 3; i++) {
      await page.getByRole("button", { name: "Import dataset" }).nth(i).click();
      await expect(page.getByTestId("dataset-row")).toHaveCount(i + 1, { timeout: 60_000 });
    }
    const rows = page.getByTestId("dataset-row");
    for (let i = 0; i < 3; i++) {
      await expect(rows.nth(i).getByTestId("dataset-label")).toHaveText("SYNTHETIC (not real exchange data)");
      await expect(rows.nth(i)).toContainText("PASS");
      await expect(rows.nth(i)).toContainText("no"); // research-grade: no
    }
    await rows.first().getByRole("button", { name: "View quality" }).click();
    const detail = page.getByTestId("quality-detail");
    await expect(detail).toContainText("Missing candles are reported, never filled");
    await expect(detail).toContainText("SYNTHETIC (not real exchange data)");
    await expect(detail).toContainText("Checksum");
    await page.screenshot({ path: `${SHOTS}/13-research-datasets.png`, fullPage: true });
  });

  test("derive a coarser timeframe: the derived label keeps the synthetic root", async ({ page }) => {
    await login(page, "op1");
    await gotoResearch(page, "/research/datasets");
    const row = page.getByTestId("dataset-row").filter({ hasText: "BTC-USDT" }).first();
    await row.getByLabel("Derived timeframe").selectOption("4h");
    await row.getByRole("button", { name: "Derive timeframe" }).click();
    await expect(page.getByTestId("dataset-msg")).toHaveText("Derive: done.", { timeout: 30_000 });
    // match the TIMEFRAME column exactly (every row's action selector also lists "4h" as an option)
    const derived = page.getByTestId("dataset-row").filter({ has: page.locator("td:nth-child(3)").filter({ hasText: /^4h$/ }) });
    await expect(derived).toHaveCount(1);
    await expect(derived.getByTestId("dataset-label")).toHaveText("SYNTHETIC (not real exchange data) [derived]");
    // finer-than-source is refused by the server (never invented)
    const c = await csrf(page);
    const ds = (await (await page.request.get("/api/v1/datasets")).json()).datasets;
    const four = ds.find((d: { timeframe: string }) => d.timeframe === "4h");
    const bad = await page.request.post(`/api/v1/datasets/${four.version_id}/derive`, { data: { timeframe: "1h" }, headers: { "X-CSRF-Token": c, "Idempotency-Key": "finer-attempt-0001" }, failOnStatusCode: false });
    expect(bad.status()).toBe(422);
  });

  test("lab: settings validation warns about synthetic data and unverified assumptions", async ({ page }) => {
    await login(page, "op1");
    await gotoResearch(page, "/research/lab");
    await page.getByTestId("strategy-select").selectOption("sma_cross");
    await expect(page.getByTestId("param-fast")).toBeVisible();
    await expect(page.getByTestId("dataset-BTC-USDT")).toBeVisible();
    await page.getByTestId("validate-settings").click();
    const issues = page.getByTestId("settings-issues");
    await expect(issues).toContainText("not research-grade");
    await expect(issues).toContainText("unverified assumption");
    await page.screenshot({ path: `${SHOTS}/14-research-lab.png`, fullPage: true });
  });

  test("run a strategy with baselines: identical data/costs, flags, overlay chart, export", async ({ page }) => {
    await login(page, "op1");
    await gotoResearch(page, "/research/lab");
    await page.getByTestId("strategy-select").selectOption("sma_cross");
    await page.getByTestId("param-fast").fill("12");
    await page.getByTestId("param-slow").fill("40");
    await page.getByPlaceholder("optional name").fill("e2e moving-average run");
    await page.getByTestId("run-backtest").click();
    await expect(page).toHaveURL(/\/research\/compare\?ids=/, { timeout: 30_000 });
    const rows = page.getByTestId("compare-row");
    await expect(rows).toHaveCount(3);
    for (let i = 0; i < 3; i++) await expect(rows.nth(i)).toContainText("COMPLETED", { timeout: 120_000 });
    for (let i = 0; i < 3; i++) await expect(rows.nth(i)).toContainText("NOT_RESEARCH_GRADE_DATA"); // synthetic inputs
    await expect(rows.filter({ hasText: "all_cash" })).toContainText("NEGATIVE_OR_ZERO_NET_RETURN"); // flat is not success
    await expect(page.getByText("identical engine, data and cost model")).toBeVisible();
    await expect(page.getByTestId("compare-chart").locator("path[data-series]")).toHaveCount(3);
    await page.screenshot({ path: `${SHOTS}/15-research-compare.png`, fullPage: true });
    const [dl] = await Promise.all([page.waitForEvent("download"), page.getByTestId("export-compare-json").click()]);
    const body = JSON.parse(readFileSync(await dl.path(), "utf-8"));
    expect(body.labels).toEqual(LABELS);
    expect(body.rows).toHaveLength(3);
    const [dl2] = await Promise.all([page.waitForEvent("download"), page.getByTestId("export-compare-csv").click()]);
    expect(readFileSync(await dl2.path(), "utf-8")).toContain("result_checksum");
  });

  test("results: simulated, never realised; reproducibility facts; fills CSV export", async ({ page }) => {
    await login(page, "op1");
    await gotoResearch(page, "/research/results");
    await expect(page.getByTestId("run-row")).toHaveCount(3);
    await page.getByRole("link", { name: "e2e moving-average run" }).click();
    await expect(page.getByTestId("run-status")).toHaveText("COMPLETED", { timeout: 60_000 });
    await expect(page.getByTestId("grade-pill")).toHaveText("NOT research-grade data");
    await expect(page.getByTestId("card-net-pnl")).toContainText("USDT");
    await expect(page.getByTestId("metric-cards")).toContainText("Simulated net P&L (marked, not realised)");
    await expect(page.getByTestId("equity-chart").locator("path[data-series]")).toHaveCount(1);
    await expect(page.getByTestId("drawdown-chart").locator("path[data-series]")).toHaveCount(1);
    await expect(page.getByTestId("risk-panel")).toContainText("regardless of P&L");
    await expect(page.getByTestId("fills-panel")).toContainText("Simulated fills");
    await expect(page.getByTestId("fills-panel")).toContainText("not exchange fills");
    await expect(page.getByTestId("config-hash")).toHaveText(/^[0-9a-f]{64}$/);
    await expect(page.getByTestId("result-checksum")).toHaveText(/^[0-9a-f]{64}$/);
    // the page may only mention realised/cash profit in NEGATED disclaimers; strip those, then no claim may remain
    const raw = await page.locator("main").innerText();
    expect(raw).toMatch(/not realised cash profit/i);
    const text = raw.replace(/no figure is a realised cash profit/gi, "").replace(/not realised cash profit/gi, "").replace(/\(marked, not realised\)/gi, "");
    expect(text).not.toMatch(/\b(realised profit|real profit|guaranteed|you (earned|made)|cash profit)\b/i);
    expect(text).toContain("NOT LIVE TRADING");
    await page.screenshot({ path: `${SHOTS}/16-research-results.png`, fullPage: true });
    const [dl] = await Promise.all([page.waitForEvent("download"), page.getByTestId("export-csv").click()]);
    expect(readFileSync(await dl.path(), "utf-8").split("\n")[0]).toContain("fill_id");
    const [dj] = await Promise.all([page.waitForEvent("download"), page.getByTestId("export-json").click()]);
    expect(JSON.parse(readFileSync(await dj.path(), "utf-8")).labels).toEqual(LABELS);
  });

  test("short runs show N/A with the reason, never zero", async ({ page }) => {
    await login(page, "op1");
    await gotoResearch(page, "/research/lab");
    await page.getByTestId("strategy-select").selectOption("sma_cross");
    await page.locator("input[type=date]").nth(0).fill("2024-01-05");
    await page.locator("input[type=date]").nth(1).fill("2024-01-20");
    await page.getByRole("checkbox").uncheck(); // no baselines: go straight to the result
    await page.getByTestId("run-backtest").click();
    await expect(page).toHaveURL(/\/research\/results\?id=/, { timeout: 30_000 });
    await expect(page.getByTestId("run-status")).toHaveText("COMPLETED", { timeout: 60_000 });
    await expect(page.getByTestId("card-sharpe")).toContainText("N/A —");
    await expect(page.getByTestId("card-sharpe")).toContainText("daily observations");
    await expect(page.getByTestId("m-annualized_return")).toContainText("N/A —");
    await expect(page.getByTestId("m-sortino")).toContainText("N/A —");
  });

  test("cancel: a queued run is cancelled before it starts and is shown as CANCELLED", async ({ page }) => {
    await login(page, "op1");
    await gotoResearch(page, "/research/results");
    const c = await csrf(page);
    const ds = (await (await page.request.get("/api/v1/datasets")).json()).datasets.filter((d: { timeframe: string }) => d.timeframe === "1h");
    const ids = Object.fromEntries(ds.map((d: { symbol: string; version_id: string }) => [d.symbol, d.version_id]));
    const body = (strategy: string, label: string) => ({
      strategy_id: strategy, dataset_version_ids: ids, rule_set: "fixture-generic",
      fee_profile: "BINANCE-SPOT-BASE-ASSUMED-v1", label,
    });
    const h = (k: string) => ({ "X-CSRF-Token": c, "Idempotency-Key": k });
    const heavy = await page.request.post("/api/v1/backtests", { data: body("regime_grid", "e2e heavy"), headers: h("heavy-run-0000001") });
    expect(heavy.status()).toBe(202);
    const light = await page.request.post("/api/v1/backtests", { data: body("all_cash", "e2e queued-then-cancelled"), headers: h("light-run-000001") });
    const lightId = (await light.json()).id;
    const cancel = await page.request.post(`/api/v1/backtests/${lightId}/cancel`, { data: {}, headers: h("cancel-run-00001") });
    expect((await cancel.json()).state).toBe("CANCELLED");
    expect((await page.request.post(`/api/v1/backtests/${lightId}/cancel`, { data: {}, headers: h("cancel-run-00002"), failOnStatusCode: false })).status()).toBe(409);
    await page.reload();
    await expect(page.getByTestId("run-row").filter({ hasText: "e2e queued-then-cancelled" })).toContainText("CANCELLED");
    // let the heavy run finish so the worker is idle for whatever runs next
    await expect(page.getByTestId("run-row").filter({ hasText: "e2e heavy" })).toContainText("COMPLETED", { timeout: 180_000 });
  });

  test("research pages expose no execution controls and carry the simulation banner", async ({ page }) => {
    await login(page, "op1");
    for (const p of PAGES) {
      await gotoResearch(page, p);
      for (const word of LABELS) await expect(page.getByTestId("sim-banner")).toContainText(word);
      const controls = await page
        .locator("button, a, input, select, textarea, [role=button], [role=link], form")
        .evaluateAll((els) => els.map((e) => {
          const el = e as HTMLElement & { placeholder?: string };
          return [el.innerText, el.getAttribute("aria-label"), el.getAttribute("name"), el.placeholder, el.getAttribute("href"), el.getAttribute("action")].filter(Boolean).join(" | ");
        }));
      expect(controls.filter((c) => FORBIDDEN.test(c)), `controls on ${p}`).toEqual([]);
    }
  });

  test("research pages are usable on a phone (no horizontal overflow)", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page, "op1");
    for (const p of PAGES) {
      await gotoResearch(page, p);
      const o = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
      expect(o.sw, `${p}: ${o.sw} > ${o.cw}`).toBeLessThanOrEqual(o.cw + 1);
    }
    await gotoResearch(page, "/research/results");
    await page.screenshot({ path: `${SHOTS}/17-research-results-mobile.png`, fullPage: true });
  });
});
