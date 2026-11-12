"use client";
import { type Risk } from "../../lib/api";
import { usePoll } from "../../lib/usePoll";
import Shell from "../Shell";

export default function RiskPage() {
  const { data, error } = usePoll<Risk>("/v1/risk");
  return (
    <Shell>
      <h1>Risk &amp; controls</h1>
      {error && <p className="err">{error}</p>}
      {data && (
        <>
          <div className="grid">
            <div className="card" data-testid="risk-newbuys">
              <div className="muted">New buys</div>
              <strong>{data.halt_new_buys ? "HALTED" : "No policy breach (execution still disabled)"}</strong>
              <div className="muted">{data.halt_reasons.join(", ") || "no breach"}</div>
            </div>
            <div className="card" data-testid="risk-gateway">
              <div className="muted">Execution gateway</div>
              <strong>{data.gateway_probe.decision}</strong>
              <div className="muted">{data.gateway_probe.reasons.join(", ")}</div>
            </div>
            <div className="card" data-testid="risk-live">
              <div className="muted">Live trading</div>
              <strong>{data.live_trading_enabled ? "ENABLED" : "DISABLED (hard-coded)"}</strong>
            </div>
          </div>
          <h2>Default policy (placeholders under test)</h2>
          <div className="card">
            <table>
              <tbody>
                {Object.entries(data.policy).map(([k, v]) => (
                  <tr key={k}>
                    <td>{k}</td>
                    <td>{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Shell>
  );
}
