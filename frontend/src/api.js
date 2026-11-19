import { useCallback, useEffect, useRef, useState } from "react";

// Where the backend lives. Empty = same address as this page (laptop: FastAPI serves both).
// On Vercel, set VITE_API_BASE to the Render URL, e.g. https://synapse-linker-api.onrender.com
const ENV = (typeof import.meta !== "undefined" && import.meta.env) || {};
export const API_BASE = (ENV.VITE_API_BASE || "").replace(/\/+$/, "");

/** Loading/offline text. A hosted backend on a free plan sleeps when idle and takes ~1 min to wake. */
export const connectText = (error, localHint) =>
  !error ? "Connecting…"
    : API_BASE ? "Waking the hosted server. Free hosting sleeps when idle; this takes up to a minute. The page retries by itself."
    : `Cannot reach the backend: ${error}.${localHint ? " " + localHint : ""}`;

export async function api(path, method = "GET", body) {
  const res = await fetch(API_BASE + path.replace(/ /g, "%20"), {   // patient IDs may contain spaces
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    const msg = data.error || (Array.isArray(d) ? d.map((x) => `${x.loc?.slice(-1)[0]}: ${x.msg}`).join("; ") : d);
    throw new Error(msg || `${res.status} ${res.statusText}`);
  }
  return data;
}

/** Fetches `path`, then refetches whenever the backend publishes an update (server-sent events).
 *  Falls back to polling every 5 s if the event stream drops. */
export function useLive(path) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const timer = useRef(null);

  const refresh = useCallback(async () => {
    try {
      setData(await api(path));
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, [path]);

  useEffect(() => {
    refresh();
    const es = new EventSource(API_BASE + "/events/stream");
    es.onmessage = () => {
      clearTimeout(timer.current);
      timer.current = setTimeout(refresh, 250);
    };
    const poll = setInterval(refresh, 5000);
    return () => {
      es.close();
      clearInterval(poll);
      clearTimeout(timer.current);
    };
  }, [refresh]);

  return [data, error, refresh];
}

export const LABEL = {
  REGISTRATION: "Registration",
  XRAY: "X-ray",
  PHLEBOTOMY: "Blood test",
  ECG: "ECG",
  CONSULT: "Consultation",
  REVIEW: "Doctor review",
  CAST_ROOM: "Cast room",
  DRESSING: "Dressing room",
  PROCEDURE: "Procedure room",
  PAYMENT: "Payment",
  PHARMACY: "Pharmacy",
};

export const VISIT = { new: "New referral", follow_up: "Follow-up", pre_op: "Pre-op assessment" };

// Display names follow CGH Orthopaedic Surgery's published subspecialty names; the model's internal
// categories are unchanged.
export const SUBSPECIALTY = {
  "Knee/Hip": "Hip & Knee",
  "Spine/Back": "Integrated Spine",
  "Shoulder/Elbow": "Shoulder & Elbow",
  "Hand/Wrist": "Hand & Wrist",
  "Foot/Ankle": "Foot & Ankle",
  Fracture: "Trauma (fracture)",
  "Post-op": "Post-operative review",
};

/** Show "Mdm W." rather than full names on shared screens. */
export const maskName = (n) => {
  if (!n) return "";
  const parts = n.replace(/\(.*\)/, "").trim().split(/\s+/);
  return parts.length > 1 ? `${parts[0]} ${parts[1][0]}.` : parts[0];
};

export const STATUS = {
  AWAITING_GUIDANCE: "Ready for next service",
  GUIDED: "On the way",
  QUEUED: "Waiting at station",
  IN_SERVICE: "Being seen",
  COMPLETED: "Visit complete",
  ESCALATED: "With staff",
};

export const fmtMin = (m) => (m == null ? "–" : `${Math.round(m)} min`);
