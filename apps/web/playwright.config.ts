import { defineConfig } from "@playwright/test";

// Run via `node e2e/run.mjs` (starts DB seed, API and web, then runs each phase).
export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.spec\.ts/,
  timeout: 60_000,
  expect: { timeout: 20_000 },
  workers: 1,
  fullyParallel: false,
  reporter: [["list"], ["json", { outputFile: "test-results/e2e-report.json" }]],
  use: {
    baseURL: "http://127.0.0.1:3640",
    // system Chrome by default (Windows/macOS, no download). On Linux ARM64 there is no Google Chrome build:
    // set E2E_BROWSER_CHANNEL=chromium to use the Playwright-installed Chromium instead.
    ...(process.env.E2E_BROWSER_CHANNEL === "chromium"
      ? {}
      : { channel: process.env.E2E_BROWSER_CHANNEL ?? "chrome" }),
    headless: true,
    screenshot: "off",
    trace: "off",
  },
});
