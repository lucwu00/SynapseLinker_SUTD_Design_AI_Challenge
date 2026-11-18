import { useEffect, useState } from "react";
import { api, LABEL } from "./api";

/** Point at arc length `s` along a rounded-square loop that starts at the middle of the bottom side and runs
 *  counter-clockwise (the 60 x 60 cm table track). Returns metres in table coordinates. */
function loopGeometry(track, table) {
  const lo = track.inset_m, hi = table.w - track.inset_m, r = track.corner_radius_m, mid = (lo + hi) / 2;
  const side = hi - lo - 2 * r, arc = (Math.PI * r) / 2;
  const parts = [                       // [length, fn(t in 0..1) -> {x,y}]
    [side / 2, (t) => ({ x: mid + t * side / 2, y: lo })],
    [arc, (t) => ({ x: hi - r + r * Math.sin(t * Math.PI / 2), y: lo + r - r * Math.cos(t * Math.PI / 2) })],
    [side, (t) => ({ x: hi, y: lo + r + t * side })],
    [arc, (t) => ({ x: hi - r + r * Math.cos(t * Math.PI / 2), y: hi - r + r * Math.sin(t * Math.PI / 2) })],
    [side, (t) => ({ x: hi - r - t * side, y: hi })],
    [arc, (t) => ({ x: lo + r - r * Math.sin(t * Math.PI / 2), y: hi - r + r * Math.cos(t * Math.PI / 2) })],
    [side, (t) => ({ x: lo, y: hi - r - t * side })],
    [arc, (t) => ({ x: lo + r - r * Math.cos(t * Math.PI / 2), y: lo + r - r * Math.sin(t * Math.PI / 2) })],
    [side / 2, (t) => ({ x: lo + r + t * side / 2, y: lo })],
  ];
  const total = parts.reduce((a, p) => a + p[0], 0);
  const at = (s) => {
    s = ((s % total) + total) % total;
    for (const [len, f] of parts) { if (s <= len + 1e-9) return f(len ? s / len : 0); s -= len; }
    return parts[0][1](0);
  };
  return { total, at };
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

  const table = m.table && m.track && m.loop_order ? m.table : null;   // 60 x 60 cm demo table
  const xs = m.nodes.map((n) => n.x), ys = m.nodes.map((n) => n.y);
  const pad = 0.75;
  const [minX, maxX, minY, maxY] = table ? [0, table.w, 0, table.h]
    : [Math.min(...xs) - pad, Math.max(...xs) + pad, Math.min(...ys) - pad, Math.max(...ys) + pad];
  const S = table ? 480 / table.w : 120, W = (maxX - minX) * S, H = (maxY - minY) * S;
  const px = (x) => (x - minX) * S, py = (y) => (maxY - y) * S;
  const node = Object.fromEntries(m.nodes.map((n) => [n.id, n]));
  const robot = m.robots.find((r) => r.position) || null;
  const route = robot?.route || [];
  const onRoute = (a, b) => route.some((n, i) => (n === a && route[i + 1] === b) || (n === b && route[i + 1] === a));

  const canEdit = m.simulated_world && !compact;
  // on the table, corridors follow the rounded tape instead of straight chords
  let seg = null, nodeS = {};
  if (table) {
    const g = loopGeometry(m.track, table), step = g.total / m.loop_order.length;
    m.loop_order.forEach((id, i) => { nodeS[id] = i * step; });
    seg = (a, b) => {
      let s0 = nodeS[a], s1 = nodeS[b];
      if (Math.abs(s1 - s0) > step * 1.5) (s1 < s0 ? (s1 += g.total) : (s0 += g.total));
      const pts = [];
      for (let k = 0; k <= 12; k++) pts.push(g.at(s0 + (s1 - s0) * k / 12));
      return { d: pts.map((p, k) => `${k ? "L" : "M"}${px(p.x).toFixed(1)},${py(p.y).toFixed(1)}`).join(" "), mid: pts[6] };
    };
  }
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
              <rect x="1" y="1" width={W - 2} height={H - 2} rx="4" className="tbl" />
              <text x={W / 2} y={H - 8} className="tbl-t">Presenter side (60 cm)</text>
            </g>
          )}
          {m.edges.map((e, i) => {
            const a = node[e.from], b = node[e.to];
            const cls = e.known_blocked ? "trk trk-blocked" : onRoute(e.from, e.to) ? "trk trk-route" : "trk";
            const sg = seg && seg(e.from, e.to);
            const mid = sg ? sg.mid : { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
            const shape = (c) => sg ? <path d={sg.d} className={c} fill="none" />
              : <line x1={px(a.x)} y1={py(a.y)} x2={px(b.x)} y2={py(b.y)} className={c} />;
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
              if (!n.marker) return null;               // corner waypoints are not taped
              const c = { x: table.w / 2, y: table.h / 2 }, horiz = Math.abs(n.y - c.y) > Math.abs(n.x - c.x);
              const L = (m.track.mark_length_m || 0.05) / 2;
              return (
                <g key={n.id}>
                  <line x1={px(horiz ? n.x : n.x - L)} y1={py(horiz ? n.y - L : n.y)} x2={px(horiz ? n.x : n.x + L)}
                        y2={py(horiz ? n.y + L : n.y)} className="mark" />
                  {n.id === m.nodes[0].id && <text x={px(n.x)} y={py(n.y - 0.045)} className="jn-label">Start</text>}
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
              const c = table.w / 2, out = 0.065;
              const ox = Math.abs(n.x - c) > Math.abs(n.y - c) ? Math.sign(n.x - c) : 0, oy = ox ? 0 : Math.sign(n.y - c);
              const cx = px(n.x + ox * out), cy = py(n.y + oy * out);
              return (
                <g key={r.room_id}>
                  <rect x={cx - 44} y={cy - 22} width="88" height="44" rx="6" className={`room room-${st}`} />
                  <text x={cx} y={cy - 4} className="room-t">{r.classroom_label.replace(" station", "")}</text>
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
