import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, type Page } from "@playwright/test";

type Creds = Record<string, { password: string; role: string; totp_secret?: string }>;
export const creds: Creds = JSON.parse(
  readFileSync(path.join(__dirname, ".secrets.json"), "utf-8"),
);

export const SHOTS = path.resolve(
  __dirname,
  "../../../docs/evidence/m0-r1/screenshots",
);

const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
function base32Decode(s: string): Buffer {
  let bits = "";
  for (const c of s.replace(/=+$/, "").toUpperCase()) bits += B32.indexOf(c).toString(2).padStart(5, "0");
  const bytes: number[] = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}

/** RFC 6238 TOTP (SHA-1, 30 s, 6 digits). */
export function totp(secret: string, atMs = Date.now()): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(atMs / 1000 / 30)));
  const h = createHmac("sha1", base32Decode(secret)).update(counter).digest();
  const o = h[h.length - 1] & 0xf;
  const n = ((h[o] & 0x7f) << 24) | (h[o + 1] << 16) | (h[o + 2] << 8) | h[o + 3];
  return String(n % 1_000_000).padStart(6, "0");
}

export async function login(page: Page, user: string): Promise<void> {
  const c = creds[user];
  await page.goto("/login");
  await page.getByPlaceholder("Username").fill(user);
  await page.getByPlaceholder("Password").fill(c.password);
  await page.getByRole("button", { name: "Continue" }).click();
  if (c.totp_secret) {
    await expect(page.getByPlaceholder("6-digit authenticator code")).toBeVisible();
    await page.getByPlaceholder("6-digit authenticator code").fill(totp(c.totp_secret));
    await page.getByRole("button", { name: "Verify" }).click();
  }
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

export const PAGES = ["/", "/markets", "/risk", "/audit"] as const;
