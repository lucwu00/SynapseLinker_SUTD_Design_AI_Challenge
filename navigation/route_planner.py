"""Route planning on the tape-track map (NetworkX shortest path).

* Corridors reported blocked by the robot are removed from planning until cleared.
* Routes are also given as one instruction per junction marker (LEFT / RIGHT / STRAIGHT /
  U_TURN / STOP) for a line-following robot without continuous localisation.
"""
from __future__ import annotations
import math
import uuid

import networkx as nx

from navigation.map_loader import load_map, to_graph, edge_key, MapError
from navigation.navigation_models import Route


class RoutePlanner:
    def __init__(self, map_data: dict | None = None):
        self.map = map_data or load_map()
        self.blocked: set[tuple[str, str]] = set()      # corridors KNOWN to be blocked (from robot reports)

    # ---------------------------------------------------------------- map helpers
    def graph(self):
        return to_graph(self.map, self.blocked)

    def block(self, a: str, b: str):
        self.blocked.add(edge_key(a, b))

    def unblock(self, a: str, b: str):
        self.blocked.discard(edge_key(a, b))

    def rooms_for(self, station: str) -> list[str]:
        return list(self.map["station_rooms"].get(station, []))

    def room_node(self, room_id: str) -> str:
        try:
            return self.map["rooms"][room_id]["node"]
        except KeyError:
            raise MapError(f"room {room_id} not on the map")

    def on_map(self, station: str) -> bool:
        return station in self.map["station_rooms"]

    def node_of(self, position: str | None) -> str:
        """Robot position may be a node, a room id or a station name; default START."""
        if position in self.map["nodes"]:
            return position
        if position in self.map["rooms"]:
            return self.map["rooms"][position]["node"]
        if position in self.map.get("stations", {}):
            return self.map["stations"][position]
        return self.map["start_node"]

    def distance(self, src: str, dst: str, heading_from: str | None = None) -> float | None:
        """Planning cost (metres, plus a small penalty for turning round on a loop track)."""
        if self.is_loop:
            c = self._loop_candidates(src, dst, heading_from)
            return min(x[0] for x in c) if c else None
        try:
            return nx.shortest_path_length(self.graph(), src, dst, weight="weight")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    # ---------------------------------------------------------------- loop track (60 x 60 cm table)
    U_TURN_PENALTY_M = 0.10

    @property
    def is_loop(self) -> bool:
        return self.map.get("track_type") == "loop"

    def _loop_neighbours(self, n: str) -> tuple[str, str]:
        order = self.map["loop_order"]
        i = order.index(n)
        return order[(i + 1) % len(order)], order[i - 1]          # (counter-clockwise next, previous)

    def forward_of(self, src: str, heading_from: str | None) -> str:
        """Neighbour the robot will drive to if it just follows the line from `src`."""
        nxt, prev = self._loop_neighbours(src)
        if heading_from == prev:
            return nxt
        if heading_from == nxt:
            return prev
        if src == self.map["start_node"] and self.map.get("start_facing") in (nxt, prev):
            return self.map["start_facing"]
        return nxt

    def _walk(self, src: str, dst: str, first: str) -> list[str] | None:
        path, prev, cur = [src], src, first
        for _ in range(len(self.map["loop_order"])):
            path.append(cur)
            if edge_key(prev, cur) in self.blocked:
                return None
            if cur == dst:
                return path
            a, b = self._loop_neighbours(cur)
            prev, cur = cur, (a if b == prev else b)
        return None

    def _loop_candidates(self, src, dst, heading_from):
        if src not in self.map["loop_order"] or dst not in self.map["loop_order"]:
            return []
        if src == dst:
            return [(0.0, [src], "GO")]
        fwd = self.forward_of(src, heading_from)
        nxt, prev = self._loop_neighbours(src)
        back = prev if fwd == nxt else nxt
        g, out = to_graph(self.map), []
        for first, action in ((fwd, "GO"), (back, "U_TURN")):
            path = self._walk(src, dst, first)
            if path:
                d = sum(g[a][b]["distance_m"] for a, b in zip(path, path[1:]))
                out.append((d + (self.U_TURN_PENALTY_M if action == "U_TURN" else 0), path, action))
        return out

    def _scan_side(self, path: list[str], heading_from: str | None) -> str:
        """Rooms sit inside the loop; is the inside on the robot's left or right when it stops?"""
        n = self.map["nodes"]
        a, b = (path[-2], path[-1]) if len(path) > 1 else (heading_from, path[-1])
        if a is None or a not in n:
            return "LEFT"
        loop = self.map.get("loop_order") or list(n)
        cx, cy = sum(n[k]["x"] for k in loop) / len(loop), sum(n[k]["y"] for k in loop) / len(loop)
        dx, dy = n[b]["x"] - n[a]["x"], n[b]["y"] - n[a]["y"]
        return "LEFT" if dx * (cy - n[b]["y"]) - dy * (cx - n[b]["x"]) >= 0 else "RIGHT"

    def _marks(self, path: list[str], mid_segment: bool, final="STOP", actions: dict | None = None) -> list[dict]:
        nodes = self.map["nodes"]
        seq = path if mid_segment else path[1:]
        out = [{"node": k, "marker": nodes[k].get("marker"), "action": (actions or {}).get(k, "PASS")}
               for k in seq if nodes[k].get("marker")]
        if out and out[-1]["node"] == path[-1]:
            out[-1]["action"] = final
        return out

    # ---------------------------------------------------------------- planning
    def plan_to_node(self, src: str, dst: str, destination_label: str, heading_from: str | None = None,
                     mid_segment: bool = False) -> Route:
        """`heading_from`: node the robot is facing away from (it came from there).
        `mid_segment`: the robot is between `heading_from` and `src`, driving towards `src`
        (after turning round in front of an obstacle), so it will still cross the mark at `src`."""
        g = self.graph()
        nodes = self.map["nodes"]
        if self.is_loop:
            c = self._loop_candidates(src, dst, heading_from)
            if not c:
                raise MapError(f"no open way round the loop from {src} to {dst}")
            _, path, start_action = min(c, key=lambda x: x[0])
            if mid_segment and start_action == "U_TURN":       # never turn back into the obstacle
                raise MapError(f"no open way round the loop from {src} to {dst}")
            marks = self._marks(path, mid_segment)
            instr = ([{"at_node": src, "at_marker": nodes[src].get("marker"), "action": "U_TURN"}]
                     if start_action == "U_TURN" else [])
            instr += [{"at_node": m["node"], "at_marker": m["marker"],
                       "action": "STOP" if m["action"] == "STOP" else "STRAIGHT"} for m in marks]
            if not instr or instr[-1]["action"] != "STOP":
                instr.append({"at_node": dst, "at_marker": nodes[dst].get("marker"), "action": "STOP"})
        else:
            try:
                path = nx.shortest_path(g, src, dst, weight="weight")
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                raise MapError(f"no walkable path from {src} to {dst}")
            instr = self.turns(path, heading_from)
            start_action = "GO"
            turn_at_start = heading_from and len(path) > 1 and instr and instr[0]["at_node"] == src
            if turn_at_start and not mid_segment:      # robot is standing on the mark: turn, then go
                start_action = {"STRAIGHT": "GO"}.get(instr[0]["action"], instr[0]["action"])
            acts = {i["at_node"]: ("PASS" if i["action"] == "STRAIGHT" else i["action"])
                    for i in instr if mid_segment or not (i["at_node"] == src and len(path) > 1)}
            marks = self._marks(path, mid_segment, actions=acts)
        dist = sum(g[a][b]["distance_m"] for a, b in zip(path, path[1:])) if len(path) > 1 else 0.0
        waypoints = [{"node": n, "x_m": nodes[n]["x"], "y_m": nodes[n]["y"], "marker": nodes[n].get("marker")}
                     for n in path]
        return Route(destination_station=destination_label, route_id=f"R-{uuid.uuid4().hex[:8]}", nodes=path,
                     waypoints=waypoints, junction_instructions=instr, distance_m=round(dist, 3),
                     edge_lengths=[round(g[a][b]["distance_m"], 3) for a, b in zip(path, path[1:])],
                     track_type="loop" if self.is_loop else "graph", heading_from=heading_from,
                     mid_segment=mid_segment, start_action=start_action, marks=marks,
                     scan_side=self._scan_side(path, heading_from))

    def plan(self, from_node: str | None, destination_station: str) -> Route:
        """Route to the first room of a station (used where no specific room is chosen)."""
        rooms = self.rooms_for(destination_station)
        if not rooms:
            raise MapError(f"station {destination_station} is not on the classroom track")
        return self.plan_to_node(self.node_of(from_node), self.room_node(rooms[0]), destination_station)

    def _heading(self, a: str, b: str) -> float:
        n = self.map["nodes"]
        return math.atan2(n[b]["y"] - n[a]["y"], n[b]["x"] - n[a]["x"])

    def turns(self, path: list[str], heading_from: str | None = None) -> list[dict]:
        """`heading_from`: node the robot came from, so the first instruction at the start node is a
        turn relative to the direction the robot is facing (needed after a reroute)."""
        nodes, out = self.map["nodes"], []
        seq = ([heading_from] if heading_from and len(path) > 1 else []) + path
        for a, b, c in zip(seq, seq[1:], seq[2:]):
            turn = math.degrees((self._heading(b, c) - self._heading(a, b) + math.pi) % (2 * math.pi) - math.pi)
            if abs(turn) < 30:
                action = "STRAIGHT"
            elif abs(turn) > 150:
                action = "U_TURN"
            else:
                action = "LEFT" if turn > 0 else "RIGHT"
            out.append({"at_node": b, "at_marker": nodes[b].get("marker"), "action": action})
        if len(path) == 1:
            out = []
        out.append({"at_node": path[-1], "at_marker": nodes[path[-1]].get("marker"), "action": "STOP"})
        return out
