import { useEffect, useState } from "react";
import { api } from "./api";

const POL = { fixed: "Booked order (baseline)", shortest_queue: "Shortest queue (no ML)", synapse: "Synapse Linker" };
const GROUPS = ["all visits", "order-flexible visits", "pre-op visits", "follow-up/new with 2 tests", "visits with no ordering choice"];

function EffectChart({ effects }) {
  const rows = GROUPS.flatMap((g) => ["shortest_queue", "synapse"].map((p) => effects.find((e) => e.group === g && e.policy === p)).filter(Boolean));
  const lo = Math.min(-8, ...rows.map((r) => r.ci95[0])), hi = Math.max(3, ...rows.map((r) => r.ci95[1]));
  const W = 640, L = 230, rowH = 22, H = rows.length * rowH + 40;
  const x = (v) => L + ((v - lo) / (hi - lo)) * (W - L - 20);
  const [tip, setTip] = useState(null);
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Change in time in clinic by group and policy">
        <line x1={x(0)} x2={x(0)} y1={8} y2={H - 26} className="axis0" />
        {[-8, -6, -4, -2, 0, 2].filter((t) => t >= lo && t <= hi).map((t) => (
          <g key={t}><line x1={x(t)} x2={x(t)} y1={8} y2={H - 26} className="grid" /><text x={x(t)} y={H - 10} className="tick">{t}</text></g>
        ))}
        {rows.map((r, i) => {
          const y = 10 + i * rowH, first = r.policy === "shortest_queue";
          return (
            <g key={i} onMouseEnter={() => setTip(r)} onMouseLeave={() => setTip(null)}>
              {first && <text x={L - 8} y={y + rowH} className="glabel">{r.group} ({r.visits.toLocaleString()})</text>}
              <rect x={Math.min(x(0), x(r.diff_min))} y={y + 4} width={Math.max(1, Math.abs(x(r.diff_min) - x(0)))} height={rowH - 8}
                    rx="3" className={`bar bar-${r.policy}`} />
              <line x1={x(r.ci95[0])} x2={x(r.ci95[1])} y1={y + rowH / 2} y2={y + rowH / 2} className="ci" />
              <rect x={L} y={y} width={W - L} height={rowH} fill="transparent" />
            </g>
          );
        })}
      </svg>
      <figcaption className="small">
        <span className="key key-shortest_queue" /> Shortest queue (no ML) <span className="key key-synapse" /> Synapse Linker.
        Minutes per visit versus the booked order; left is faster. Lines show 95% confidence intervals.
        {tip && <strong className="tip"> {POL[tip.policy]}, {tip.group}: {tip.diff_min} min (CI {tip.ci95[0]} to {tip.ci95[1]}), {tip.pct_reduction}%.</strong>}
      </figcaption>
    </figure>
  );
}

export default function Replay() {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  const load = () => api("/simulation/results").then(setD).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (d?.latest_quick_replay?.status !== "running") return;
    const t = setTimeout(load, 3000);
    return () => clearTimeout(t);
  }, [d]);
  if (err) return <p className="error">{err}</p>;
  if (!d) return <p>Loading results…</p>;
  const r = d.held_out_report;
  if (!r) return <p className="empty">No replay report yet. Run <code>python -m flow.evaluate_replay</code>.</p>;
  const flex = r.effects.find((e) => e.policy === "synapse" && e.group === "order-flexible visits");
  const all = r.effects.find((e) => e.policy === "synapse" && e.group === "all visits");
  const q = d.latest_quick_replay;
  return (
    <section className="replay">
      <h2>Before/after replay, held-out {r.period}</h2>
      {(() => {
        const pre = r.effects.find((e) => e.policy === "synapse" && e.group === "pre-op visits");
        const sqPre = r.effects.find((e) => e.policy === "shortest_queue" && e.group === "pre-op visits");
        const S = (pol, g) => r.summary.find((x) => x.policy === pol && x.group === g);
        const b = S("fixed", "order-flexible visits"), sl = S("synapse", "order-flexible visits");
        const hours = Math.round((Math.abs(flex.diff_min) * flex.visits) / 60);
        const share = Math.round((flex.visits / r.visits) * 100);
        const waitCut = Math.round(((b.mean_wait_min - sl.mean_wait_min) / b.mean_wait_min) * 100);
        return (
          <>
            <div className="impact">
              <div><strong>{Math.abs(pre.diff_min).toFixed(0)} min</strong><span>faster per pre-op visit (95% CI {Math.abs(pre.ci95[1])}–{Math.abs(pre.ci95[0])})</span></div>
              <div><strong>{hours} h</strong><span>of patient time saved in {r.days} clinic days ({flex.visits.toLocaleString()} order-flexible visits)</span></div>
              <div><strong>{waitCut}%</strong><span>less queueing for those patients ({b.mean_wait_min} → {sl.mean_wait_min} min)</span></div>
              <div><strong>{(pre.diff_min / sqPre.diff_min).toFixed(1)}×</strong><span>the saving of simple shortest-queue routing without ML ({Math.abs(sqPre.diff_min)} min)</span></div>
            </div>
            <p className="lede">
              Where the order of tests can change, Synapse Linker cuts time in clinic by <strong>{Math.abs(flex.diff_min)} min a visit</strong>,
              and the slowest 10% of these visits finish {(b.p90_total_min - sl.p90_total_min).toFixed(1)} min sooner.
              Only {share}% of visits have a choice of order today, so the clinic-wide average barely moves ({all.pct_reduction}%):
              the gain grows as more steps become order-flexible.
            </p>
          </>
        );
      })()}
      <p className="small muted">
        Synthetic data. Each of the {r.days} days was re-simulated with identical arrivals, service durations and background demand; only the routing policy changed.
        Replay check: baseline mean {r.replay_validation.replay_mean} min vs recorded {r.replay_validation.recorded_mean} min.
      </p>
      <EffectChart effects={r.effects} />
      <table className="summary">
        <thead><tr><th>Policy</th><th>Group</th><th>Visits</th><th>Mean</th><th>Median</th><th>90th pct</th><th>Mean wait</th><th>Route changes</th></tr></thead>
        <tbody>{r.summary.map((x, i) => (
          <tr key={i}><td>{POL[x.policy]}</td><td>{x.group}</td><td>{x.visits.toLocaleString()}</td><td>{x.mean_total_min}</td>
            <td>{x.median_total_min}</td><td>{x.p90_total_min}</td><td>{x.mean_wait_min}</td><td>{x.route_changes}</td></tr>))}
        </tbody>
      </table>
      <div className="row">
        <button onClick={() => api("/simulation/replay", "POST", { days: 3 }).then(load).catch((e) => setErr(e.message))} disabled={q.status === "running"}>
          {q.status === "running" ? "Replaying 3 days…" : "Run a live 3-day replay"}
        </button>
        {q.status === "done" && q.result && (
          <span className="small">Live replay mean time in clinic: {q.result.filter((x) => x.group === "all visits").map((x) => `${POL[x.policy]} ${x.mean_total_min} min`).join(", ")}</span>
        )}
        {q.status === "error" && <span className="error small">{q.error}</span>}
      </div>
    </section>
  );
}
