import { expect, test } from "@playwright/test";
import { login, SHOTS } from "./helpers";

test.describe("@live audit trail and role visibility", () => {
  test("reviewer sees the verified hash chain with the earlier auth events", async ({ page }) => {
    await login(page, "rev1");
    await page.getByRole("link", { name: "Audit" }).click();
    await expect(page.getByRole("heading", { name: "Audit trail" })).toBeVisible();
    await expect(page.getByText("verified", { exact: true })).toBeVisible();
    const table = page.locator("table");
    for (const action of ["auth.password_ok", "auth.mfa_ok", "auth.login_failed", "auth.mfa_failed", "user.create", "mfa.enroll"]) {
      await expect(table).toContainText(action);
    }
    // secrets never appear in the audit payloads
    const text = await table.innerText();
    expect(text).not.toMatch(/password"?\s*:/i);
    expect(text).not.toMatch(/totp_secret|otpauth/i);
    await page.screenshot({ path: `${SHOTS}/06-audit-reviewer.png`, fullPage: true });
  });

  test("viewer and operator cannot see or open the audit trail", async ({ page }) => {
    for (const u of ["viewer1", "op1"]) {
      await page.context().clearCookies();
      await login(page, u);
      await expect(page.getByRole("link", { name: "Audit" })).toHaveCount(0);
      await page.goto("/audit");
      await expect(page.getByText("Your role cannot view the audit trail.")).toBeVisible();
      expect((await page.request.get("/api/v1/audit")).status()).toBe(403);
    }
  });
});
