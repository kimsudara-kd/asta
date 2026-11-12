"use client";

export type Series = { name: string; color: string; points: { t: number; y: number }[] };

/** Dependency-free SVG line chart. Y values are used for GEOMETRY only (display); exact decimal
 *  figures are always shown as text elsewhere. */
export default function LineChart({
  title,
  series,
  height = 220,
  yFormat = (v: number) => v.toFixed(2),
  testId,
}: {
  title: string;
  series: Series[];
  height?: number;
  yFormat?: (v: number) => string;
  testId?: string;
}) {
  const W = 800;
  const pad = { l: 56, r: 12, t: 12, b: 22 };
  const all = series.flatMap((s) => s.points);
  if (all.length < 2) return <div className="muted" data-testid={testId}>No data to chart.</div>;
  const xs = all.map((p) => p.t);
  const ys = all.map((p) => p.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (y0 === y1) { y0 -= 1; y1 += 1; }
  const sx = (t: number) => pad.l + ((t - x0) / (x1 - x0 || 1)) * (W - pad.l - pad.r);
  const sy = (y: number) => pad.t + (1 - (y - y0) / (y1 - y0)) * (height - pad.t - pad.b);
  const d = (s: Series) => s.points.map((p, i) => `${i ? "L" : "M"}${sx(p.t).toFixed(1)},${sy(p.y).toFixed(1)}`).join(" ");
  const label = `${title}: ${series.map((s) => s.name).join(", ")}`;
  const ticks = [y0, (y0 + y1) / 2, y1];
  return (
    <figure style={{ margin: 0 }} data-testid={testId}>
      <figcaption className="muted">{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label={label} style={{ width: "100%", height: "auto" }}>
        {ticks.map((v, i) => (
          <g key={i}>
            <line x1={pad.l} x2={W - pad.r} y1={sy(v)} y2={sy(v)} stroke="currentColor" opacity={0.12} />
            <text x={pad.l - 6} y={sy(v) + 4} textAnchor="end" fontSize="11" fill="currentColor" opacity={0.7}>
              {yFormat(v)}
            </text>
          </g>
        ))}
        <text x={pad.l} y={height - 4} fontSize="11" fill="currentColor" opacity={0.7}>
          {new Date(x0).toISOString().slice(0, 10)}
        </text>
        <text x={W - pad.r} y={height - 4} fontSize="11" textAnchor="end" fill="currentColor" opacity={0.7}>
          {new Date(x1).toISOString().slice(0, 10)}
        </text>
        {series.map((s) => (
          <path key={s.name} d={d(s)} fill="none" stroke={s.color} strokeWidth={1.6} data-series={s.name} />
        ))}
      </svg>
      {series.length > 1 && (
        <div className="muted" style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          {series.map((s) => (
            <span key={s.name}>
              <span style={{ color: s.color }}>■</span> {s.name}
            </span>
          ))}
        </div>
      )}
    </figure>
  );
}
