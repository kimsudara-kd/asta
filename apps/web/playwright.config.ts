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
    channel: "chrome", // system Chrome: no browser download
    headless: true,
    screenshot: "off",
    trace: "off",
  },
});
