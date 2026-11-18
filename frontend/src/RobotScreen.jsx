import { useEffect, useState } from "react";
import { api, useLive, connectText } from "./api";
import { LANGS, PLACE, TURN, T, fill } from "./i18n";
import JourneyLine from "./JourneyLine";

function speak(text, lang) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = lang;
  u.rate = 0.9;
  window.speechSynthesis.speak(u);
}

/** Patient-facing screen on the robot's phone (EDi-01) or the virtual assistant (EDi-02).
 *  Shows the queue number (no names), in English, Chinese, Malay or Tamil. Both robots read the same
 *  journey from the backend, so a handover keeps the patient's context. */
export default function RobotScreen({ robotId }) {
  const [s, error, refresh] = useLive(`/robots/${robotId}/session`);
  const [q, setQ] = useState("");
  const [msg, setMsg] = useState(null);
  const [lang, setLang] = useState(() => { try { return localStorage.getItem("lang") || "en"; } catch { return "en"; } });
  useEffect(() => { try { localStorage.setItem("lang", lang); } catch { /* ignore */ } }, [lang]);
  useEffect(() => { const t = setInterval(refresh, 1500); return () => clearInterval(t); }, [refresh]);  // robot moves continuously

  const t = T[lang], P = PLACE[lang], TR = TURN[lang];
  const speech = LANGS.find((l) => l.code === lang).speech;
  const post = async (path, body) => {
    try { await api(path, "POST", body); setMsg(null); refresh(); } catch (e) { setMsg(e.message); }
  };
  const findByQueue = async (e) => {
    e.preventDefault();
    try {
      const st = await api("/dashboard/state");
      const p = st.patients.find((x) => x.queue_no === q.trim() || x.patient_id === q.trim());
      if (!p) { setMsg("Queue number not found"); return; }
      await post(`/robots/${robotId}/assign`, { patient_id: p.patient_id });
    } catch (err) { setMsg(err.message); }
  };

  if (!s) return <main className="robot-screen">{connectText(error)}</main>;
  const nav = s.robot?.state;
  const j = s.journey;
  const p = j?.patient;
  const rec = p?.recommendation || {};
  const next = p?.assigned_destination || rec.recommended_station;
  const cand = rec.candidates?.find((c) => c.step === next);
  const virtual = s.robot?.kind === "virtual" || s.robot?.adapter === "none";
  const room = j?.room || s.direction_room;
  const place = (step) => {
    const name = P[step] || step;
    return room && next === step && room.classroom_label ? `${name} (${room.classroom_label})` : name;
  };

  const join = (a, b) => [a, b].filter(Boolean).join(lang === "zh" ? "" : " ");
  let headline, line;
  if (!p) { headline = t.hello; line = virtual ? t.enterNo : t.waitingPatient; }
  else if (p.status === "COMPLETED") { headline = t.done; line = t.doneLine; }
  else if (p.status === "ESCALATED") { headline = t.staff; line = t.staffLine; }
  else if (nav === "EMERGENCY_STOP") { headline = t.stopped; line = t.stoppedLine; }
  else if (nav === "BLOCKED") { headline = t.blocked; line = t.blockedLine; }
  else if (p.status === "QUEUED") { headline = place(p.assigned_destination || p.current_station); line = fill(t.waitAt, { p: P[p.current_station] }); }
  else if (p.status === "IN_SERVICE") { headline = P[p.current_station]; line = join(fill(t.at, { p: P[p.current_station] }), t.afterThis); }
  else if (rec.status === "WAIT_UNAVAILABLE") { headline = t.next; line = t.noRoom; }
  else if (next) {
    headline = `${t.next}: ${place(next)}`;
    line = cand ? fill(t.wait, { n: Math.round(cand.predicted_wait_min) }) : "";
    if (p.status === "GUIDED" && nav === "NAVIGATING") line = join(fill(t.follow, { p: place(next) }), line);
    else if (p.status === "GUIDED" && !s.destination_on_track) line = join(fill(t.walk, { p: P[next] }), line);
  } else { headline = t.hello; line = ""; }

  return (
    <main className="robot-screen" lang={lang === "zh" ? "zh-Hans" : lang}>
      <header className="robot-top">
        <span className="robot-name">{robotId}</span>
        <span className="small">{s.clinic_time}</span>
      </header>
      <div className="langs" role="group" aria-label="Language">
        {LANGS.map((l) => (
          <button key={l.code} aria-pressed={lang === l.code} onClick={() => setLang(l.code)}>{l.name}</button>
        ))}
      </div>

      {p && (
        <div className="robot-q">
          <div><span className="qno-label">{t.queue}</span><span className="num">{p.queue_no}</span></div>
          {j.patients_ahead != null && p.status === "QUEUED" && <span className="ahead">{fill(t.ahead, { n: j.patients_ahead })}</span>}
        </div>
      )}
      <h1 className="headline">{headline}</h1>
      <p className="line">{line}</p>

      {p && p.status !== "COMPLETED" && (
        <JourneyLine large labels={P} required={p.required_steps} completed={p.completed_steps} eligible={j.eligible_now}
                     current={["GUIDED", "QUEUED", "IN_SERVICE"].includes(p.status) ? p.assigned_destination : null} />
      )}

      {p && rec.recommended_sequence?.length > 1 && p.status === "AWAITING_GUIDANCE" && (
        <p className="why">{t.anyOrder} {rec.recommended_sequence.map((x) => P[x]).join(", ")}.</p>
      )}

      {virtual && s.directions?.length > 0 && p && !["COMPLETED", "QUEUED", "IN_SERVICE"].includes(p.status) && (
        <ol className="directions" aria-label="Directions">
          {s.directions.map((d, i) => <li key={i}>{TR[d.action]}{d.action !== "STOP" && d.at_marker ? ` (${TR.at} ${d.at_marker})` : ""}</li>)}
        </ol>
      )}

      <div className="big-actions">
        <button className="big" onClick={() => speak(join(headline + (lang === "zh" ? "。" : "."), line), speech)}>{t.readAloud}</button>
        {p && p.status === "AWAITING_GUIDANCE" && next && rec.status === "ROUTE" && (
          <button className="big primary" onClick={() => post(`/patients/${p.patient_id}/destination`, { step: next, robot_id: robotId })}>
            {virtual ? t.show : t.guide}
          </button>
        )}
        {p && !["COMPLETED", "ESCALATED"].includes(p.status) && (
          <button className="big" onClick={() => post(`/patients/${p.patient_id}/escalate`, { reason: `Patient asked for help at ${robotId}`, active: true })}>
            {t.help}
          </button>
        )}
      </div>

      {virtual && (
        <form className="lookup" onSubmit={findByQueue}>
          <label>{t.queue}<input inputMode="numeric" value={q} onChange={(e) => setQ(e.target.value)} placeholder="1001" /></label>
          <button className="big" type="submit">{t.find}</button>
        </form>
      )}
      {msg && <p className="error" role="alert">{msg}</p>}
      <p className="disclaimer">{t.disclaimer}</p>
    </main>
  );
}
