"""Shared navigation data types (used by every adapter and by the backend)."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
import time


class NavState(str, Enum):
    IDLE = "IDLE"
    NAVIGATING = "NAVIGATING"
    BLOCKED = "BLOCKED"
    ARRIVED = "ARRIVED"
    FAILED = "FAILED"
    EMERGENCY_STOP = "EMERGENCY_STOP"


ACTIVE_STATES = {NavState.NAVIGATING, NavState.BLOCKED}


@dataclass
class Route:
    destination_station: str
    route_id: str | None = None
    nodes: list[str] = field(default_factory=list)
    waypoints: list[dict] = field(default_factory=list)          # [{"node", "x_m", "y_m", "marker"}]
    junction_instructions: list[dict] = field(default_factory=list)  # [{"at_marker", "action"}] for line following
    distance_m: float | None = None
    edge_lengths: list[float] = field(default_factory=list)   # metres per route segment
    room_id: str | None = None                                # specific room at the destination
    # ---- line-following contract (what the ESP32 firmware actually executes)
    track_type: str = "graph"                                 # "loop" (table demo) or "graph"
    heading_from: str | None = None                           # node the robot is facing away from at the start
    mid_segment: bool = False                                 # robot starts between nodes (turned around mid-corridor)
    start_action: str = "GO"                                  # GO | U_TURN | LEFT | RIGHT before following the line
    marks: list[dict] = field(default_factory=list)           # tape features in crossing order (see route_planner)
    scan_side: str = "LEFT"                                   # side of the track the destination room is on
    shapes: list = field(default_factory=list)                # per edge: tape polyline [(x, y), ...] in travel order

    @property
    def mark_nodes(self) -> list[str]:
        return [m["node"] for m in self.marks]

    def to_dict(self):
        return asdict(self)


@dataclass
class NavTask:
    task_id: str
    robot_id: str
    patient_id: str
    destination_station: str
    route: Route | None = None
    state: NavState = NavState.NAVIGATING
    progress: float = 0.0               # 0..1 (simulation only)
    reason: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def command(self) -> dict:
        """The command contract shared by the simulation and ESP32 adapters."""
        return {"task_id": self.task_id, "patient_id": self.patient_id, "command": "NAVIGATE",
                "destination_station": self.destination_station,
                "room_id": self.route.room_id if self.route else None,
                "scan_room_on_arrival": bool(self.route and self.route.room_id),
                "route_id": self.route.route_id if self.route else None,
                "waypoints": self.route.waypoints if self.route else [],
                "junction_instructions": self.route.junction_instructions if self.route else [],
                # compact form for the line-following firmware: count tape marks, act at each
                "track_type": self.route.track_type if self.route else None,
                "start_action": self.route.start_action if self.route else "GO",
                "mark_actions": [m["action"] for m in self.route.marks] if self.route else [],
                # per feature: t = S(tation)/J(unction), p = expected pattern C/L/R, a = action,
                # d = taped cm from the previous feature (0 = unknown), m = give up after this many cm
                "features": [{"t": m["kind"][0], "p": m["pattern"], "a": m["action"], "d": m["dist_cm"],
                              "m": m["max_cm"]} for m in self.route.marks] if self.route else [],
                "scan_side": self.route.scan_side if self.route else "LEFT"}

    def to_dict(self):
        d = asdict(self)
        d["state"] = self.state.value
        return d


@dataclass
class RobotStatus:
    robot_id: str
    kind: str                           # "physical" | "virtual"
    adapter: str                        # "simulation" | "esp32" | "none"
    state: NavState = NavState.IDLE
    task_id: str | None = None
    position: str | None = None         # last known station / marker (no continuous localisation assumed)
    comm_ok: bool = True
    last_seen: float = field(default_factory=time.time)
    message: str | None = None

    def to_dict(self):
        d = asdict(self)
        d["state"] = self.state.value
        return d
