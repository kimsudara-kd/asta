"use client";
import { useState, type FormEvent } from "react";
import { api, ApiError } from "../../lib/api";

export default function Login() {
  const [step, setStep] = useState<"password" | "mfa">("password");
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setErr(null);
    const f = new FormData(e.currentTarget);
    const json = { "Content-Type": "application/json" };
    try {
      if (step === "password") {
        const r = await api<{ mfa_required: boolean }>("/v1/auth/login", {
          method: "POST",
          headers: json,
          body: JSON.stringify({ username: f.get("username"), password: f.get("password") }),
        });
        if (r.mfa_required) setStep("mfa");
        else window.location.href = "/";
      } else {
        await api("/v1/auth/mfa", {
          method: "POST",
          headers: json,
          body: JSON.stringify({ code: f.get("code") }),
        });
        window.location.href = "/";
      }
    } catch (e2) {
      setErr(
        e2 instanceof ApiError && e2.status === 401
          ? "Invalid credentials or code."
          : "Sign-in failed.",
      );
    }
  }

  return (
    <>
      <div className="banner">PAPER · PUBLIC MARKET DATA ONLY · NO TRADING · NO REAL FUNDS</div>
      <main>
        <h1>ASTA sign-in</h1>
        <form onSubmit={submit}>
          {step === "password" ? (
            <>
              <input name="username" placeholder="Username" autoComplete="username" required />
              <input
                name="password"
                type="password"
                placeholder="Password"
                autoComplete="current-password"
                required
              />
            </>
          ) : (
            <input
              name="code"
              inputMode="numeric"
              pattern="[0-9]{6}"
              placeholder="6-digit authenticator code"
              autoComplete="one-time-code"
              required
            />
          )}
          <button type="submit">{step === "password" ? "Continue" : "Verify"}</button>
          {err && (
            <div className="err" role="alert">
              {err}
            </div>
          )}
        </form>
      </main>
    </>
  );
}
