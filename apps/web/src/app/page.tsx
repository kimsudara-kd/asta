"use client";
import { type Overview } from "../lib/api";
import { DATA_DISCLAIMER, stateLabel, stateTone } from "../lib/format";
import { usePoll } from "../lib/usePoll";
import Shell from "./Shell";

export default function OverviewPage() {
  const { data, error } = usePoll<Overview>("/v1/overview");
  return (
    <Shell>
      <h1>Overview</h1>
      {error && <p className="err">{error}</p>}
      {!data ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <div className="grid">
            <div className="card">
              <div className="muted">Environment</div>
              <strong>{data.environment}</strong>
              <div className="muted">{data.banner}</div>
            </div>
            <div className="card">
              <div className="muted">Simulated start equity</div>
              <strong>${data.equity.simulated_start}</strong>
              <div className="muted">
                P&amp;L {data.equity.pnl} · fee drag {data.equity.fee_drag} (no trades exist)
              </div>
            </div>
            <div className="card">
              <div className="muted">Execution</div>
              <strong>{data.risk.execution_enabled ? "ENABLED" : "DISABLED"}</strong>
              <div className="muted">Gateway probe: {data.risk.gateway_probe.decision}</div>
            </div>
            <div className="card">
              <div className="muted">Heartbeat</div>
              <strong>{data.heartbeat_utc.slice(11, 19)} UTC</strong>
              <div className="muted">
                up {data.uptime_seconds}s · {data.events_accepted} events · {data.duplicates_dropped}{" "}
                duplicates dropped
              </div>
            </div>
          </div>
          <p className="muted" data-testid="data-disclaimer">
            {DATA_DISCLAIMER}
          </p>
          <h2>Venues</h2>
          <div className="grid">
            {data.venues.map((v) => (
              <div className="card" key={v.venue} data-testid="venue-card">
                <strong>{v.venue}</strong>{" "}
                <span className={`pill ${stateTone(v.state)}`}>{stateLabel(v.state)}</span>
                <div className="muted">
                  {v.state === "FIXTURE"
                    ? "clock: n/a (fixture data)"
                    : `clock offset ${v.clock_offset_ms ?? "?"} ms ${v.clock_ok ? "(ok)" : "(NOT ok)"}`}{" "}
                  · reconnects {v.reconnects}
                  {v.gap_open ? " · data gap open" : ""}
                </div>
                {v.error && <div className="err">{v.error}</div>}
              </div>
            ))}
          </div>
        </>
      )}
    </Shell>
  );
}
