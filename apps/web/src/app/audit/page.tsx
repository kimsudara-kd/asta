"use client";
import { type Audit } from "../../lib/api";
import { usePoll } from "../../lib/usePoll";
import Shell from "../Shell";

export default function AuditPage() {
  const { data, error } = usePoll<Audit>("/v1/audit?limit=100", 15000);
  return (
    <Shell>
      <h1>Audit trail</h1>
      {error && (
        <p className="err">
          {error === "Request failed (403)" ? "Your role cannot view the audit trail." : error}
        </p>
      )}
      {data && (
        <>
          <p>
            Hash chain:{" "}
            <span className={`pill ${data.chain_ok ? "ok" : "bad"}`}>
              {data.chain_ok ? "verified" : `BROKEN at ${data.first_bad_index}`}
            </span>
          </p>
          <div className="card scroll">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Time (UTC)</th>
                  <th>Actor</th>
                  <th>Action</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {data.events.map((e) => (
                  <tr key={e.id}>
                    <td>{e.id}</td>
                    <td>{e.at.slice(0, 19).replace("T", " ")}</td>
                    <td>{e.actor}</td>
                    <td>{e.action}</td>
                    <td className="muted">{JSON.stringify(e.payload)}</td>
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
