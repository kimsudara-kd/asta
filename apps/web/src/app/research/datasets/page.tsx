"use client";
import { useState } from "react";
import ResearchShell, { usePrincipal } from "../../../components/ResearchShell";
import { api } from "../../../lib/api";
import {
  canWriteResearch,
  explain,
  post,
  type DatasetRow,
  type ImportableFile,
  type QualityReport,
} from "../../../lib/research";
import { usePoll } from "../../../lib/usePoll";

function Pill({ status }: { status: string }) {
  const tone = status === "PASS" ? "ok" : status === "WARNING" ? "warn" : "bad";
  return <span className={`pill ${tone}`}>{status}</span>;
}

export default function DatasetsPage() {
  const me = usePrincipal();
  const list = usePoll<{ datasets: DatasetRow[] }>("/v1/datasets", 8000);
  const files = usePoll<{ files: ImportableFile[]; note: string }>("/v1/datasets/importable", 15000);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<{ ds: DatasetRow; reports: { status: string; report: QualityReport; created_at: string }[] } | null>(null);
  const [tf, setTf] = useState<Record<string, string>>({});
  const canWrite = canWriteResearch(me?.role);

  async function act(label: string, fn: () => Promise<unknown>) {
    if (!me) return;
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      setMsg(`${label}: done.`);
    } catch (e) {
      setMsg(`${label}: ${await explain(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function open(ds: DatasetRow) {
    const q = await api<{ reports: { status: string; report: QualityReport; created_at: string }[] }>(`/v1/datasets/${ds.version_id}/quality`);
    setDetail({ ds, reports: q.reports });
  }

  return (
    <ResearchShell active="/research/datasets">
      <h1>Historical datasets</h1>
      <p className="muted">
        Datasets are imported from LOCAL files only and are content-addressed (checksum). Synthetic or fixture
        data is always labelled and is never treated as real exchange history.
      </p>
      {msg && <p role="status" data-testid="dataset-msg">{msg}</p>}

      <div className="card scroll">
        <table data-testid="dataset-table">
          <thead>
            <tr>
              <th>Venue</th><th>Market</th><th>TF</th><th>Source type</th><th>Period (UTC)</th><th>Rows</th>
              <th>Quality</th><th>Research-grade</th><th>Checksum</th><th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {(list.data?.datasets ?? []).map((d) => (
              <tr key={d.version_id} data-testid="dataset-row">
                <td>{d.venue}</td>
                <td>{d.symbol}</td>
                <td>{d.timeframe}</td>
                <td>
                  <span className={`pill ${d.accepted_for_research ? "ok" : "warn"}`} data-testid="dataset-label">{d.label}</span>
                </td>
                <td>{d.period_start.slice(0, 10)} → {d.period_end.slice(0, 10)}</td>
                <td>{d.row_count}</td>
                <td><Pill status={d.validation_status} /></td>
                <td>{d.accepted_for_research ? "yes" : "no"}</td>
                <td className="mono">{d.checksum.slice(0, 12)}</td>
                <td>
                  <button onClick={() => open(d)}>View quality</button>{" "}
                  {canWrite && (
                    <>
                      <button disabled={busy} onClick={() => act("Validate", () => post(`/v1/datasets/${d.version_id}/validate`, {}, me!.csrf_token))}>
                        Validate
                      </button>{" "}
                      <select aria-label="Derived timeframe" value={tf[d.version_id] ?? "1h"} onChange={(e) => setTf({ ...tf, [d.version_id]: e.target.value })}>
                        {["15m", "1h", "4h", "1d"].map((x) => <option key={x}>{x}</option>)}
                      </select>{" "}
                      <button disabled={busy} onClick={() => act("Derive", () => post(`/v1/datasets/${d.version_id}/derive`, { timeframe: tf[d.version_id] ?? "1h" }, me!.csrf_token))}>
                        Derive timeframe
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
            {list.data && list.data.datasets.length === 0 && (
              <tr><td colSpan={10} className="muted">No datasets imported yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <h2>Import from local files</h2>
      <p className="muted" data-testid="import-note">{files.data?.note ?? ""} {!canWrite && "Your role can view datasets but not import them."}</p>
      <div className="card scroll">
        <table>
          <thead><tr><th>File</th><th>Market</th><th>TF</th><th>Declared source type</th><th>Size</th><th /></tr></thead>
          <tbody>
            {(files.data?.files ?? []).map((f) => (
              <tr key={f.file_name} data-testid="importable-row">
                <td className="mono">{f.file_name}</td>
                <td>{f.symbol}</td>
                <td>{f.timeframe}</td>
                <td>{f.source_type}</td>
                <td>{(f.size_bytes / 1024).toFixed(0)} KB</td>
                <td>
                  {canWrite && (
                    <button disabled={busy} onClick={() => act(`Import ${f.file_name}`, () => post("/v1/datasets/import", { file_name: f.file_name }, me!.csrf_token))}>
                      Import dataset
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {files.data && files.data.files.length === 0 && (
              <tr><td colSpan={6} className="muted">No importable files found in the import directory.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {detail && (
        <div className="card" data-testid="quality-detail">
          <h2>Quality report — {detail.ds.symbol} {detail.ds.timeframe}</h2>
          <div className="kv">
            <div>Status</div><div><Pill status={detail.ds.validation_status} /></div>
            <div>Source</div><div>{detail.ds.source}</div>
            <div>Source type</div><div>{detail.ds.label}</div>
            <div>Checksum</div><div className="mono">{detail.ds.checksum}</div>
            <div>Imported</div><div>{detail.ds.imported_at} by {detail.ds.imported_by}</div>
            <div>Provenance</div><div className="mono">{JSON.stringify(detail.ds.provenance)}</div>
            {detail.ds.parent_checksums.length > 0 && (<><div>Derived from</div><div className="mono">{detail.ds.parent_checksums.join(", ")}</div></>)}
          </div>
          {detail.reports.map((r, i) => (
            <div key={i}>
              <h3>Report {i + 1} <Pill status={r.status} /> <span className="muted">{r.created_at.slice(0, 19)}</span></h3>
              <p className="muted">Stats: {JSON.stringify(r.report.stats)}</p>
              {r.report.findings.length === 0 ? <p>No findings.</p> : r.report.findings.map((f) => (
                <div key={f.code} className={f.severity === "REJECTED" ? "badbox" : "warnbox"}>
                  <strong>{f.code}</strong> ({f.severity}) — {f.message} [{f.count}] {f.examples.join("; ")}
                </div>
              ))}
            </div>
          ))}
          <p className="muted">Missing candles are reported, never filled. A REJECTED dataset is retained for diagnosis and cannot be used for results.</p>
          <button onClick={() => setDetail(null)}>Close</button>
        </div>
      )}
    </ResearchShell>
  );
}
