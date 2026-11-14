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
    <div className="auth">
      <div className="banner">PAPER · PUBLIC MARKET DATA ONLY · NO TRADING · NO REAL FUNDS</div>
      <div className="auth-stage">
        <main className="card auth-card">
          <div className="brand" aria-hidden="true">
            <span className="brand-mark">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 18l5.5-7 4 4L20 6" />
              </svg>
            </span>
            <span className="brand-name">
              ASTA<span className="brand-sub">Spot research platform</span>
            </span>
          </div>
          <h1>ASTA sign-in</h1>
          <p className="muted">
            {step === "password"
              ? "Sign in to the research and paper-trading console."
              : "Enter the 6-digit code from your authenticator app."}
          </p>
          <form onSubmit={submit}>
            {step === "password" ? (
              <>
                <input
                  name="username"
                  placeholder="Username"
                  aria-label="Username"
                  autoComplete="username"
                  autoFocus
                  required
                />
                <input
                  name="password"
                  type="password"
                  placeholder="Password"
                  aria-label="Password"
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
                aria-label="Authenticator code"
                autoComplete="one-time-code"
                autoFocus
                required
              />
            )}
            <button type="submit">{step === "password" ? "Continue" : "Verify"}</button>
            {err && (
              <div className="err" role="alert" data-testid="login-error">
                {err}
              </div>
            )}
          </form>
          <p className="muted auth-foot">Simulation only. No exchange credentials, no orders, no real funds.</p>
        </main>
      </div>
    </div>
  );
}
