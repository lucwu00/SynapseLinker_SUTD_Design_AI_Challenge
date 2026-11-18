import { useEffect, useState } from "react";
import { api, LABEL } from "./api";

/** Tape centre line of a corridor: its two nodes plus any "via" corners, drawn with rounded corners
 *  (radius r, metres). Returns an SVG path and the point halfway along. */
function corridorPath(pts, r, px, py) {
  const len = (p, q) => Math.hypot(q.x - p.x, q.y - p.y);
  let d = `M${px(pts[0].x).toFixed(1)},${py(pts[0].y).toFixed(1)}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const p = pts[i - 1], c = pts[i], n = pts[i + 1];
    const k1 = Math.min(r, len(p, c) / 2) / (len(p, c) || 1), k2 = Math.min(r, len(c, n) / 2) / (len(c, n) || 1);
    const a = { x: c.x + (p.x - c.x) * k1, y: c.y + (p.y - c.y) * k1 }, b = { x: c.x + (n.x - c.x) * k2, y: c.y + (n.y - c.y) * k2 };
    d += ` L${px(a.x).toFixed(1)},${py(a.y).toFixed(1)} Q${px(c.x).toFixed(1)},${py(c.y).toFixed(1)} ${px(b.x).toFixed(1)},${py(b.y).toFixed(1)}`;
  }
  const last = pts[pts.length - 1];
  d += ` L${px(last.x).toFixed(1)},${py(last.y).toFixed(1)}`;
  const segs = pts.slice(1).map((q, i) => len(pts[i], q));
  let half = segs.reduce((x, y) => x + y, 0) / 2, mid = pts[0];
  for (let i = 0; i < segs.length; i++) {
    if (half <= segs[i]) { const f = half / (segs[i] || 1); mid = { x: pts[i].x + f * (pts[i + 1].x - pts[i].x), y: pts[i].y + f * (pts[i + 1].y - pts[i].y) }; break; }
    half -= segs[i];
  }
  return { d, mid };
}

/** Live view of the classroom tape track: corridors, rooms, the robot and its route.
 *  Click a corridor to place or remove a simulated obstacle; use the room buttons to simulate people. */
export default function FloorMap({ compact = false }) {
  const [m, setM] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let alive = true;
    const load = () => api("/navigation/map").then((d) => alive && setM(d)).catch((e) => alive && setErr(e.message));
    load();
    const t = setInterval(load, 600);                 // the robot moves continuously
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (err && !m) return <p className="error">{err}</p>;
  if (!m) return null;

  const table = m.table || null;                     // tabletop demo (e.g. 60 x 60 cm)
  const xs = m.nodes.map((n) => n.x), ys = m.nodes.map((n) => n.y);
  const pad = 0.75, tp = 0.07;                        // tp: room cards may hang over the table edge
  const [minX, maxX, minY, maxY] = table ? [-tp, table.w + tp, -tp, table.h + tp]
    : [Math.min(...xs) - pad, Math.max(...xs) + pad, Math.min(...ys) - pad, Math.max(...ys) + pad];
  const S = table ? 480 / table.w : 120, W = (maxX - minX) * S, H = (maxY - minY) * S;
  const px = (x) => (x - minX) * S, py = (y) => (maxY - y) * S;
  const node = Object.fromEntries(m.nodes.map((n) => [n.id, n]));
  const robot = m.robots.find((r) => r.position) || null;
  const route = robot?.route || [];
  const onRoute = (a, b) => route.some((n, i) => (n === a && route[i + 1] === b) || (n === b && route[i + 1] === a));

  const canEdit = m.simulated_world && !compact;
  const R = m.track?.corner_radius_m || 0;
  const seg = (e) => corridorPath([node[e.from], ...(e.via || []).map(([x, y]) => ({ x, y })), node[e.to]], R, px, py);
  const kind = m.node_kind || {};
  const tangent = (id) => {                           // tape direction at a node (for drawing its station bar)
    const e = m.edges.find((x) => x.from === id || x.to === id);
    if (!e) return { x: 1, y: 0 };
    const pts = [node[e.from], ...(e.via || []).map(([x, y]) => ({ x, y })), node[e.to]];
    const [p, q] = e.from === id ? [pts[0], pts[1]] : [pts[pts.length - 1], pts[pts.length - 2]];
    const L = Math.hypot(q.x - p.x, q.y - p.y) || 1;
    return { x: (q.x - p.x) / L, y: (q.y - p.y) / L };
  };
  const toggleObstacle = (e) =>
    api("/navigation/obstacles", "POST", { from_node: e.from, to_node: e.to, present: !e.obstacle }).then(setM).catch((x) => setErr(x.message));
  const room = (id, body) => api(`/rooms/${id}/status`, "POST", body).then(setM).catch((x) => setErr(x.message));

  return (
    <section className={`floor ${compact ? "floor-compact" : ""}`}>
      <h2>{compact ? "Floor (demo track)" : table ? "Demo table track" : "Classroom track"}</h2>
      {!compact && (
        <p className="small muted">
          {m.example ? "Example track: replace with your measured tape layout (config/classroom_map.json)." : `Track: ${m.path}`}{" "}
          {m.simulated_world && "Click a corridor to place a simulated obstacle."}
        </p>
      )}
      <div className="floor-wrap">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Demo track with rooms and robot">
          {table && (
            <g>
              <rect x={px(0)} y={py(table.h)} width={table.w * S} height={table.h * S} rx="4" className="tbl" />
              <text x={px(table.w / 2)} y={py(0) + 14} className="tbl-t">Presenter side ({Math.round(table.w * 100)} cm)</text>
            </g>
          )}
          {m.edges.map((e, i) => {
            const a = node[e.from], b = node[e.to];
            const cls = e.known_blocked ? "trk trk-blocked" : onRoute(e.from, e.to) ? "trk trk-route" : "trk";
            const sg = seg(e), mid = sg.mid;
            const shape = (c) => <path d={sg.d} className={c} fill="none" />;
            return (
              <g key={i} onClick={() => canEdit && toggleObstacle(e)} className={canEdit ? "trk-g" : ""}>
                {shape(cls)}
                {canEdit && <g>{shape("trk-hit")}<title>{`${e.from}-${e.to}: ${e.obstacle ? "remove obstacle" : "place obstacle"}`}</title></g>}
                {e.obstacle && <rect x={px(mid.x) - 11} y={py(mid.y) - 11} width="22" height="22" rx="3" className="obstacle" />}
              </g>
            );
          })}
          {m.nodes.map((n) => {
            if (table) {
              if (kind[n.id] !== "station") return null;   // junctions are just where tape meets
              const t = tangent(n.id), L = (m.track?.mark_length_m || 0.06) / 2;
              return (
                <g key={n.id}>
                  <line x1={px(n.x - t.y * L)} y1={py(n.y + t.x * L)} x2={px(n.x + t.y * L)} y2={py(n.y - t.x * L)} className="mark" />
                  {n.id === m.start_node && <text x={px(n.x)} y={py(n.y) + 34} className="jn-label">Start</text>}
                </g>
              );
            }
            return (
              <g key={n.id}>
                <circle cx={px(n.x)} cy={py(n.y)} r={n.id.startsWith("N_") ? 0 : 5} className="jn" />
                {!n.id.startsWith("N_") && <text x={px(n.x)} y={py(n.y) + 20} className="jn-label">{n.id === "START" ? "Start" : n.id}</text>}
              </g>
            );
          })}
          {m.rooms.map((r) => {
            const n = node[r.node];
            const st = !r.staffed ? "closed" : r.occupied ? "occ" : "free";
            if (table) {             // card outside the loop beside the mark; dashed spot inside = where the "person" block goes
              const z = r.zone || { x: table.w / 2, y: table.h / 2 }, out = 0.075;
              const ox = Math.abs(n.x - z.x) > Math.abs(n.y - z.y) ? Math.sign(n.x - z.x) : 0, oy = ox ? 0 : Math.sign(n.y - z.y);
              const cx = px(n.x + ox * out), cy = py(n.y + oy * out);
              return (
                <g key={r.room_id}>
                  <rect x={cx - 52} y={cy - 22} width="104" height="44" rx="6" className={`room room-${st}`} />
                  <text x={cx} y={cy - 4} className="room-t">{r.classroom_label}</text>
                  <text x={cx} y={cy + 12} className="room-s">{r.label}</text>
                  {r.zone && <rect x={px(r.zone.x) - 12} y={py(r.zone.y) - 12} width="24" height="24" rx="3"
                                   className={`zone ${r.person_present ? "zone-on" : ""}`}><title>{`${r.label}: place the person block here`}</title></rect>}
                </g>
              );
            }
            return (
              <g key={r.room_id}>
                <rect x={px(n.x) - 52} y={py(n.y) - 46} width="104" height="46" rx="6" className={`room room-${st}`} />
                <text x={px(n.x)} y={py(n.y) - 28} className="room-t">{r.classroom_label}</text>
                <text x={px(n.x)} y={py(n.y) - 12} className="room-s">{r.label}</text>
                {r.person_present && <circle cx={px(n.x) + 44} cy={py(n.y) - 38} r="6" className="person"><title>Someone is in this room</title></circle>}
              </g>
            );
          })}
          {robot?.position && (
            <g>
              <circle cx={px(robot.position.x)} cy={py(robot.position.y)} r="11" className={`bot bot-${robot.state.toLowerCase()}`} />
              <text x={px(robot.position.x)} y={py(robot.position.y) - 16} className="bot-t">{robot.robot_id}</text>
            </g>
          )}
        </svg>
        {!compact && <ul className="room-list">
          {m.rooms.map((r) => (
            <li key={r.room_id}>
              <strong>{r.classroom_label}</strong> {r.label}
              <span className={`room-state room-${!r.staffed ? "closed" : r.occupied ? "occ" : "free"}`}>
                {!r.staffed ? "Closed" : r.occupied ? "Occupied" : "Free"}
              </span>
              <span className="small muted"> (system knows from: {r.source})</span>
              <div className="row">
                {m.simulated_world && (
                  <button onClick={() => room(r.room_id, { person_present: !r.person_present })}>
                    {r.person_present ? "Person leaves" : "Person walks in"} (unreported)
                  </button>
                )}
                <button onClick={() => room(r.room_id, { occupied: !r.occupied, source: "IoT sensor" })}>
                  Sensor: {r.occupied ? "free" : "occupied"}
                </button>
                <button onClick={() => room(r.room_id, { staffed: !r.staffed })}>{r.staffed ? "Close" : "Open"}</button>
              </div>
            </li>
          ))}
        </ul>}
      </div>
      {robot && !compact && (
        <p className="small">
          {robot.robot_id}: {robot.state.toLowerCase()}
          {robot.route.length > 0 && <> on route {robot.route.join(" → ")}</>}
          {robot.message && <span className="warn"> {robot.message}</span>}
        </p>
      )}
      {!compact && <p className="small muted">
        Legend: <span className="lg lg-route" /> current route <span className="lg lg-blocked" /> corridor reported blocked{" "}
        <span className="lg lg-obst" /> obstacle (simulated) <span className="lg lg-person" /> person in room, not yet detected.
        Stations not on the track ({["PHLEBOTOMY", "ECG", "PAYMENT", "PHARMACY"].map((s) => LABEL[s]).join(", ")}) get spoken directions only.
      </p>}
    </section>
  );
}
