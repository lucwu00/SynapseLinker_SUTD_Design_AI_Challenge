import { useState } from "react";
import { api, useLive, LABEL, VISIT, STATUS, SUBSPECIALTY, maskName, fmtMin, connectText } from "./api";
import JourneyLine from "./JourneyLine";
import Replay from "./Replay";
import FloorMap from "./FloorMap";

const SCENES = [
  [1, "Patient checks in"],
  [2, "Pre-op test order"],
  [3, "ECG congestion"],
  [4, "Blood-test room closes"],
  [5, "Robot drives"],
  [6, "Robot handover"],
  [7, "Before/after result"],
  [8, "Room occupied, re-guide"],
  [9, "Corridor blocked, reroute"],
];

function Risk({ p }) {
  if (!p) return null;
  return (
    <span className={`risk risk-${p.alert_level.toLowerCase()}`} title="Probability of a bottleneck in the next 30 minutes (Model B)">
      {p.alert_level === "HIGH" ? "High" : p.alert_level === "MEDIUM" ? "Medium" : "Low"} risk {Math.round(p.bottleneck_probability_next_30min * 100)}%
    </span>
  );
}

function StationCard({ s, act }) {
  const p = s.prediction;
  const load = (d) => act(`/stations/${s.station_id}/status`, { background_queue: Math.max(0, s.background_queue + d) });
  return (
    <article className={`station ${s.available ? "" : "station-closed"}`}>
      <header>
        <h3>{LABEL[s.station_id]}</h3>
        <Risk p={p} />
      </header>
      <p className="wait">
        <strong>{p ? Math.round(p.predicted_wait_new_arrival_min) : "–"}</strong> min
      </p>
      <p className="muted small">
        predicted wait for someone joining now
        {p?.expected_range_min && ` (range ${Math.round(p.expected_range_min[0])}–${Math.round(p.expected_range_min[1])})`}
      </p>
      <dl className="facts">
        <div><dt>Waiting</dt><dd>{s.queue_length}</dd></div>
        <div><dt>{s.station_id === "CONSULT" ? "Doctors" : "Staff"} busy</dt><dd>{s.servers_busy}/{s.servers_on_duty}</dd></div>
      </dl>
      <div className="row">
        <button onClick={() => act(`/stations/${s.station_id}/status`, { available: !s.available })}>
          {s.available ? "Close room" : "Reopen room"}
        </button>
        <button onClick={() => load(5)} title="Simulate 5 more people from other departments">+5 waiting</button>
        <button onClick={() => load(-5)} disabled={s.background_queue === 0}>−5</button>
      </div>
      {!s.available && <p className="closed-note">Unavailable: no one is routed here</p>}
    </article>
  );
}

function PatientRow({ p, robots, act }) {
  const rec = p.recommendation || {};
  const [added, setAdded] = useState([]);
  const step = p.assigned_destination;
  const toggle = (x) => setAdded((a) => (a.includes(x) ? a.filter((y) => y !== x) : [...a, x]));
  const pid = p.patient_id;
  return (
    <article className={`patient ${p.escalated ? "patient-escalated" : ""}`}>
      <header>
        <div className="row" style={{ marginTop: 0 }}>
          <span className="queue-badge"><span className="qno-label">Queue no.</span><span className="qno">{p.queue_no}</span></span>
          <div>
          <h3>{maskName(p.display_name)}</h3>
          <p className="muted small">
            {pid}, {VISIT[p.visit_type]}, {SUBSPECIALTY[p.referral_category] || p.referral_category}, age {p.age}
            {p.mobility !== "independent" && `, ${p.mobility.replace("_", " ")}`}
            {p.clinician_priority === "expedited" && ", expedited by clinician"}
          </p>
          </div>
        </div>
        <span className={`status status-${p.status.toLowerCase()}`}>{STATUS[p.status]}</span>
      </header>
      <JourneyLine required={p.required_steps} completed={p.completed_steps} eligible={p.eligible_now}
                   current={["GUIDED", "QUEUED", "IN_SERVICE"].includes(p.status) ? step : null} />

      {rec.status === "ROUTE" && p.status !== "QUEUED" && p.status !== "IN_SERVICE" && (
        <div className="rec">
          <p>
            <strong>Next: {LABEL[rec.recommended_station]}</strong>
            {rec.candidates?.[0] && (
              <>, predicted wait {fmtMin(rec.candidates.find((c) => c.step === rec.recommended_station)?.predicted_wait_min)}</>
            )}
            {rec.recommended_sequence?.length > 1 && (
              <span className="muted"> (order {rec.recommended_sequence.map((x) => LABEL[x]).join(", ")})</span>
            )}
          </p>
          {rec.candidates?.length > 1 && (
            <table className="cands">
              <thead><tr><th>Option</th><th>Wait (ML)</th><th>Risk (ML)</th><th>Group done in</th></tr></thead>
              <tbody>
                {rec.candidates.map((c) => (
                  <tr key={c.step} className={c.step === rec.recommended_station ? "chosen" : ""}>
                    <td>{LABEL[c.step]}</td><td>{fmtMin(c.predicted_wait_min)}</td>
                    <td>{c.alert_level.toLowerCase()} {Math.round(c.bottleneck_probability_next_30min * 100)}%</td>
                    <td>{fmtMin(c.est_stage_completion_min)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <details>
            <summary>Why this recommendation</summary>
            <p className="small">{rec.explanation}</p>
            <ul className="small muted">{rec.rule_checks?.map((r, i) => <li key={i}>Rule: {r}</li>)}</ul>
          </details>
        </div>
      )}
      {rec.status && rec.status !== "ROUTE" && p.status !== "COMPLETED" && <p className="rec small">{rec.explanation}</p>}
      {p.room && ["GUIDED", "QUEUED", "IN_SERVICE"].includes(p.status) && (
        <p className="small">Room: <strong>{p.room.classroom_label}</strong> ({p.room.label})</p>
      )}
      {p.queue && (
        <p className="small">
          {p.patients_ahead != null && p.status === "QUEUED" && <>{p.patients_ahead} ahead. </>}
          At {LABEL[p.queue.station]} for {fmtMin(p.queue.waiting_min)}
          {p.queue.predicted_wait_min != null && `, predicted ${fmtMin(p.queue.predicted_wait_min)} on arrival`}
        </p>
      )}

      <div className="row">
        {p.status === "AWAITING_GUIDANCE" && rec.recommended_station && (
          <button className="primary" onClick={() => act(`/patients/${pid}/destination`, { step: rec.recommended_station })}>
            Send to {LABEL[rec.recommended_station]}
          </button>
        )}
        {p.status === "GUIDED" && <button onClick={() => act(`/patients/${pid}/arrived`)}>Mark arrived</button>}
        {p.status === "QUEUED" && <button className="primary" onClick={() => act(`/patients/${pid}/service-started`)}>Start service</button>}
        {p.status === "IN_SERVICE" && (
          <>
            {(step === "CONSULT" || step === "REVIEW") && (
              <fieldset className="orders">
                <legend className="small">Clinician orders</legend>
                {["XRAY", "REVIEW", "CAST_ROOM", "DRESSING", "PROCEDURE", "PHARMACY"].map((x) => (
                  <label key={x} className="small"><input type="checkbox" checked={added.includes(x)} onChange={() => toggle(x)} /> {LABEL[x]}</label>
                ))}
              </fieldset>
            )}
            <button className="primary" onClick={() => act(`/patients/${pid}/service-completed`, { step, added_steps: added })}>
              Complete {LABEL[step]}
            </button>
          </>
        )}
        {p.status !== "COMPLETED" && (
          <button onClick={() => act(`/patients/${pid}/escalate`, p.escalated ? { reason: "Cleared by staff", active: false }
                                                                       : { reason: "Patient feels unwell (staff)", active: true })}>
            {p.escalated ? "Clear staff flag" : "Flag for staff"}
          </button>
        )}
        {p.status !== "COMPLETED" && (
          <select aria-label="Robot" value={p.robot_id || ""} onChange={(e) => e.target.value && act(`/robots/${e.target.value}/assign`, { patient_id: pid, force: true })}>
            <option value="">Assign robot…</option>
            {robots.map((r) => <option key={r.robot_id} value={r.robot_id}>{r.robot_id} ({r.kind})</option>)}
          </select>
        )}
      </div>
    </article>
  );
}

function CheckIn({ act }) {
  const [f, setF] = useState({ patient_id: "", visit_type: "follow_up", referral_category: "Knee/Hip", age: 60,
    mobility: "independent", clinician_priority: "routine", doctor_id: "DR01", pre_consult_tests: [], robot_id: "" });
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === "number" ? Number(e.target.value) : e.target.value });
  const tog = (t) => setF({ ...f, pre_consult_tests: f.pre_consult_tests.includes(t) ? f.pre_consult_tests.filter((x) => x !== t) : [...f.pre_consult_tests, t] });
  const submit = (e) => {
    e.preventDefault();
    const b = { ...f };
    if (!b.robot_id) delete b.robot_id;
    act("/patients/check-in", b).then((ok) => ok && setF({ ...f, patient_id: "" }));
  };
  return (
    <form className="checkin" onSubmit={submit}>
      <h2>Check in a patient</h2>
      <label>Patient ID<input required pattern="[A-Za-z0-9_-]{1,32}" value={f.patient_id} onChange={set("patient_id")} placeholder="ORTHO_010" /></label>
      <label>Visit<select value={f.visit_type} onChange={set("visit_type")}>{Object.entries(VISIT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label>Referral<select value={f.referral_category} onChange={set("referral_category")}>
        {Object.entries(SUBSPECIALTY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label>Age<input type="number" min="0" max="120" value={f.age} onChange={set("age")} /></label>
      <label>Mobility<select value={f.mobility} onChange={set("mobility")}><option value="independent">Independent</option><option value="walking_aid">Walking aid</option><option value="wheelchair">Wheelchair</option></select></label>
      <label>Clinician priority<select value={f.clinician_priority} onChange={set("clinician_priority")}><option value="routine">Routine</option><option value="expedited">Expedited</option></select></label>
      <label>Doctor<select value={f.doctor_id} onChange={set("doctor_id")}><option>DR01</option><option>DR02</option></select></label>
      <label>Robot<select value={f.robot_id} onChange={set("robot_id")}><option value="">None</option><option>EDi-01</option><option>EDi-02</option></select></label>
      <fieldset><legend>Tests before consultation</legend>
        {["XRAY", "PHLEBOTOMY", "ECG"].map((t) => <label key={t} className="inline"><input type="checkbox" checked={f.pre_consult_tests.includes(t)} onChange={() => tog(t)} /> {LABEL[t]}</label>)}
      </fieldset>
      <button className="primary" type="submit">Check in</button>
    </form>
  );
}

function RobotCard({ r, act }) {
  const st = r.status || {};
  const t = r.current_task;
  return (
    <article className="robot">
      <header>
        <h3>{r.robot_id}</h3>
        <span className={`nav nav-${(st.state || "IDLE").toLowerCase()}`}>{st.state || "IDLE"}</span>
      </header>
      <p className="small muted">{r.kind === "physical" ? `Physical OjanBot, ${r.adapter} adapter` : "Virtual assistant (shows directions, does not move)"}</p>
      <p className="small">{r.patient_id ? <>Serving <strong>{r.patient_id}</strong></> : "No patient assigned"}</p>
      {t && <p className="small">Task {t.task_id} to {LABEL[t.destination_station]}: {t.state.toLowerCase()} {t.state === "NAVIGATING" && `${Math.round(t.progress * 100)}%`}</p>}
      {st.message && r.kind === "physical" && <p className="small warn">{st.message}</p>}
      {r.kind === "physical" && (
        <div className="row">
          <button onClick={() => act(`/robots/${r.robot_id}/command`, { action: "obstacle" })}>Obstacle</button>
          <button onClick={() => act(`/robots/${r.robot_id}/command`, { action: "clear_obstacle" })}>Clear</button>
          <button className="danger" onClick={() => act(`/robots/${r.robot_id}/command`, { action: "stop" })}>Emergency stop</button>
          <button onClick={() => act(`/robots/${r.robot_id}/command`, { action: "resume" })}>Resume</button>
        </div>
      )}
      <a className="small" href={`#/robot/${r.robot_id}`} target="_blank" rel="noreferrer">Open {r.robot_id} screen</a>
    </article>
  );
}

const SRC = { ml: "ML", "ml+rules": "ML + rules", rules: "Rules", robot: "Robot", staff: "Staff", system: "System",
  autonomous: "Autonomous", demo: "Demo", patient: "Patient", locked: "Rules" };

export default function Presenter() {
  const [s, error, refresh] = useLive("/dashboard/state");
  const [msg, setMsg] = useState(null);
  const [scene, setScene] = useState(null);
  const [tab, setTab] = useState("floor");

  const act = async (path, body) => {
    try {
      const r = await api(path, "POST", body || {});
      setMsg(null);
      refresh();
      return r || true;
    } catch (e) {
      setMsg(e.message);
      return false;
    }
  };
  const runScene = async (n) => {
    const r = await act(`/demo/scene/${n}`);
    if (r) setScene(r);
  };

  if (!s) return <main className="loading">{error ? connectText(error, "Start it with: uvicorn backend.main:app --port 8000") : "Loading clinic state…"}</main>;

  return (
    <div className="dash">
      <header className="topbar">
        <div className="brand">
          <h1>Presenter console</h1>
          <p>Demo controls and simulation for Synapse Linker. Show the <a href="#/" target="_blank" rel="noreferrer">wall display</a> to
            the audience and the <a href="#/robot/EDi-01" target="_blank" rel="noreferrer">EDi-01</a> /{" "}
            <a href="#/robot/EDi-02" target="_blank" rel="noreferrer">EDi-02</a> screens on phones.</p>
          <p className="context">Concept prototype for a Singapore public hospital such as Changi General Hospital. Not affiliated with
            or endorsed by CGH. Fictional patients, synthetic data, not clinically validated.</p>
        </div>
        <div className="clock" aria-live="polite">
          <span className="clock-time">{s.clock.clinic_time}</span>
          <span className="small muted">clinic time, {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][s.clock.day_of_week]}, speed ×{s.clock.speed}</span>
          <div className="row">
            <button onClick={() => act("/demo/clock", { advance_min: 15 })}>+15 min</button>
            <button onClick={() => act("/demo/clock", { speed: s.clock.speed === 1 ? 10 : 1 })}>{s.clock.speed === 1 ? "Run ×10" : "Real time"}</button>
          </div>
        </div>
        <div className="controls">
          <label className="inline"><input type="checkbox" checked={s.auto_dispatch} onChange={(e) => act("/demo/auto-dispatch", { enabled: e.target.checked })} /> Robots act automatically</label>
          <button onClick={() => { setScene(null); act("/demo/reset"); }}>Reset demo</button>
          <p className="small muted">Models {s.model_versions.waiting_time}, {s.model_versions.bottleneck}</p>
        </div>
      </header>

      <nav className="scenes" aria-label="Demo scenes">
        {SCENES.map(([n, t]) => <button key={n} onClick={() => runScene(n)}><span className="scene-n">{n}</span>{t}</button>)}
        <span className="tabs">
          <button className={tab === "floor" ? "on" : ""} onClick={() => setTab("floor")}>Clinic floor</button>
          <button className={tab === "replay" ? "on" : ""} onClick={() => setTab("replay")}>Before/after evaluation</button>
        </span>
      </nav>

      {msg && <p className="error" role="alert">{msg} <button onClick={() => setMsg(null)}>Dismiss</button></p>}
      {scene && (
        <section className="narration" aria-live="polite">
          <h2>Scene {scene.scene}: {scene.title}</h2>
          <ul>{scene.narration.map((l, i) => <li key={i}>{l.startsWith("{") ? <code>{l.slice(0, 240)}…</code> : l}</li>)}</ul>
          <button onClick={() => setScene(null)}>Hide</button>
        </section>
      )}

      {tab === "replay" ? <Replay /> : (
        <div className="grid">
          <main>
            <FloorMap />
            <section>
              <h2>Stations</h2>
              <p className="small muted">Waits and risks are live predictions from the trained models. "Waiting" includes simulated patients from other departments.</p>
              <div className="stations">{s.stations.map((x) => <StationCard key={x.station_id} s={x} act={act} />)}</div>
            </section>
            <section>
              <h2>Patients</h2>
              {s.patients.length === 0 && <p className="empty">No patients yet. Check one in, or run scene 1.</p>}
              {s.patients.map((p) => <PatientRow key={p.patient_id} p={p} robots={s.robots} act={act} />)}
            </section>
          </main>
          <aside>
            <section><h2>Robots</h2>{s.robots.map((r) => <RobotCard key={r.robot_id} r={r} act={act} />)}
              <p className="small muted">{s.map.warning ? "Using the fictional example map." : `Map: ${s.map.path}`}</p>
            </section>
            <CheckIn act={act} />
            <section className="log">
              <h2>Activity</h2>
              <ol>
                {s.events.map((e) => (
                  <li key={e.id}>
                    <span className="t">{e.clinic_time}</span>
                    <span className={`src src-${e.source.replace("+", "")}`}>{SRC[e.source] || e.source}</span>
                    <span>{e.message || e.type}</span>
                  </li>
                ))}
              </ol>
            </section>
          </aside>
        </div>
      )}
    </div>
  );
}
