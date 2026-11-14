"use client";
import { useMemo, useState } from "react";
import { Skeleton, Tile } from "../../components/paper/ui";
import { type Audit } from "../../lib/api";
import { usePoll } from "../../lib/usePoll";
import Shell from "../Shell";

const tone = (action: string) =>
  /failed|denied|locked|revoke/.test(action) ? "bad" : /^auth\./.test(action) ? "ok" : /create|enroll|update|reset|rearm|import/.test(action) ? "warn" : "na";

export default function AuditPage() {
  const { data, error } = usePoll<Audit>("/v1/audit?limit=100", 15000);
  const [actor, setActor] = useState("");
  const [kind, setKind] = useState("");
  const events = data?.events ?? [];
  const actors = useMemo(() => [...new Set(events.map((e) => e.actor))].sort(), [events]);
  const kinds = useMemo(() => [...new Set(events.map((e) => e.action.split(".")[0]))].sort(), [events]);
  const shown = events.filter((e) => (!actor || e.actor === actor) && (!kind || e.action.startsWith(`${kind}.`)));
  const failures = events.filter((e) => /failed|denied|locked/.test(e.action)).length;
  const last = events[0] ?? null;
  return (
    <Shell>
      <h1>Audit trail</h1>
      <p className="muted">An append-only, hash-chained record of who did what. Secrets are never written to it.</p>
      {error && (
        <p className="err">
          {error === "Request failed (403)" ? "Your role cannot view the audit trail." : error}
        </p>
      )}
      {!data && !error && <div className="grid kpis"><Skeleton h={110} /><Skeleton h={110} /><Skeleton h={110} /><Skeleton h={110} /></div>}
      {data && (
        <>
          <div className="grid kpis">
            <Tile label="Hash chain" sub={data.chain_ok ? "every entry links to the one before it" : "tampering or corruption detected"}>
              <span className={`pill ${data.chain_ok ? "ok" : "bad"}`}>{data.chain_ok ? "verified" : `BROKEN at ${data.first_bad_index}`}</span>
            </Tile>
            <Tile label="Events loaded" sub="newest 100">{events.length.toLocaleString("en-US")}</Tile>
            <Tile label="Failed sign-in or checks" sub="in the loaded window"><span className={failures ? "neg" : "pos"}>{failures}</span></Tile>
            <Tile label="Latest event" sub={last ? `${last.actor} · ${last.at.slice(0, 19).replace("T", " ")} UTC` : "—"}>{last ? last.action : "—"}</Tile>
          </div>
          <section className="card">
            <div className="card-head">
              <h2 className="card-title">Events</h2>
              <div className="row" style={{ margin: 0 }}>
                <label className="inline-filter">Area
                  <select value={kind} onChange={(e) => setKind(e.target.value)}>
                    <option value="">all</option>{kinds.map((k) => <option key={k}>{k}</option>)}
                  </select>
                </label>
                <label className="inline-filter">Actor
                  <select value={actor} onChange={(e) => setActor(e.target.value)}>
                    <option value="">all</option>{actors.map((a) => <option key={a}>{a}</option>)}
                  </select>
                </label>
              </div>
            </div>
            <div className="scroll tall">
              <table>
                <thead>
                  <tr><th className="num">#</th><th>Time (UTC)</th><th>Actor</th><th>Action</th><th>Detail</th></tr>
                </thead>
                <tbody>
                  {shown.map((e) => (
                    <tr key={e.id}>
                      <td className="num">{e.id}</td>
                      <td className="nowrap">{e.at.slice(0, 19).replace("T", " ")}</td>
                      <td>{e.actor}</td>
                      <td><span className={`pill ${tone(e.action)}`}>{e.action}</span></td>
                      <td className="wrap muted mono">{JSON.stringify(e.payload)}</td>
                    </tr>
                  ))}
                  {shown.length === 0 && <tr><td colSpan={5} className="muted">No events match.</td></tr>}
                </tbody>
              </table>
            </div>
            <p className="muted table-cap">{shown.length.toLocaleString("en-US")} of {events.length.toLocaleString("en-US")} loaded events shown (newest first).</p>
          </section>
        </>
      )}
    </Shell>
  );
}
