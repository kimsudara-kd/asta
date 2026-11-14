"use client";
import { useId, useState } from "react";
import { COLOR, useWidth, type Tone } from "./Charts";
import { nearest, type Sample } from "../lib/marketHistory";

export type PriceLine = { name: string; tone: Tone; samples: Sample[] };

/** Y-axis label precision from the visible range (decoration only; exact prices are printed as text elsewhere). */
function fmt(v: number, span: number): string {
  const dp = span < 0.05 ? 4 : span < 5 ? 3 : 2;
  return v.toFixed(dp);
}

/** Interactive multi-venue price chart: area under the first line, last-point pulse, hover crosshair + readout. */
export default function PriceChart({ lines, height = 300, label }: { lines: PriceLine[]; height?: number; label: string }) {
  const gid = useId();
  const [wrap, W] = useWidth();
  const [hoverX, setHoverX] = useState<number | null>(null);
  const all = lines.flatMap((l) => l.samples);
  if (all.length < 2) {
    return <div className="chart-empty muted">Collecting prices… the chart fills in as quotes arrive ({all.length} sample{all.length === 1 ? "" : "s"}).</div>;
  }
  const pad = { l: 64, r: 14, t: 12, b: 26 };
  const H = W < 520 ? Math.max(210, Math.round(W * 0.7)) : height;
  const xs = all.map((s) => s.t);
  const ys = all.map((s) => s.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (y0 === y1) { const d = Math.max(Math.abs(y0) * 0.0004, 0.01); y0 -= d; y1 += d; } else { const d = (y1 - y0) * 0.12; y0 -= d; y1 += d; }
  const sx = (t: number) => pad.l + ((t - x0) / (x1 - x0 || 1)) * (W - pad.l - pad.r);
  const sy = (y: number) => pad.t + (1 - (y - y0) / (y1 - y0)) * (H - pad.t - pad.b);
  const path = (s: Sample[]) => s.map((p, i) => `${i ? "L" : "M"}${sx(p.t).toFixed(1)},${sy(p.y).toFixed(1)}`).join(" ");
  const ticks = [0, 1, 2, 3].map((i) => y0 + ((y1 - y0) * i) / 3);
  const clock = (t: number) => new Date(t).toISOString().slice(11, 19);
  const first = lines[0].samples;

  const hoverT = hoverX === null ? null : x0 + ((hoverX - pad.l) / (W - pad.l - pad.r)) * (x1 - x0);
  const hits = hoverT === null ? [] : lines.map((l) => { const i = nearest(l.samples, hoverT); return { line: l, s: i < 0 ? null : l.samples[i] }; });
  const hoverPx = hoverT === null || hits.every((h) => h.s === null) ? null : sx(hits.find((h) => h.s)!.s!.t);

  function onMove(e: React.PointerEvent<HTMLDivElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    setHoverX(Math.min(W - pad.r, Math.max(pad.l, e.clientX - r.left)));
  }

  return (
    <div ref={wrap} className="pc-wrap" onPointerMove={onMove} onPointerLeave={() => setHoverX(null)}>
      <svg className="pc" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" style={{ stopColor: COLOR[lines[0].tone], stopOpacity: 0.22 }} />
            <stop offset="1" style={{ stopColor: COLOR[lines[0].tone], stopOpacity: 0 }} />
          </linearGradient>
        </defs>
        {ticks.map((v, i) => (
          <g key={i}>
            <line x1={pad.l} x2={W - pad.r} y1={sy(v)} y2={sy(v)} className="grid-line" />
            <text x={pad.l - 10} y={sy(v) + 4} textAnchor="end" className="axis-text">{fmt(v, y1 - y0)}</text>
          </g>
        ))}
        <text x={pad.l} y={H - 7} className="axis-text">{clock(x0)}</text>
        <text x={W - pad.r} y={H - 7} textAnchor="end" className="axis-text">{clock(x1)}</text>
        {first.length > 1 && <path d={`${path(first)} L${sx(first[first.length - 1].t)},${H - pad.b} L${sx(first[0].t)},${H - pad.b} Z`} fill={`url(#${gid})`} />}
        {lines.map((l) => l.samples.length > 1 && (
          <path key={l.name} d={path(l.samples)} fill="none" data-series={l.name} style={{ stroke: COLOR[l.tone] }} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        ))}
        {lines.map((l) => { const last = l.samples[l.samples.length - 1]; return last && (
          <g key={`dot-${l.name}`}>
            <circle cx={sx(last.t)} cy={sy(last.y)} r="9" className="pc-pulse" style={{ fill: COLOR[l.tone] }} />
            <circle cx={sx(last.t)} cy={sy(last.y)} r="3.6" style={{ fill: COLOR[l.tone] }} />
          </g>); })}
        {hoverPx !== null && (
          <g>
            <line x1={hoverPx} x2={hoverPx} y1={pad.t} y2={H - pad.b} className="pc-cross" />
            {hits.map((h) => h.s && <circle key={h.line.name} cx={sx(h.s.t)} cy={sy(h.s.y)} r="4.5" className="pc-hit" style={{ stroke: COLOR[h.line.tone] }} />)}
          </g>
        )}
      </svg>
      {hoverPx !== null && (
        <div className="pc-tip" style={{ left: hoverPx > W * 0.62 ? undefined : hoverPx + 14, right: hoverPx > W * 0.62 ? W - hoverPx + 14 : undefined }} role="status" aria-live="off">
          <div className="pc-tip-t">{clock(hits.find((h) => h.s)!.s!.t)} UTC</div>
          {hits.map((h) => h.s && (
            <div key={h.line.name} className="pc-tip-row"><i style={{ background: COLOR[h.line.tone] }} />{h.line.name}<b>{h.s.text}</b></div>
          ))}
        </div>
      )}
    </div>
  );
}
