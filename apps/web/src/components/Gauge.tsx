"use client";

export type GaugeTone = "ok" | "warn" | "bad";

/** Half-circle gauge: the arc fills from empty to the LIMIT (full arc = limit reached). Geometry only; the printed
 *  numbers are exact strings supplied by the caller. */
export default function Gauge({ ratio, tone, value, caption, limit }: { ratio: number; tone: GaugeTone; value: string; caption: string; limit: string }) {
  const r = 52;
  const c = Math.PI * r; // length of the half circle
  const fill = Math.max(0.012, Math.min(1, ratio)) * c;
  const d = "M12 66 A52 52 0 0 1 116 66";
  return (
    <div className={`gauge ${tone}`}>
      <svg viewBox="0 0 128 76" role="img" aria-label={`${caption}: ${value}, limit ${limit}`}>
        <path d={d} className="g-track" />
        <path d={d} className="g-fill" style={{ strokeDasharray: `${fill} ${c}` }} />
        <text x="64" y="58" textAnchor="middle" className="g-val">{value}</text>
      </svg>
      <div className="g-cap">{caption}</div>
      <div className="g-lim muted">limit {limit}</div>
    </div>
  );
}
