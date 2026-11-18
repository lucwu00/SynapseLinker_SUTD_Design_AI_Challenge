"""Load and validate the classroom tape-track map (config/classroom_map.json, else the example).

The map describes the TAPE TRACK the team lays on the floor (junction markers, corridors and
room stops), so it does not need the classroom's architectural floor plan.
"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path

import networkx as nx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAP = ROOT / "config" / "classroom_map.json"
EXAMPLE_MAP = ROOT / "config" / "classroom_map.example.json"
TABLE_MAP = ROOT / "config" / "table_map_60cm.json"


def default_map_path() -> Path:
    """SYNAPSE_MAP env var > config/classroom_map.json > the 60 x 60 cm table loop > the example."""
    env = os.environ.get("SYNAPSE_MAP")
    if env:
        p = Path(env)
        return p if p.is_absolute() else ROOT / p
    for p in (DEFAULT_MAP, TABLE_MAP):
        if p.exists():
            return p
    return EXAMPLE_MAP


class MapError(ValueError):
    pass


def load_map(path: Path | str | None = None) -> dict:
    p = Path(path) if path else default_map_path()
    m = json.loads(p.read_text(encoding="utf-8"))
    validate_map(m)
    m["_path"] = str(p)
    return m


def validate_map(m: dict):
    for k in ("units", "frame", "start_node", "nodes", "edges"):
        if k not in m:
            raise MapError(f"map missing '{k}'")
    if m["units"] != "metres":
        raise MapError("units must be 'metres'")
    nodes = m["nodes"]
    for nid, n in nodes.items():
        for k in ("x", "y"):
            if not isinstance(n.get(k), (int, float)):
                raise MapError(f"node {nid} needs numeric {k}")
    if m["start_node"] not in nodes:
        raise MapError("start_node not in nodes")
    for e in m["edges"]:
        if e["from"] not in nodes or e["to"] not in nodes:
            raise MapError(f"edge {e} references unknown node")
        if "distance_m" in e and e["distance_m"] <= 0:
            raise MapError(f"edge {e} has non-positive distance")
    restricted = set(m.get("restricted_nodes", []))
    rooms = m.get("rooms")
    if rooms is None:                       # legacy format: {"stations": {station: node}}
        if "stations" not in m:
            raise MapError("map needs 'rooms'")
        rooms = {f"{st}_ROOM": {"node": nid, "station": st, "label": st, "classroom_label": nid}
                 for st, nid in m["stations"].items()}
        m["rooms"] = rooms
    for rid, r in rooms.items():
        for k in ("node", "station", "label"):
            if k not in r:
                raise MapError(f"room {rid} missing '{k}'")
        if r["node"] not in nodes:
            raise MapError(f"room {rid} -> unknown node {r['node']}")
        if r["node"] in restricted:
            raise MapError(f"room {rid} is inside a restricted area")
    for e in m["edges"]:
        for v in e.get("via", []):
            if not (isinstance(v, (list, tuple)) and len(v) == 2):
                raise MapError(f"edge {e['from']}-{e['to']}: via points must be [x, y]")
    if m.get("start_facing"):
        ok = any({e["from"], e["to"]} == {m["start_node"], m["start_facing"]} for e in m["edges"])
        if not ok:
            raise MapError("start_facing must be a node next to start_node")
    for nid, n in nodes.items():
        if n.get("kind") not in (None, "station", "junction", "waypoint"):
            raise MapError(f"node {nid}: kind must be station, junction or waypoint")
    # derived lookups
    m["station_rooms"] = {}
    for rid, r in rooms.items():
        m["station_rooms"].setdefault(r["station"], []).append(rid)
    m["stations"] = {st: rooms[rids[0]]["node"] for st, rids in m["station_rooms"].items()}
    m["node_room"] = {r["node"]: rid for rid, r in rooms.items()}
    degree = {nid: 0 for nid in nodes}
    for e in m["edges"]:
        degree[e["from"]] += 1; degree[e["to"]] += 1
    m["node_kind"] = {nid: n.get("kind") or ("junction" if degree[nid] >= 3 else
                                             "station" if n.get("marker") else "waypoint")
                      for nid, n in nodes.items()}


def edge_key(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted((a, b)))


def _edge(m: dict, a: str, b: str) -> dict:
    for e in m["edges"]:
        if (e["from"], e["to"]) in ((a, b), (b, a)):
            return e
    raise MapError(f"no corridor {a}-{b}")


def edge_points(m: dict, a: str, b: str) -> list[tuple[float, float]]:
    """Tape centre line from node a to node b: the nodes plus any 'via' corner points, in travel order."""
    e = _edge(m, a, b)
    n = m["nodes"]
    pts = [(n[e["from"]]["x"], n[e["from"]]["y"])] + [tuple(v) for v in e.get("via", [])] + \
          [(n[e["to"]]["x"], n[e["to"]]["y"])]
    return pts if e["from"] == a else pts[::-1]


def edge_length(m: dict, e: dict) -> float:
    """Measured distance if given, else the polyline length with corners rounded to the track radius."""
    if e.get("distance_m"):
        return float(e["distance_m"])
    pts = edge_points(m, e["from"], e["to"])
    d = sum(math.dist(p, q) for p, q in zip(pts, pts[1:]))
    r = float(m.get("track", {}).get("corner_radius_m", 0))
    for p, q, s in zip(pts, pts[1:], pts[2:]):       # each rounded corner is shorter than the sharp one
        a1 = math.atan2(q[1] - p[1], q[0] - p[0]); a2 = math.atan2(s[1] - q[1], s[0] - q[0])
        phi = abs((a2 - a1 + math.pi) % (2 * math.pi) - math.pi)
        d -= r * (2 * math.tan(phi / 2) - phi)
    return d


def to_graph(m: dict, blocked: set | None = None) -> nx.Graph:
    """Walkable graph. Restricted nodes and corridors known to be blocked are left out."""
    g = nx.Graph()
    restricted = set(m.get("restricted_nodes", []))
    blocked = blocked or set()
    for nid, n in m["nodes"].items():
        if nid not in restricted:
            g.add_node(nid, **n)
    for e in m["edges"]:
        if e["from"] in restricted or e["to"] in restricted or edge_key(e["from"], e["to"]) in blocked:
            continue
        d = edge_length(m, e)
        g.add_edge(e["from"], e["to"], weight=d * float(e.get("cost_factor", 1.0)), distance_m=d)
    return g
