import { Fragment, useEffect, useState } from "react";
import { api, useLive, LABEL, VISIT, SUBSPECIALTY, maskName, connectText } from "./api";
import FloorMap from "./FloorMap";

/* Operations wall display: what clinic staff and the flow coordinator watch.
   Dense, status-coloured, no demo controls (those live on #/presenter). */

const STATUS = {
  AWAITING_GUIDANCE: ["Ready", "info"], GUIDED: ["En route", "accent"], QUEUED: ["Waiting", "neutral"],
  IN_SERVICE: ["In service", "ok"], COMPLETED: ["Completed", "dim"], ESCALATED: ["Staff assist", "crit"],
};
const ROBOT_STATE = { IDLE: "neutral", NAVIGATING: "accent", ARRIVED: "ok", BLOCKED: "warn", FAILED: "crit", EMERGENCY_STOP: "crit" };
const short = (s) => ({ CONSULT: "Consult", REVIEW: "Review", REGISTRATION: "Reg", PHLEBOTOMY: "Blood", CAST_ROOM: "Cast",
  DRESSING: "Dressing", PROCEDURE: "Proc", PAYMENT: "Pay", PHARMACY: "Pharm", XRAY: "X-ray", ECG: "ECG" }[s] || s);
const SRC = { ml: "ML", "ml+rules": "ML+Rules", rules: "Rules", locked: "Rules", robot: "Robot", staff: "Staff",
  system: "System", autonomous: "Auto", demo: "Demo", patient: "Patient" };
const n0 = (v) => (v == null ? "–" : Math.round(v));
const level = (w, thr) => (w == null ? "neutral" : w >= thr ? "crit" : w >= thr * 0.6 ? "warn" : "ok");

function Spark({ data, thr }) {
  if (!data || data.length < 2) return <span className="o-dim">collecting</span>;
  const W = 96, H = 22, max = Math.max(thr, ...data.map((d) => d.w)) * 1.1;
  const pts = data.map((d, i) => [(i / (data.length - 1)) * W, H - (d.w / max) * H]);
  const last = pts[pts.length - 1];
  return (
    <svg width={W} height={H} className="o-spark" aria-label={`Predicted wait trend, now ${Math.round(data[data.length - 1].w)} min`}>
      <line x1="0" x2={W} y1={H - (thr / max) * H} y2={H - (thr / max) * H} className="o-spark-thr" />
      <polyline points={pts.map((p) => p.join(",")).join(" ")} className="o-spark-line" />
      <circle cx={last[0]} cy={last[1]} r="2.5" className={`o-spark-dot o-${level(data[data.length - 1].w, thr)}`} />
    </svg>
  );
}

function MiniJourney({ p }) {
  const done = [...p.completed_steps];
  return (
    <span className="o-mini" aria-label="Journey">
      {p.required_steps.map((s, i) => {
        const k = done.indexOf(s);
        let st = "later";
        if (k >= 0) { done.splice(k, 1); st = "done"; }
        else if (p.assigned_destination === s && ["GUIDED", "QUEUED", "IN_SERVICE"].includes(p.status)) st = "now";
        else if (p.eligible_now.includes(s)) st = "next";
        return <i key={i} className={`o-mj o-mj-${st}`} title={`${LABEL[s]}: ${st}`} />;
      })}
    </span>
  );
}

function PatientDetail({ p, robots, act }) {
  const rec = p.recommendation || {};
  const [added, setAdded] = useState([]);
  const step = p.assigned_destination;
  useEffect(() => setAdded([]), [p.patient_id, step]);   // orders belong to one consult only
  const pid = p.patient_id;
  const tog = (x) => setAdded((a) => (a.includes(x) ? a.filter((y) => y !== x) : [...a, x]));
  return (
    <div className="o-detail">
      <div>
        <p className="o-detail-h">{["QUEUED", "IN_SERVICE"].includes(p.status) ? "Last routing decision" : "Routing decision"} {rec.computed_at && <span className="o-dim">computed {rec.computed_at}</span>}</p>
        {rec.candidates?.length > 0 && (
          <table className="o-subtable">
            <thead><tr><th>Option</th><th className="r">Pred. wait</th><th className="r">Range</th><th className="r">30-min risk</th><th className="r">Group done</th></tr></thead>
            <tbody>
              {rec.candidates.map((c) => (
                <tr key={c.step} className={c.step === rec.recommended_station ? "o-chosen" : ""}>
                  <td>{LABEL[c.step]}{c.step === rec.recommended_station && " ◂"}</td>
                  <td className="r">{n0(c.predicted_wait_min)} min</td>
                  <td className="r o-dim">{c.expected_range_min ? `${n0(c.expected_range_min[0])}–${n0(c.expected_range_min[1])}` : "–"}</td>
                  <td className="r"><span className={`o-pill o-${c.alert_level === "HIGH" ? "crit" : c.alert_level === "MEDIUM" ? "warn" : "ok"}`}>{Math.round(c.bottleneck_probability_next_30min * 100)}%</span></td>
                  <td className="r">{n0(c.est_stage_completion_min)} min</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="o-small">{rec.explanation}</p>
        <ul className="o-rules">{rec.rule_checks?.map((r, i) => <li key={i}>{r}</li>)}</ul>
      </div>
      <div className="o-actions">
        <p className="o-detail-h">Staff actions</p>
        {p.status === "AWAITING_GUIDANCE" && rec.recommended_station && (
          <button className="o-btn o-btn-primary" onClick={() => act(`/patients/${pid}/destination`, { step: rec.recommended_station })}>Send to {LABEL[rec.recommended_station]}</button>
        )}
        {p.status === "GUIDED" && <button className="o-btn" onClick={() => act(`/patients/${pid}/arrived`)}>Mark arrived</button>}
        {p.status === "QUEUED" && <button className="o-btn o-btn-primary" onClick={() => act(`/patients/${pid}/service-started`)}>Start service</button>}
        {p.status === "IN_SERVICE" && (
          <>
            {(step === "CONSULT" || step === "REVIEW") && (
              <div className="o-orders">
                {["XRAY", "REVIEW", "CAST_ROOM", "DRESSING", "PROCEDURE", "PHARMACY"].map((x) => (
                  <label key={x}><input type="checkbox" checked={added.includes(x)} onChange={() => tog(x)} /> {LABEL[x]}</label>
                ))}
              </div>
            )}
            <button className="o-btn o-btn-primary" onClick={() => act(`/patients/${pid}/service-completed`, { step, added_steps: (step === "CONSULT" || step === "REVIEW") ? added : [], recorded_by: "Duty staff" })}>
              Complete {LABEL[step]}{(step === "CONSULT" || step === "REVIEW") && added.length > 0 && ` + ${added.length} order${added.length > 1 ? "s" : ""}`}
            </button>
          </>
        )}
        {p.status !== "COMPLETED" && (
          <button className="o-btn" onClick={() => act(`/patients/${pid}/escalate`, p.escalated ? { reason: "Resolved by staff", active: false } : { reason: "Flagged by staff", active: true })}>
            {p.escalated ? "Resolve staff flag" : "Flag for staff"}
          </button>
        )}
        {p.status !== "COMPLETED" && (
          <select className="o-select" value={p.robot_id || ""} onChange={(e) => e.target.value && act(`/robots/${e.target.value}/assign`, { patient_id: pid, force: true })}>
            <option value="">Assign robot</option>
            {robots.map((r) => <option key={r.robot_id} value={r.robot_id}>{r.robot_id}</option>)}
          </select>
        )}
        {p.escalation_reason && <p className="o-small o-crit-t">{p.escalation_reason}</p>}
      </div>
    </div>
  );
}

export default function OpsBoard() {
  const [s, error, refresh] = useLive("/dashboard/state");
  const [open, setOpen] = useState(null);
  const [msg, setMsg] = useState(null);
  const act = async (path, body) => {
    try { await api(path, "POST", body || {}); setMsg(null); refresh(); } catch (e) { setMsg(e.message); }
  };
  if (!s) return <div className="ops ops-loading">{connectText(error)}</div>;

  const active = s.patients.filter((p) => p.status !== "COMPLETED");
  const waiting = active.filter((p) => p.status === "QUEUED").length;
  const preds = s.stations.filter((x) => x.available && x.prediction && x.queue_length > 0);
  const avg = preds.length ? preds.reduce((a, x) => a + x.prediction.predicted_wait_new_arrival_min, 0) / preds.length : null;
  const atRisk = s.stations.filter((x) => x.prediction?.alert_level === "HIGH");
  const openAlerts = s.alerts.filter((a) => !a.acknowledged_by);
  const busyRobots = s.robots.filter((r) => ["NAVIGATING", "BLOCKED"].includes(r.status?.state)).length;
  const qOf = (pid) => s.patients.find((p) => p.patient_id === pid)?.queue_no;

  return (
    <div className="ops">
      <header className="o-top">
        <div className="o-brand">
          <span className="o-word">Synapse Linker</span>
          <span className="o-sub">Orthopaedic Surgery SOC, patient flow</span>
        </div>
        <div className="o-kpis">
          <div className="o-kpi"><span className="o-kpi-v">{active.length}</span><span className="o-kpi-l">In clinic</span></div>
          <div className="o-kpi"><span className="o-kpi-v">{waiting}</span><span className="o-kpi-l">Queued</span></div>
          <div className="o-kpi"><span className="o-kpi-v">{n0(avg)}<small> min</small></span><span className="o-kpi-l">Avg predicted wait</span></div>
          <div className={`o-kpi ${atRisk.length ? "o-kpi-warn" : ""}`}><span className="o-kpi-v">{atRisk.length}</span><span className="o-kpi-l">High-risk stations</span></div>
          <div className={`o-kpi ${openAlerts.some((a) => a.severity === "critical") ? "o-kpi-crit" : openAlerts.length ? "o-kpi-warn" : ""}`}><span className="o-kpi-v">{openAlerts.length}</span><span className="o-kpi-l">Open alerts</span></div>
          <div className="o-kpi"><span className="o-kpi-v">{busyRobots}/{s.robots.length}</span><span className="o-kpi-l">Robots moving</span></div>
        </div>
        <div className="o-clock">
          <span className="o-time">{s.clock.clinic_time}</span>
          <span className="o-dim">{["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][s.clock.day_of_week]}{s.clock.mode === "set" && " (scenario)"} {s.clock.speed !== 1 && `×${s.clock.speed}`}</span>
          <span className={`o-live ${error ? "o-live-off" : ""}`}>{error ? "Offline" : "Live"}</span>
        </div>
      </header>
      {msg && <div className="o-error" role="alert">{msg} <button className="o-btn" onClick={() => setMsg(null)}>Dismiss</button></div>}

      <div className="o-grid">
        <div className="o-main">
          <section className="o-panel">
            <h2 className="o-h">Stations</h2>
            <table className="o-table o-st">
              <thead>
                <tr>
                  <th>Station</th><th>Status</th><th className="r">Queue</th><th className="r">Staff</th>
                  <th className="r">Observed</th><th className="r">Predicted wait</th><th>Trend</th><th className="r">30-min risk</th>
                </tr>
              </thead>
              <tbody>
                {s.stations.map((x) => {
                  const p = x.prediction || {};
                  const st = !x.available ? (x.room_free === false ? ["No free room", "warn"] : ["Closed", "crit"]) : ["Open", "ok"];
                  const risk = p.alert_level === "HIGH" ? "crit" : p.alert_level === "MEDIUM" ? "warn" : "ok";
                  return (
                    <tr key={x.station_id} className={!x.available ? "o-row-off" : ""}>
                      <td className="o-strong">{LABEL[x.station_id]}</td>
                      <td><span className={`o-dot o-${st[1]}`} />{st[0]}</td>
                      <td className="r o-num">{x.queue_length}</td>
                      <td className="r o-num">{x.servers_busy}/{x.servers_on_duty}</td>
                      <td className="r o-num o-dim">{x.recent_mean_wait_30min == null ? "–" : `${n0(x.recent_mean_wait_30min)}m`}</td>
                      <td className="r o-num">
                        <span className={`o-wait o-${level(p.predicted_wait_new_arrival_min, x.threshold_min)}-t`}>{n0(p.predicted_wait_new_arrival_min)}</span>
                        <span className="o-dim"> min {p.expected_range_min && `(${n0(p.expected_range_min[0])}–${n0(p.expected_range_min[1])})`}</span>
                      </td>
                      <td><Spark data={x.history} thr={x.threshold_min} /></td>
                      <td className="r"><span className={`o-pill o-${risk}`}>{p.alert_level ? `${p.alert_level[0]}${p.alert_level.slice(1).toLowerCase()} ${Math.round(p.bottleneck_probability_next_30min * 100)}%` : "–"}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="o-foot">Predicted wait: Model A for a follow-up patient joining now. Risk: Model B, probability the next 30 minutes exceed the station target (dashed line on trend). Queue includes other departments.</p>
          </section>

          <section className="o-panel">
            <h2 className="o-h">Patient tracking <span className="o-dim">{active.length} active, {s.patients.length - active.length} completed</span></h2>
            {s.patients.length === 0 ? <p className="o-empty">No patients checked in.</p> : (
              <table className="o-table o-table-click">
                <thead>
                  <tr>
                    <th>Q no.</th><th>Patient</th><th>Visit</th><th>Status</th><th>Location</th>
                    <th className="r">In clinic</th><th>Next</th><th className="r">Pred. wait</th><th>Robot</th><th>Journey</th>
                  </tr>
                </thead>
                <tbody>
                  {s.patients.map((p) => {
                    const rec = p.recommendation || {};
                    const nxt = p.assigned_destination || rec.recommended_station;
                    const c = rec.candidates?.find((x) => x.step === nxt);
                    const [label, tone] = STATUS[p.status];
                    const loc = p.room && ["GUIDED", "QUEUED", "IN_SERVICE"].includes(p.status)
                      ? `${short(p.current_station === "REGISTRATION" ? nxt : p.current_station)}, ${p.room.classroom_label}` : short(p.current_station);
                    return (
                      <Fragment key={p.patient_id}>
                        <tr className={`${open === p.patient_id ? "o-open" : ""} ${p.status === "COMPLETED" ? "o-row-off" : ""}`}
                            onClick={() => setOpen(open === p.patient_id ? null : p.patient_id)} tabIndex={0}
                            onKeyDown={(e) => e.key === "Enter" && setOpen(open === p.patient_id ? null : p.patient_id)}>
                          <td className="o-strong o-num">{p.queue_no}</td>
                          <td>{maskName(p.display_name)}{p.clinician_priority === "expedited" && <span className="o-tag">Exp</span>}{p.mobility !== "independent" && <span className="o-tag" title={p.mobility}>{p.mobility === "wheelchair" ? "WC" : "Aid"}</span>}</td>
                          <td className="o-dim">{VISIT[p.visit_type]}, {SUBSPECIALTY[p.referral_category]}</td>
                          <td><span className={`o-pill o-${tone}`}>{label}</span></td>
                          <td>{p.status === "COMPLETED" ? "–" : loc}</td>
                          <td className="r o-num">{n0(p.minutes_in_clinic)}m</td>
                          <td>{p.status === "COMPLETED" ? "–" : rec.status === "WAIT_UNAVAILABLE" ? <span className="o-warn-t">Waiting for room</span> : nxt ? LABEL[nxt] : "–"}</td>
                          <td className="r o-num">{p.status === "QUEUED" ? `${n0(p.queue?.waiting_min)}m / ${n0(p.queue?.predicted_wait_min)}m` : c ? `${n0(c.predicted_wait_min)}m` : "–"}</td>
                          <td>{p.robot_id || <span className="o-dim">–</span>}</td>
                          <td><MiniJourney p={p} /></td>
                        </tr>
                        {open === p.patient_id && (
                          <tr className="o-detail-row"><td colSpan={10}><PatientDetail p={p} robots={s.robots} act={act} /></td></tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            )}
            <p className="o-foot">Queued: waited / predicted at arrival. Click a row for the routing decision and staff actions. Names masked; patients are called by queue number.</p>
          </section>
        </div>

        <aside className="o-side">
          <section className="o-panel">
            <h2 className="o-h">Alerts <span className="o-dim">{openAlerts.length} open</span></h2>
            {s.alerts.length === 0 ? <p className="o-empty">No alerts.</p> : (
              <ol className="o-alerts">
                {s.alerts.slice(0, 8).map((a) => (
                  <li key={a.id} className={`o-alert o-alert-${a.severity} ${a.acknowledged_by ? "o-acked" : ""}`}>
                    <div className="o-alert-h">
                      <span className="o-num o-dim">{a.clinic_time}</span>
                      <strong>{a.title}</strong>
                      {a.patient_id && <span className="o-dim">Q{qOf(a.patient_id)}</span>}
                    </div>
                    <p className="o-alert-m">{a.message}</p>
                    {a.acknowledged_by ? <span className="o-dim o-small">Acknowledged by {a.acknowledged_by} {a.acknowledged_at}</span>
                      : <button className="o-btn o-btn-sm" onClick={() => act(`/alerts/${a.id}/ack`, { by: "Duty staff" })}>Acknowledge</button>}
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section className="o-panel">
            <h2 className="o-h">Robots</h2>
            {s.robots.map((r) => {
              const st = r.status?.state || "IDLE";
              const t = r.current_task;
              return (
                <div key={r.robot_id} className="o-robot">
                  <div className="o-robot-h">
                    <strong>{r.robot_id}</strong>
                    <span className="o-dim">{r.kind === "physical" ? `OjanBot, ${r.adapter}` : "Virtual assistant"}</span>
                    <span className={`o-pill o-${ROBOT_STATE[st] || "neutral"}`}>{st.replace("_", " ").toLowerCase()}</span>
                  </div>
                  <div className="o-robot-b">
                    {r.patient_id ? <>Q{qOf(r.patient_id)}</> : <span className="o-dim">unassigned</span>}
                    {t && ["NAVIGATING", "BLOCKED"].includes(t.state) && (
                      <>
                        <span className="o-dim"> to {LABEL[t.destination_station]}{t.route?.room_id && ` (${t.route.room_id.replace("ROOM_", "Room ")})`}</span>
                        <span className="o-bar"><span style={{ width: `${Math.round(t.progress * 100)}%` }} /></span>
                      </>
                    )}
                  </div>
                  {r.status?.message && r.kind === "physical" && <p className="o-small o-warn-t">{r.status.message}</p>}
                </div>
              );
            })}
          </section>

          <div className="o-floor"><FloorMap compact /></div>

          <section className="o-panel">
            <h2 className="o-h">Event log</h2>
            <ol className="o-log">
              {s.events.slice(0, 14).map((e) => (
                <li key={e.id}><span className="o-num o-dim">{e.clinic_time}</span><span className={`o-src o-src-${e.source.replace("+", "")}`}>{SRC[e.source] || e.source}</span><span>{e.message || e.type}</span></li>
              ))}
            </ol>
          </section>
        </aside>
      </div>
      <footer className="o-footer">
        Concept prototype for a Singapore public-hospital orthopaedic SOC. Not affiliated with CGH. Fictional patients, synthetic data, not clinically validated.
        Models {s.model_versions.waiting_time}, {s.model_versions.bottleneck}. <a href="#/presenter">Presenter console</a>
      </footer>
    </div>
  );
}
