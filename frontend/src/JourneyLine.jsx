import { LABEL } from "./api";

/** A patient's journey drawn as a floor wayfinding line: done stops are filled,
 *  the current stop is ringed, steps allowed next are outlined, later steps are faint. */
export default function JourneyLine({ required, completed, eligible = [], current, large = false, labels = LABEL }) {
  const done = [...completed];
  const stops = required.map((step) => {
    const i = done.indexOf(step);
    if (i >= 0) {
      done.splice(i, 1);
      return { step, state: "done" };
    }
    if (current && step === current) return { step, state: "now" };
    if (eligible.includes(step)) return { step, state: "next" };
    return { step, state: "later" };
  });
  // the step in progress shows as "now" only once
  let nowSeen = false;
  stops.forEach((s) => {
    if (s.state === "now") {
      if (nowSeen) s.state = "later";
      nowSeen = true;
    }
  });
  return (
    <ol className={`journey ${large ? "journey-large" : ""}`} aria-label="Journey">
      {stops.map((s, i) => (
        <li key={i} className={`stop stop-${s.state}`}>
          <span className="dot" aria-hidden="true" />
          <span className="stop-name">{labels[s.step] || s.step}</span>
          <span className="sr-only">
            {{ done: "completed", now: "current", next: "can go now", later: "later" }[s.state]}
          </span>
        </li>
      ))}
    </ol>
  );
}
