"use client";
import { useEffect, useId, useState } from "react";

export type Pt = { t: number; y: number };
export type Tone = "accent" | "muted" | "ok" | "warn" | "bad" | "violet";
export type Line = { name: string; points: Pt[]; tone: Tone; area?: boolean; dashed?: boolean };

export const COLOR: Record<Tone, string> = {
  accent: "var(--accent)", muted: "var(--muted)", ok: "var(--ok)", warn: "var(--warn)", bad: "var(--bad)", violet: "#9b7bff",
};

/** Y values drive GEOMETRY only; exact figures are always printed as text elsewhere. */
function extent(lines: Line[], pad = 0.08) {
  const pts = lines.flatMap((l) => l.points);
  const ys = pts.map((p) => p.y);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (y0 === y1) { y0 -= 1; y1 += 1; }
  const d = (y1 - y0) * pad;
  return { x0: Math.min(...pts.map((p) => p.t)), x1: Math.max(...pts.map((p) => p.t)), y0: y0 - d, y1: y1 + d };
}

/** Width of the element in px (ResizeObserver), so text in the chart keeps its true size on any screen. */
export function useWidth(fallback = 760): [(el: HTMLDivElement | null) => void, number] {
  // callback ref: the measuring effect re-runs whenever the element (re)mounts, e.g. when data arrives late
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  const [w, setW] = useState(fallback);
  useEffect(() => {
    if (!el) return;
    const apply = () => setW(Math.max(260, Math.round(el.clientWidth)));
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return [setEl, w];
}

/** Compact trend line for KPI tiles. */
export function Sparkline({ points, tone = "accent", label, height = 44 }: { points: Pt[]; tone?: Line["tone"]; label: string; height?: number }) {
  const gid = useId();
  if (points.length < 2) return <div className="spark-empty" aria-hidden="true" />;
  const W = 160, H = height;
  const { x0, x1, y0, y1 } = extent([{ name: "s", points, tone }], 0.12);
  const sx = (t: number) => ((t - x0) / (x1 - x0 || 1)) * W;
  const sy = (y: number) => H - ((y - y0) / (y1 - y0)) * H;
  const line = points.map((p, i) => `${i ? "L" : "M"}${sx(p.t).toFixed(1)},${sy(p.y).toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" style={{ stopColor: COLOR[tone], stopOpacity: 0.32 }} />
          <stop offset="1" style={{ stopColor: COLOR[tone], stopOpacity: 0 }} />
        </linearGradient>
      </defs>
      <path d={`${line} L${W},${H} L0,${H} Z`} fill={`url(#${gid})`} />
      <path d={line} fill="none" style={{ stroke: COLOR[tone] }} strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      <circle cx={sx(last.t)} cy={sy(last.y)} r="2.6" style={{ fill: COLOR[tone] }} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Multi-line chart with a soft area under the primary series, grid, and date axis. */
export function AreaChart({
  lines, height = 260, yFormat = (v: number) => v.toFixed(0), baseline, label, floor,
}: { lines: Line[]; height?: number; yFormat?: (v: number) => string; baseline?: number; label: string; floor?: number }) {
  const gid = useId();
  const [wrap, W] = useWidth();
  const all = lines.flatMap((l) => l.points);
  if (all.length < 2) return <div className="muted chart-empty">No data to chart yet.</div>;
  const pad = { l: 46, r: 8, t: 10, b: 24 };
  height = W < 520 ? Math.max(190, Math.round(W * 0.62)) : height;
  const ext = extent(lines);
  const { x0, x1, y1 } = ext;
  const y0 = floor !== undefined ? Math.max(floor, ext.y0) : ext.y0; // e.g. drawdown can never be below 0
  const sx = (t: number) => pad.l + ((t - x0) / (x1 - x0 || 1)) * (W - pad.l - pad.r);
  const sy = (y: number) => pad.t + (1 - (y - y0) / (y1 - y0)) * (height - pad.t - pad.b);
  const path = (pts: Pt[]) => pts.map((p, i) => `${i ? "L" : "M"}${sx(p.t).toFixed(1)},${sy(p.y).toFixed(1)}`).join(" ");
  const ticks = [0, 1, 2, 3].map((i) => y0 + ((y1 - y0) * i) / 3);
  const day = (t: number) => new Date(t).toISOString().slice(0, 10);
  const primary = lines.find((l) => l.area) ?? lines[0];
  return (
    <div ref={wrap} className="area-wrap">
    <svg className="area-chart" width={W} height={height} viewBox={`0 0 ${W} ${height}`} role="img" aria-label={label}>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" style={{ stopColor: COLOR[primary.tone], stopOpacity: 0.2 }} />
          <stop offset="0.75" style={{ stopColor: COLOR[primary.tone], stopOpacity: 0.03 }} />
          <stop offset="1" style={{ stopColor: COLOR[primary.tone], stopOpacity: 0 }} />
        </linearGradient>
      </defs>
      {ticks.map((v, i) => (
        <g key={i}>
          <line x1={pad.l} x2={W - pad.r} y1={sy(v)} y2={sy(v)} className="grid-line" />
          <text x={pad.l - 8} y={sy(v) + 4} textAnchor="end" className="axis-text">{yFormat(v)}</text>
        </g>
      ))}
      {baseline !== undefined && baseline > y0 && baseline < y1 && (
        <line x1={pad.l} x2={W - pad.r} y1={sy(baseline)} y2={sy(baseline)} className="baseline" />
      )}
      <text x={pad.l} y={height - 6} className="axis-text">{day(x0)}</text>
      <text x={W - pad.r} y={height - 6} textAnchor="end" className="axis-text">{day(x1)}</text>
      {primary.points.length > 1 && (
        <path d={`${path(primary.points)} L${sx(primary.points[primary.points.length - 1].t)},${sy(y0)} L${sx(primary.points[0].t)},${sy(y0)} Z`} fill={`url(#${gid})`} />
      )}
      {[...lines].sort((a, b) => Number(!!a.area) - Number(!!b.area)).map((l) => (
        <path
          key={l.name} d={path(l.points)} fill="none" data-series={l.name}
          style={{ stroke: COLOR[l.tone] }} strokeWidth={l.area ? 2.2 : 1.4} opacity={l.area ? 1 : 0.75}
          strokeDasharray={l.dashed ? "5 4" : undefined} strokeLinejoin="round" strokeLinecap="round"
        />
      ))}
    </svg>
    </div>
  );
}
