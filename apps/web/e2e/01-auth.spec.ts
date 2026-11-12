import { expect, test } from "@playwright/test";
import { creds, login, SHOTS, totp } from "./helpers";

test.describe("@live authentication and MFA", () => {
  test("protected pages redirect an anonymous visitor to sign-in", async ({ page }) => {
    for (const p of ["/", "/markets", "/risk", "/audit"]) {
      await page.goto(p);
      await expect(page).toHaveURL(/\/login$/);
    }
    await expect(page.getByRole("heading", { name: "ASTA sign-in" })).toBeVisible();
    await expect(page.getByTestId("banner").or(page.locator(".banner"))).toContainText("NO TRADING");
    await page.screenshot({ path: `${SHOTS}/01-login-desktop.png` });
  });

  test("wrong password is rejected with a generic message", async ({ page }) => {
    await page.goto("/login");
    await page.getByPlaceholder("Username").fill("viewer1");
    await page.getByPlaceholder("Password").fill("definitely-not-the-password");
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByTestId("login-error")).toHaveText("Invalid credentials or code.");
    await expect(page).toHaveURL(/\/login$/);
  });

  test("operator needs MFA: password alone gives no access; wrong code fails; right code works", async ({
    page,
  }) => {
    const c = creds["op1"];
    await page.goto("/login");
    await page.getByPlaceholder("Username").fill("op1");
    await page.getByPlaceholder("Password").fill(c.password);
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByPlaceholder("6-digit authenticator code")).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/02-mfa-prompt.png` });

    // password-only session must not open the dashboard
    const direct = await page.request.get("/api/v1/overview");
    expect(direct.status()).toBe(401);

    await page.getByPlaceholder("6-digit authenticator code").fill("000000");
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByTestId("login-error")).toHaveText("Invalid credentials or code.");

    await page.getByPlaceholder("6-digit authenticator code").fill(totp(c.totp_secret!));
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
    await expect(page.getByText("op1 (operator)")).toBeVisible();
  });

  test("viewer (no MFA role) signs in; sign-out ends the session", async ({ page }) => {
    await login(page, "viewer1");
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login$/);
    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);
    expect((await page.request.get("/api/v1/overview")).status()).toBe(401);
  });

  test("session cookie is HttpOnly and SameSite=Strict (not readable by page scripts)", async ({
    page,
    context,
  }) => {
    await login(page, "viewer1");
    const cookies = await context.cookies();
    const s = cookies.find((c) => c.name === "asta_session");
    expect(s).toBeTruthy();
    expect(s!.httpOnly).toBe(true);
    expect(s!.sameSite).toBe("Strict");
    expect(await page.evaluate(() => document.cookie)).not.toContain("asta_session");
  });
});
