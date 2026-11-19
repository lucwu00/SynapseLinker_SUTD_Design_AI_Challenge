"""Direction-aware route planning on a black-tape track (any shape: loops, T-junctions, spurs).

The map is a graph:
  * nodes   = places the line sensor can recognise: STATION marks (a short bar across the line)
              and JUNCTIONS (where tape branches), plus optional unmarked waypoints;
  * edges   = taped corridors; "via" points describe bends/corners so headings at each end are right.

The planner searches over (node, arrived-from) states, so turning costs and U-turns count, and it
returns what a line-following robot without position sensing can execute:
  * start_action      GO | LEFT | RIGHT | U_TURN (before it starts following the line)
  * features          every tape feature it will cross, in order, with
                      kind (STATION/JUNCTION), the sensor pattern to expect
                      ("C" = tape on both sides, "L"/"R" = branch on one side),
                      the action there (PASS | LEFT | RIGHT | U_TURN | STOP) and the
                      taped distance from the previous feature (cm) so the robot can
                      ignore look-alike patterns on bends and notice a missed marker.
Corridors reported blocked are excluded until cleared; the ML/routing layer never sees any of this.
"""
from __future__ import annotations
import heapq
import math
import uuid

from navigation.map_loader import load_map, to_graph, edge_key, edge_points, MapError
from navigation.navigation_models import Route

TURN_COST_M = {"STRAIGHT": 0.0, "LEFT": 0.03, "RIGHT": 0.03, "U_TURN": 0.12}


def _ang(v) -> float:
    return math.atan2(v[1], v[0])


def classify_turn(arrive: tuple | None, depart: tuple) -> str:
    """Heading change from the arrival direction to the departure direction."""
    if arrive is None:
        return "STRAIGHT"
    d = math.degrees((_ang(depart) - _ang(arrive) + math.pi) % (2 * math.pi) - math.pi)
    if abs(d) < 35:
        return "STRAIGHT"
    if abs(d) > 145:
        return "U_TURN"
    return "LEFT" if d > 0 else "RIGHT"


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

    # ---------------------------------------------------------------- geometry
    def depart_dir(self, a: str, b: str) -> tuple:
        p = edge_points(self.map, a, b)
        return (p[1][0] - p[0][0], p[1][1] - p[0][1])

    def arrive_dir(self, a: str, b: str) -> tuple:
        p = edge_points(self.map, a, b)
        return (p[-1][0] - p[-2][0], p[-1][1] - p[-2][1])

    def _start_heading(self, src: str, heading_from: str | None) -> tuple | None:
        """Direction the robot faces at `src`: arrived from `heading_from`, or the map's start_facing."""
        g = to_graph(self.map)                      # physical tape, blocked or not
        if heading_from and g.has_edge(heading_from, src):
            return self.arrive_dir(heading_from, src)
        if src == self.map["start_node"] and self.map.get("start_facing") and g.has_edge(src, self.map["start_facing"]):
            return self.depart_dir(src, self.map["start_facing"])
        return None

    def kind(self, n: str) -> str:
        return self.map["node_kind"][n]

    def expected_pattern(self, n: str, arrive: tuple) -> str:
        """What the 5-sensor bar should see when crossing node `n` coming in along `arrive`:
        C = tape on both sides (station bar, or arriving at the top of a T), L / R = branch on one side,
        "" = nothing special (a plain bend)."""
        if self.kind(n) == "station":
            return "C"
        sides = set()
        for nb in to_graph(self.map).neighbors(n):              # the tape is there even if blocked
            t = classify_turn(arrive, self.depart_dir(n, nb))
            if t in ("LEFT", "RIGHT"):
                sides.add(t)
        if sides == {"LEFT", "RIGHT"}:
            return "C"
        return {"LEFT": "L", "RIGHT": "R"}.get(next(iter(sides)), "") if sides else ""

    def is_feature(self, n: str, arrive: tuple | None) -> bool:
        return self.kind(n) in ("station", "junction") and (arrive is None or self.expected_pattern(n, arrive) != "")

    # ---------------------------------------------------------------- search
    def _search(self, src: str, dst: str, heading_from: str | None, mid_segment: bool):
        g = self.graph()
        if src not in g or dst not in g:
            return None
        h0 = self._start_heading(src, heading_from)
        start = (src, heading_from if h0 is not None and heading_from else None)
        pq, best, parent = [(0.0, 0, start)], {start: 0.0}, {start: None}
        tick = 0
        while pq:
            cost, _, st = heapq.heappop(pq)
            if cost > best.get(st, math.inf):
                continue
            node, prev = st
            if node == dst:
                path = []
                while st:
                    path.append(st[0]); st = parent[st]
                return cost, path[::-1]
            arrive = self.arrive_dir(prev, node) if prev else h0
            for nb in g.neighbors(node):
                turn = classify_turn(arrive, self.depart_dir(node, nb))
                if node == src and prev == start[1] and mid_segment and nb == heading_from:
                    continue                                    # never drive back into the obstacle
                c = cost + g[node][nb]["weight"] + TURN_COST_M[turn]
                # a turn at a T-junction where the tape carries on is found by distance only (single AN wire):
                # prefer routes that turn at corners, which the robot sees (map: junction_turn_cost_m)
                if turn in ("LEFT", "RIGHT") and g.degree(node) >= 3 and node != src:
                    c += self.map.get("junction_turn_cost_m", 0.0)
                ns = (nb, node)
                if c < best.get(ns, math.inf):
                    best[ns], parent[ns] = c, st
                    tick += 1
                    heapq.heappush(pq, (c, tick, ns))
        return None

    def distance(self, src: str, dst: str, heading_from: str | None = None) -> float | None:
        """Planning cost in metres (taped distance plus small turn penalties)."""
        if src == dst:
            return 0.0
        r = self._search(src, dst, heading_from, False)
        return r[0] if r else None

    # ---------------------------------------------------------------- planning
    def plan_to_node(self, src: str, dst: str, destination_label: str, heading_from: str | None = None,
                     mid_segment: bool = False) -> Route:
        """`heading_from`: node the robot came from (it faces away from it).
        `mid_segment`: the robot is on the corridor between `heading_from` and `src`, driving towards
        `src` (it turned round in front of an obstacle), so it will still cross the feature at `src`."""
        g = self.graph()
        nodes = self.map["nodes"]
        if src == dst:
            path = [src]
        else:
            r = self._search(src, dst, heading_from, mid_segment)
            if not r:
                raise MapError(f"no walkable path from {src} to {dst}")
            path = r[1]
        h0 = self._start_heading(src, heading_from)
        lengths = [g[a][b]["distance_m"] for a, b in zip(path, path[1:])]

        start_action, instr = "GO", []
        if len(path) > 1 and not mid_segment and h0 is not None:
            t = classify_turn(h0, self.depart_dir(src, path[1]))
            if t != "STRAIGHT":
                start_action = t
                instr.append({"at_node": src, "at_marker": nodes[src].get("marker"), "action": t})

        features, since = [], 0.0
        first = 0 if mid_segment else 1
        for i in range(first, len(path)):
            n = path[i]
            if i > 0:
                since += lengths[i - 1]
            arrive = self.arrive_dir(path[i - 1], n) if i > 0 else h0
            last = i == len(path) - 1
            if not last and not self.is_feature(n, arrive):
                continue                                         # plain bend: the robot just follows the line
            if last:
                action = "STOP"
            else:
                t = classify_turn(arrive, self.depart_dir(n, path[i + 1]))
                action = "PASS" if t == "STRAIGHT" else t
            unknown = mid_segment and i == 0                   # we do not know how far back it turned round
            # turned round mid-corridor but `src` has no tape mark (e.g. START): the first mark is `since`
            # beyond src, plus however far the robot was past src - the robot adds that (negative = "add yours")
            plus = mid_segment and not features and i > 0
            features.append({
                "node": n, "marker": nodes[n].get("marker"),
                # a junction with only two corridors is a square CORNER: the tape just ends and turns
                "kind": "CORNER" if self.kind(n) == "junction" and to_graph(self.map).degree(n) == 2
                        else self.kind(n).upper(),
                "pattern": self.expected_pattern(n, arrive) if arrive is not None else "C",
                "action": action,
                "dist_cm": 0 if unknown else (-round(since * 100, 1) if plus else round(since * 100, 1)),
                "max_cm": round((self._edge_len(heading_from, n) if unknown else
                                 since + (self._edge_len(heading_from, src) if plus else 0)) * 100 + 8, 1),
            })
            instr.append({"at_node": n, "at_marker": nodes[n].get("marker"),
                          "action": "STOP" if last else ("STRAIGHT" if action == "PASS" else action)})
            since = 0.0
        if not features or features[-1]["node"] != dst:          # src == dst
            instr.append({"at_node": dst, "at_marker": nodes[dst].get("marker"), "action": "STOP"})

        shapes = [edge_points(self.map, a, b) for a, b in zip(path, path[1:])]
        waypoints = [{"node": n, "x_m": nodes[n]["x"], "y_m": nodes[n]["y"], "marker": nodes[n].get("marker")}
                     for n in path]
        return Route(destination_station=destination_label, route_id=f"R-{uuid.uuid4().hex[:8]}", nodes=path,
                     waypoints=waypoints, junction_instructions=instr, distance_m=round(sum(lengths), 3),
                     edge_lengths=[round(x, 3) for x in lengths], track_type=self.map.get("track_type", "graph"),
                     heading_from=heading_from, mid_segment=mid_segment, start_action=start_action,
                     marks=features, scan_side=self.scan_side(path, heading_from), shapes=shapes)

    def _edge_len(self, a: str | None, b: str) -> float:
        g = to_graph(self.map)
        return g[a][b]["distance_m"] if a and g.has_edge(a, b) else 0.5

    def scan_side(self, path: list[str], heading_from: str | None = None) -> str:
        """Which way to turn at the destination to face the room (its 'zone'), else the map centre."""
        dst = path[-1]
        arrive = self.arrive_dir(path[-2], dst) if len(path) > 1 else self._start_heading(dst, heading_from)
        if arrive is None:
            return "LEFT"
        n = self.map["nodes"]
        room = self.map["rooms"].get(self.map["node_room"].get(dst, ""), {})
        z = room.get("zone")
        if z:
            tx, ty = z["x"], z["y"]
        else:
            tx = sum(v["x"] for v in n.values()) / len(n)
            ty = sum(v["y"] for v in n.values()) / len(n)
        cross = arrive[0] * (ty - n[dst]["y"]) - arrive[1] * (tx - n[dst]["x"])
        return "LEFT" if cross >= 0 else "RIGHT"

    def plan(self, from_node: str | None, destination_station: str) -> Route:
        """Route to the first room of a station (used where no specific room is chosen)."""
        rooms = self.rooms_for(destination_station)
        if not rooms:
            raise MapError(f"station {destination_station} is not on the track")
        return self.plan_to_node(self.node_of(from_node), self.room_node(rooms[0]), destination_station)

    def turns(self, path: list[str], heading_from: str | None = None) -> list[dict]:
        """Turn instructions for a given path (kept for callers that plan the path themselves)."""
        nodes, out = self.map["nodes"], []
        arrive = self._start_heading(path[0], heading_from) if heading_from else None
        for i in range(len(path) - 1):
            a = path[i]
            if i > 0:
                arrive = self.arrive_dir(path[i - 1], a)
            if i > 0 or arrive is not None:
                out.append({"at_node": a, "at_marker": nodes[a].get("marker"),
                            "action": classify_turn(arrive, self.depart_dir(a, path[i + 1]))})
        out.append({"at_node": path[-1], "at_marker": nodes[path[-1]].get("marker"), "action": "STOP"})
        return out
