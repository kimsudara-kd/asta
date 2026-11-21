import { expect, test, type Page } from "@playwright/test";
import { login, SHOTS } from "./helpers";

// A visible control/link must never look like an execution surface. "Paper Orders" (plural noun in a
// heading/tab name) is a read-only list, not a verb; the verbs themselves stay forbidden.
const FORBIDDEN = /\b(buy|sell|place|order|trade|withdraw|transfer|deposit|execute|leverage|margin|borrow|enable live|go live)\b/i;
const BANNER = "PAPER TRADING — VIRTUAL FUNDS ONLY";
const PAGES = ["/paper", "/paper/agent", "/paper/portfolio", "/paper/orders", "/paper/risk", "/paper/performance"];

async function csrf(page: Page): Promise<string> {
  return (await (await page.request.get("/api/v1/auth/me")).json()).csrf_token;
}
async function gotoPaper(page: Page, path: string) {
  await page.goto(path);
  await expect(page.getByTestId("paper-banner")).toHaveText(BANNER);
  await expect(page.getByTestId("session-select")).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  page.on("pageerror", (e) => console.log("PAGEERROR", e.message.slice(0, 300)));
});

test.describe("@paper viewer: read-only", () => {
  test("viewer sees the session but has no create/control buttons, and the server refuses direct calls", async ({ page }) => {
    await login(page, "viewer1");
    await page.getByRole("link", { name: "Paper trading", exact: true }).click();
    await expect(page.getByTestId("paper-banner")).toHaveText(BANNER);
    await expect(page.getByTestId("session-state")).toContainText("RUNNING");
    await expect(page.getByTestId("create-note")).toContainText("view sessions but not create or control");
    for (const id of ["create-session", "ctl-start", "ctl-pause", "ctl-resume", "ctl-stop"]) await expect(page.getByTestId(id)).toHaveCount(0);
    const c = await csrf(page);
    const sid = (await (await page.request.get("/api/v1/paper/sessions")).json()).sessions[0].id;
    for (const path of [`/api/v1/paper/sessions/${sid}/pause`, `/api/v1/paper/sessions/${sid}/stop`, "/api/v1/paper/sessions"]) {
      const r = await page.request.post(path, { data: path.endsWith("sessions") ? { name: "x" } : {}, headers: { "X-CSRF-Token": c, "Idempotency-Key": "viewer-attempt-0001" }, failOnStatusCode: false });
      expect(r.status(), path).toBe(403);
    }
  });
});

test.describe("@paper agent page", () => {
  test("the Agent page explains the rule-based agent and lists its strategies, decisions and holdings (read-only)", async ({ page }) => {
    await login(page, "viewer1");
    await gotoPaper(page, "/paper/agent");
    await expect(page.getByTestId("agent-identity")).toContainText("rule-based");
    await expect(page.getByTestId("agent-identity")).toContainText("DENY");
    await expect(page.getByTestId("agent-strategies").locator("tbody tr").first()).toBeVisible();
    await expect(page.getByTestId("agent-holdings").locator("tbody tr").first()).toBeVisible();
    await expect(page.getByTestId("agent-signals")).toBeVisible();
    await expect(page.locator("main button, main [role=button]").filter({ hasText: FORBIDDEN })).toHaveCount(0);
    await page.screenshot({ path: `${SHOTS}/paper-agent.png`, fullPage: true });
  });
});

test.describe.serial("@paper operator: pages and session lifecycle", () => {
  test("overview: feeds, session status, equity, risk status, open paper orders, recent activity", async ({ page }) => {
    await login(page, "op1");
    await gotoPaper(page, "/paper");
    await expect(page.getByTestId("feed-BINANCE")).toContainText("CONNECTED");
    await expect(page.getByTestId("feed-OKX")).toContainText("CONNECTED");
    await expect(page.getByTestId("session-state")).toContainText("RUNNING");
    await expect(page.getByTestId("session-equity")).not.toHaveText("");
    await expect(page.getByTestId("session-card")).toContainText("none is a deployment candidate");
    await expect(page.getByTestId("risk-card")).toContainText("within limits");
    await expect(page.getByTestId("open-orders")).toBeVisible();
    await expect(page.getByTestId("recent-signals").locator("tbody tr").first()).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/20-paper-overview.png`, fullPage: true });
  });

  test("portfolio: totals match the overview; wallet slices; shadow benchmarks are not portfolio", async ({ page }) => {
    await login(page, "op1");
    await gotoPaper(page, "/paper");
    const eq = (await page.getByTestId("session-equity").innerText()).trim();
    await page.getByRole("tab", { name: "Paper Portfolio" }).click();
    await expect(page.getByTestId("pf-equity")).toHaveText(eq);
    const rows = page.getByTestId("pf-accounts").locator("tbody tr");
    await expect(rows).toHaveCount(6); // 2 venues x 3 strategies
    await expect(page.getByTestId("pf-accounts")).toContainText("BINANCE:regime_grid");
    await expect(page.getByTestId("pf-accounts")).not.toContainText("BENCH");
    await expect(page.getByTestId("pf-exposure")).toContainText("By venue");
    await page.screenshot({ path: `${SHOTS}/21-paper-portfolio.png`, fullPage: true });
  });

  test("signals and simulated orders: rejected signals carry reasons; filters work", async ({ page }) => {
    await login(page, "op1");
    await gotoPaper(page, "/paper/orders");
    await expect(page.getByRole("heading", { name: "Signals & Paper Orders" })).toBeVisible();
    await expect(page.getByTestId("signal-row").first()).toBeVisible();
    await page.getByTestId("sig-filter").selectOption("rej");
    await expect(page.getByTestId("signals-table")).not.toContainText("accepted", { timeout: 15000 });
    const reasons = await page.getByTestId("signals-table").locator("tbody tr td:last-child").allTextContents();
    expect(reasons.length).toBeGreaterThan(0);
    expect(reasons.every((r) => r.trim() !== "—" && r.trim() !== "")).toBeTruthy();
    await page.getByTestId("ord-filter").selectOption("FILLED");
    await expect(page.getByTestId("orders-table")).toContainText("FILLED", { timeout: 15000 });
    await page.screenshot({ path: `${SHOTS}/22-paper-signals-orders.png`, fullPage: true });
  });

  test("risk and incidents: limits, latched-halt policy, data health, incidents, acknowledge an alert", async ({ page }) => {
    await login(page, "op1");
    await gotoPaper(page, "/paper/risk");
    await expect(page.getByTestId("limits-card")).toContainText("Per asset ≤ 10.00%");
    await expect(page.getByTestId("limits-card")).toContainText("operator re-arm required");
    await expect(page.getByTestId("halts")).toContainText("no active halt");
    await expect(page.getByTestId("health-card")).toContainText("BINANCE:BTC-USDT");
    await expect(page.getByTestId("incidents-table")).toContainText("DATA_GAP");
    await page.getByTestId("alerts-table").getByRole("button", { name: "Acknowledge" }).first().click();
    await expect(page.getByTestId("alerts-table")).toContainText("acknowledged", { timeout: 15000 });
    await page.screenshot({ path: `${SHOTS}/23-paper-risk.png`, fullPage: true });
  });

  test("performance and reports: equity vs benchmarks, strategy table, daily report is reproducible", async ({ page }) => {
    await login(page, "op1");
    await gotoPaper(page, "/paper/performance");
    await expect(page.getByTestId("equity-chart")).toBeVisible();
    await expect(page.getByTestId("equity-chart").locator("[data-series]")).toHaveCount(4);
    await expect(page.getByTestId("perf-note")).toContainText("not evidence of a profitable edge");
    await expect(page.getByTestId("strategy-table")).toContainText("regime_grid");
    await expect(page.getByTestId("strategy-table").locator("tbody tr td:last-child")).toHaveText(["no", "no", "no"]);
    await page.getByTestId("report-day").fill("2026-01-15");
    await page.getByTestId("make-report").click();
    await expect(page.getByTestId("reports-table").locator("[data-testid=report-row]")).toHaveCount(1, { timeout: 20000 });
    await expect(page.getByTestId("report-row")).toContainText("2026-01-15");
    await expect(page.getByTestId("report-row")).toContainText("yes"); // reproducible
    await page.getByRole("button", { name: "Open report" }).click();
    await expect(page.getByTestId("report-detail")).toContainText("NO_STORED_OBSERVATIONS (fixture/test input)");
    await expect(page.getByTestId("report-detail")).toContainText("not evidence of a profitable edge");
    const dl = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export JSON" }).click();
    expect((await dl).suggestedFilename()).toBe("paper-report-2026-01-15.json");
    await page.screenshot({ path: `${SHOTS}/24-paper-performance.png`, fullPage: true });
  });

  test("session lifecycle from the UI: pause, resume (validated), then stop; create form is validated", async ({ page }) => {
    await login(page, "op1");
    await gotoPaper(page, "/paper");
    await page.getByTestId("ctl-pause").click();
    await expect(page.getByTestId("session-state")).toContainText("PAUSED", { timeout: 20000 });
    await gotoPaper(page, "/paper");
    await page.getByTestId("ctl-resume").click();
    await expect(page.getByTestId("session-state")).toContainText("RUNNING", { timeout: 20000 });
    // creating a second session (a feed is verified in this fixture DB) starts in INITIALIZING
    await gotoPaper(page, "/paper");
    await page.getByTestId("new-name").fill("second session");
    await page.getByTestId("create-session").click();
    await expect(page.getByTestId("create-msg")).toContainText("Session created");
    await gotoPaper(page, "/paper");
    await page.getByTestId("session-select").selectOption({ label: "e2e fixture session · RUNNING" });
    await page.getByTestId("ctl-stop").click();
    await expect(page.getByTestId("session-state")).toContainText("STOPPED", { timeout: 20000 });
    await expect(page.getByTestId("ctl-start")).toBeDisabled();
    await page.screenshot({ path: `${SHOTS}/25-paper-stopped.png`, fullPage: true });
  });

  test("paper pages show the banner and expose no execution controls", async ({ page }) => {
    await login(page, "op1");
    for (const p of PAGES) {
      await gotoPaper(page, p);
      const controls = await page
        .locator("button, a, input, select, textarea, [role=button], [role=link], form")
        .evaluateAll((els) => els.map((e) => {
          const el = e as HTMLElement & { placeholder?: string };
          return [el.innerText, el.getAttribute("aria-label"), el.getAttribute("name"), el.placeholder, el.getAttribute("href"), el.getAttribute("action")].filter(Boolean).join(" | ");
        }));
      expect(controls.filter((c) => FORBIDDEN.test(c)), `controls on ${p}`).toEqual([]);
    }
  });

  test("paper pages are usable on a phone (no horizontal overflow)", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page, "op1");
    for (const p of PAGES) {
      await gotoPaper(page, p);
      const o = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
      expect(o.sw, `${p}: ${o.sw} > ${o.cw}`).toBeLessThanOrEqual(o.cw + 1);
    }
    await page.screenshot({ path: `${SHOTS}/26-paper-mobile.png`, fullPage: true });
  });
});
