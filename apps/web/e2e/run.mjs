// Browser-acceptance orchestrator (local, disposable). Usage (from apps/web):
//   ASTA_E2E_PG=postgresql+pg8000://asta_test:asta_test_only@127.0.0.1:5641 node e2e/run.mjs
// Creates a throw-away database `asta_e2e` on the compose TEST postgres, seeds users, starts the
// API (3 configurations) and the web server, runs Playwright (system Chrome) per phase, then
// stops only the processes it started, deletes the secrets file and drops the e2e database.
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, rmSync } from "node:fs";
import path from "node:path";

const web = path.resolve(import.meta.dirname, "..");
const repo = path.resolve(web, "../..");
const py = path.join(repo, ".venv", "Scripts", "python.exe");
const secrets = path.join(import.meta.dirname, ".secrets.json");
const pgBase = process.env.ASTA_E2E_PG ?? "postgresql+pg8000://asta_test:asta_test_only@127.0.0.1:5641";
const dbUrl = `${pgBase}/asta_e2e`;
const baseEnv = {
  ...process.env,
  ASTA_DATABASE_URL: dbUrl,
  ASTA_SECRET_KEY: "e2e-only-secret-key-not-a-real-secret",
  ASTA_API_PORT: "4640",
  ASTA_DATA_DIR: path.join(repo, "data", "e2e", "store"),
  ASTA_IMPORT_DIR: path.join(repo, "data", "e2e", "import"),
  ASTA_ARGON2_MEMORY_KIB: "7168",
  ASTA_ARGON2_TIME_COST: "5",
  PYTHONUTF8: "1",
};
const started = [];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { stdio: "inherit", ...opts });
  if (r.status !== 0) throw new Error(`${cmd} ${args.join(" ")} -> exit ${r.status}`);
}
function psql(sql) {
  return spawnSync("docker", ["exec", "asta-db-test", "psql", "-U", "asta_test", "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-c", sql], { stdio: "inherit" });
}
function start(name, cmd, args, opts) {
  const child = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"], ...opts });
  child.stdout.on("data", (d) => process.stdout.write(`[${name}] ${d}`));
  child.stderr.on("data", (d) => process.stdout.write(`[${name}] ${d}`));
  started.push(child);
  return child;
}
function stop(child) {
  if (child && child.exitCode === null) spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
}
async function waitFor(url, ms = 90_000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try { if ((await fetch(url)).ok) return; } catch { /* retry */ }
    await sleep(500);
  }
  throw new Error(`timeout waiting for ${url}`);
}
async function phase(label, grep, apiEnv) {
  console.log(`\n===== PHASE ${label} =====`);
  const api = start(`api:${label}`, py, ["-u", "-m", "asta.main"], { cwd: repo, env: { ...baseEnv, ...apiEnv } });
  try {
    await waitFor("http://127.0.0.1:4640/health");
    const r = spawnSync(process.execPath, [path.join(web, "node_modules/@playwright/test/cli.js"), "test", "--grep", grep],
      { cwd: web, stdio: "inherit", env: process.env });
    return r.status === 0;
  } finally {
    stop(api);
    await sleep(1500);
  }
}

let ok = false;
let webProc;
try {
  mkdirSync(path.resolve(repo, "docs/evidence/m2/screenshots"), { recursive: true });
  psql("DROP DATABASE IF EXISTS asta_e2e WITH (FORCE)");
  if (psql("CREATE DATABASE asta_e2e").status !== 0) throw new Error("cannot create asta_e2e");
  rmSync(path.join(repo, "data", "e2e"), { recursive: true, force: true });
  run(py, [path.join(repo, "scripts/e2e_seed.py"), secrets, path.join(repo, "data", "e2e", "import")], { cwd: repo, env: baseEnv });
  const onlyPre = (process.env.E2E_PHASES ?? "").split(",").filter(Boolean);
  if (onlyPre.length === 0 || onlyPre.includes("paper")) run(py, [path.join(repo, "scripts/e2e_seed_paper.py")], { cwd: repo, env: baseEnv });

  run(process.execPath, [path.join(web, "node_modules/next/dist/bin/next"), "build"], { cwd: web });
  webProc = start("web", process.execPath, [path.join(web, "node_modules/next/dist/bin/next"), "start", "-H", "127.0.0.1", "-p", "3640"], { cwd: web });
  await waitFor("http://127.0.0.1:3640/login");

  const results = [];
  const only = (process.env.E2E_PHASES ?? "").split(",").filter(Boolean); // e.g. E2E_PHASES=research
  const want = (n) => only.length === 0 || only.includes(n);
  if (want("live")) results.push(["live", await phase("live", "@live", { ASTA_FIXTURE_VENUES: "OKX" })]);
  if (want("nodata")) results.push(["nodata", await phase("nodata", "@nodata", { ASTA_ENABLE_MARKET_INGEST: "false", ASTA_FIXTURE_VENUES: "" })]);
  if (want("aging")) results.push(["aging", await phase("aging", "@aging", { ASTA_FIXTURE_VENUES: "BINANCE,OKX", ASTA_STALE_AFTER_SECONDS: "1" })]);
  if (want("research")) results.push(["research", await phase("research", "@research", { ASTA_ENABLE_MARKET_INGEST: "false", ASTA_FIXTURE_VENUES: "" })]);
  if (want("paper")) results.push(["paper", await phase("paper", "@paper", { ASTA_ENABLE_MARKET_INGEST: "false", ASTA_FIXTURE_VENUES: "" })]);
  console.log("\nPHASE RESULTS:", JSON.stringify(results));
  ok = results.every(([, v]) => v);
} catch (e) {
  console.error("E2E ORCHESTRATION ERROR:", e);
} finally {
  for (const c of started) stop(c);
  if (existsSync(secrets)) rmSync(secrets);
  psql("DROP DATABASE IF EXISTS asta_e2e WITH (FORCE)");
}
process.exit(ok ? 0 : 1);
