import { useEffect, useRef, useState } from "react";
import { api, useLive, connectText } from "./api";
import { LANGS, PLACE, TURN, T, VOICE, fill } from "./i18n";
import { answer, Recognition, secureOk } from "./voice";
import JourneyLine from "./JourneyLine";

function speak(text, lang) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = lang;
  u.rate = 0.9;
  window.speechSynthesis.speak(u);
}

const MAX_TURNS = 30;   // the conversation is kept on this screen only, and cleared with the next patient

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

  // ---- voice / typed questions
  const [listening, setListening] = useState(false);
  const [heard, setHeard] = useState("");
  const [turns, setTurns] = useState([]);
  const [sheet, setSheet] = useState(false);
  const [typed, setTyped] = useState("");
  const recRef = useRef(null);
  const ctxRef = useRef(null);
  const logRef = useRef(null);
  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [turns, sheet]);
  const patientId = s?.journey?.patient?.patient_id;
  useEffect(() => { setTurns([]); setSheet(false); }, [patientId]);   // privacy: new patient, new conversation
  useEffect(() => () => recRef.current?.abort?.(), []);

  const t = T[lang], P = PLACE[lang], TR = TURN[lang], X = VOICE[lang];
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

  // what the voice answers come from: the same live journey the screen shows
  const say = join(headline + (lang === "zh" ? "。" : "."), line);
  const left = (() => {
    if (!p) return [];
    const done = [...p.completed_steps], pend = [];
    p.required_steps.forEach((x) => { const i = done.indexOf(x); if (i >= 0) done.splice(i, 1); else pend.push(x); });
    const cur = ["GUIDED", "QUEUED", "IN_SERVICE"].includes(p.status) ? p.assigned_destination : null;
    if (cur && pend.indexOf(cur) >= 0) pend.splice(pend.indexOf(cur), 1);
    return pend.map((x) => P[x] || x);
  })();
  const waitText = !p ? "" : p.status === "QUEUED"
    ? join(j.patients_ahead != null ? fill(t.ahead, { n: j.patients_ahead }) + (lang === "zh" ? "。" : ".") : "",
           p.queue?.predicted_wait_min != null ? fill(t.wait, { n: Math.max(0, Math.round(p.queue.predicted_wait_min - (p.queue.waiting_min || 0))) }) : "")
    : cand ? join(`${t.next}: ${place(next)}.`, fill(t.wait, { n: Math.round(cand.predicted_wait_min) })) : say;
  const canGuide = !!(p && p.status === "AWAITING_GUIDANCE" && next && rec.status === "ROUTE");
  ctxRef.current = { lang, p, say, waitText, stepsLeft: left, canGuide, est: j?.estimate, stages: j?.stages,
                     place: (x) => P[x] || x };

  const ask = (text) => {
    const q2 = text.trim();
    if (!q2) return;
    const c = ctxRef.current;
    const r = answer(q2, c, VOICE[c.lang]);
    setTurns((a) => [...a, { who: "you", text: q2 }, { who: "me", text: r.reply }].slice(-MAX_TURNS));
    const lng = typeof r.action === "object" && r.action?.lang;
    speak(r.reply, LANGS.find((l) => l.code === (lng || c.lang)).speech);
    if (lng) setLang(lng);
    if (r.action === "help" && c.p) post(`/patients/${c.p.patient_id}/escalate`, { reason: `Patient asked for help by voice at ${robotId}: "${q2.slice(0, 80)}"`, active: true });
    if (r.action === "guide" && c.canGuide) post(`/patients/${c.p.patient_id}/destination`, { step: next, robot_id: robotId });
  };
  const listen = () => {
    if (listening) { recRef.current?.stop(); return; }
    if (!Recognition || !secureOk) { setTurns((a) => [...a, { who: "me", text: !Recognition ? X.noMic : X.insecure }]); setSheet(true); return; }
    window.speechSynthesis?.cancel();
    const r = new Recognition();
    r.lang = speech; r.interimResults = true; r.continuous = false; r.maxAlternatives = 1;
    let final = "";
    r.onresult = (e) => {
      let txt = "";
      for (let i = 0; i < e.results.length; i++) { txt += e.results[i][0].transcript; if (e.results[i].isFinal) final = txt; }
      setHeard(txt);
    };
    r.onerror = (e) => { if (e.error === "not-allowed" || e.error === "service-not-allowed") setTurns((a) => [...a, { who: "me", text: X.noMic }]); };
    r.onend = () => { setListening(false); setHeard(""); if (final) ask(final); else setTurns((a) => a); };
    recRef.current = r;
    setHeard(""); setListening(true);
    try { r.start(); } catch { setListening(false); }
  };

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

      {(listening || heard) && <p className="voice-live" aria-live="polite">{heard || X.listening}</p>}
      <div className="big-actions">
        <button className={`big voice-btn ${listening ? "voice-on" : ""}`} aria-pressed={listening} onClick={listen}>
          <svg aria-hidden="true" viewBox="0 0 24 24" width="26" height="26"><path fill="currentColor" d="M12 15a3.5 3.5 0 0 0 3.5-3.5v-6a3.5 3.5 0 1 0-7 0v6A3.5 3.5 0 0 0 12 15Zm6-3.5a1 1 0 1 0-2 0 4 4 0 0 1-8 0 1 1 0 1 0-2 0 6 6 0 0 0 5 5.9V20H8.5a1 1 0 1 0 0 2h7a1 1 0 1 0 0-2H13v-2.6a6 6 0 0 0 5-5.9Z"/></svg>
          {listening ? X.stop : X.speak}
        </button>
        <button className="big" onClick={() => speak(say, speech)}>{t.readAloud}</button>
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

      <section className={`convo ${sheet ? "convo-open" : ""}`} aria-label={X.transcript}>
        <button className="convo-handle" aria-expanded={sheet} onClick={() => setSheet(!sheet)}>
          <span className="convo-grip" aria-hidden="true" />
          {sheet ? X.hide : `${X.transcript}${turns.length ? ` (${turns.filter((x) => x.who === "you").length})` : ""}`}
        </button>
        {sheet && (
          <div className="convo-body">
            <ol className="convo-log" aria-live="polite" ref={logRef}>
              {turns.length === 0 && <li className="convo-empty">{X.empty}</li>}
              {turns.map((x, i) => (
                <li key={i} className={`bubble bubble-${x.who}`}><span className="bubble-who">{x.who === "you" ? X.you : X.me}</span>{x.text}</li>
              ))}
            </ol>
            <form className="convo-type" onSubmit={(e) => { e.preventDefault(); ask(typed); setTyped(""); }}>
              <input aria-label={X.type} placeholder={X.type} value={typed} onChange={(e) => setTyped(e.target.value)} maxLength={200} />
              <button type="submit" className="primary">{X.send}</button>
            </form>
          </div>
        )}
      </section>
    </main>
  );
}
