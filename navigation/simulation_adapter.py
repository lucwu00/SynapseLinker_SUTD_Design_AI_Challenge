"""Simulated OjanBot following the same command contract as the ESP32 adapter.

Behaviour mirrors what the physical robot is expected to do on the tape track:
  * drives segment by segment along the route at a conservative speed;
  * an obstacle on the tape is detected ~0.25 m before it -> stop (BLOCKED), no steering around it;
  * if the obstacle clears within `obstacle_wait_s` -> continue (OBSTACLE_CLEARED);
  * otherwise turn round on the line and report PATH_BLOCKED, so the backend can plan the other
    way round (REROUTE) - controlled rerouting, not free-form avoidance;
  * on reaching a room stop, scan the room (ultrasonic/PIR) and report whether it is occupied.

`world` hooks are the simulated physical world (obstacles on the floor, people in rooms); the
backend only learns about them through the robot's reports, as it would with real hardware.
"""
from __future__ import annotations
import time
from typing import Callable

from navigation.navigation_interface import NavigationAdapter, NavigationError
from navigation.navigation_models import NavState, NavTask, RobotStatus, Route, ACTIVE_STATES

DETECT_M = 0.25        # default; maps can set "obstacle_detect_m" (the 60 cm table uses 0.06)


def came_from(route: Route | None) -> str | None:
    """Node the robot drove in from, i.e. which way it is facing when it stops."""
    if not route or not route.nodes:
        return None
    return route.nodes[-2] if len(route.nodes) > 1 else route.heading_from


class SimulationAdapter(NavigationAdapter):
    def __init__(self, robot_id: str, speed_m_s: float = 0.15, on_event=None, kind: str = "physical",
                 obstacle_wait_s: float = 5.0, nodes: dict | None = None, start_node: str = "START",
                 obstacle_on: Callable[[str, str], bool] | None = None,
                 room_occupied: Callable[[str], bool] | None = None, detect_m: float = DETECT_M):
        super().__init__(robot_id, on_event)
        self.speed, self.wait_s, self.nodes, self.detect_m = speed_m_s, obstacle_wait_s, nodes or {}, detect_m
        self.obstacle_on = obstacle_on or (lambda a, b: False)
        self.room_occupied = room_occupied or (lambda room: False)
        self.status = RobotStatus(robot_id=robot_id, kind=kind, adapter="simulation", position=start_node)
        self.current: NavTask | None = None
        self.travelled = 0.0
        self.blocked_edge: tuple[str, str] | None = None
        self.blocked_for = 0.0
        self.manual_obstacle = False
        self.reported = False

    def _emit(self, kind, **data):
        self.status.last_seen = time.time()
        self.on_event(kind, {"robot_id": self.robot_id, **data})

    # ------------------------------------------------------------------ geometry
    def _cum(self, route: Route) -> list[float]:
        c = [0.0]
        for d in route.edge_lengths or []:
            c.append(c[-1] + d)
        return c

    def _edge_index(self) -> int:
        cum = self._cum(self.current.route)
        for i in range(len(cum) - 1):
            if self.travelled < cum[i + 1] - 1e-9:
                return i
        return max(len(cum) - 2, 0)

    def position_xy(self) -> dict | None:
        t = self.current
        if t and t.route and t.route.edge_lengths and t.state in (*ACTIVE_STATES, NavState.EMERGENCY_STOP):
            i = self._edge_index()
            cum = self._cum(t.route)
            a, b = self.nodes.get(t.route.nodes[i]), self.nodes.get(t.route.nodes[i + 1])
            if a and b:
                f = (self.travelled - cum[i]) / max(cum[i + 1] - cum[i], 1e-9)
                return {"x": a["x"] + f * (b["x"] - a["x"]), "y": a["y"] + f * (b["y"] - a["y"])}
        n = self.nodes.get(self.status.position)
        return {"x": n["x"], "y": n["y"]} if n else None

    # ------------------------------------------------------------------ contract
    def navigate(self, patient_id, destination_station, task_id, route: Route | None = None) -> NavTask:
        if task_id in self.tasks:
            return self.tasks[task_id]
        if self.status.state == NavState.EMERGENCY_STOP:
            raise NavigationError("Robot is in EMERGENCY_STOP; an operator must resume it first")
        if self.current and self.current.state in ACTIVE_STATES:
            raise NavigationError(f"Robot busy with task {self.current.task_id}")
        if route is None or len(route.nodes) < 1 or route.distance_m is None or \
                len(route.edge_lengths) != max(len(route.nodes) - 1, 0):
            raise NavigationError("Invalid or missing route; robot will not move")
        task = NavTask(task_id, self.robot_id, patient_id, destination_station, route)
        self.tasks[task_id], self.current = task, task
        self._start(route)
        self._emit("NAV_STARTED", task_id=task_id, destination=destination_station, room_id=route.room_id)
        if route.distance_m == 0:
            self._arrive(task)
        return task

    def _start(self, route: Route):
        self.travelled, self.blocked_edge, self.blocked_for, self.reported, self.manual_obstacle = 0.0, None, 0.0, False, False
        self.current.state = self.status.state = NavState.NAVIGATING
        self.status.task_id, self.status.message = self.current.task_id, None

    def reroute(self, task_id: str, route: Route) -> NavTask:
        """Backend sends a new route for the same task (after PATH_BLOCKED or a room change)."""
        t = self.tasks.get(task_id)
        if not t:
            raise NavigationError(f"Unknown task {task_id}")
        if self.status.state == NavState.EMERGENCY_STOP:
            raise NavigationError("Robot is in EMERGENCY_STOP")
        t.route, t.destination_station = route, route.destination_station
        self.current = t
        self._start(route)
        self._emit("REROUTED", task_id=task_id, nodes=route.nodes, room_id=route.room_id)
        return t

    def tick(self, dt_s: float):
        t = self.current
        if not t or t.route is None:
            return
        st = self.status.state
        if st == NavState.NAVIGATING:
            cum = self._cum(t.route)
            nxt = self.travelled + dt_s * self.speed
            for i, (a, b) in enumerate(zip(t.route.nodes, t.route.nodes[1:])):
                stop_at = cum[i] + max((cum[i + 1] - cum[i]) / 2 - self.detect_m, min(0.05, (cum[i + 1] - cum[i]) / 3))   # obstacle mid-segment
                if self.obstacle_on(a, b) and self.travelled <= stop_at < nxt + 1e-9:
                    self.travelled = stop_at
                    self.blocked_edge, self.blocked_for, self.reported = (a, b), 0.0, False
                    t.state = self.status.state = NavState.BLOCKED
                    self.status.message = "Obstacle ahead - stopped"
                    self._emit("BLOCKED", task_id=t.task_id, edge=[a, b])
                    return
            self.travelled = min(nxt, cum[-1])
            t.progress = self.travelled / max(cum[-1], 1e-9)
            if self.travelled >= cum[-1] - 1e-9:
                self._arrive(t)
        elif st == NavState.BLOCKED and not self.reported:
            a, b = self.blocked_edge
            still = self.manual_obstacle or self.obstacle_on(a, b)
            if not still:
                t.state = self.status.state = NavState.NAVIGATING
                self.status.message, self.blocked_edge = None, None
                self._emit("OBSTACLE_CLEARED", task_id=t.task_id)
                return
            self.blocked_for += dt_s
            if self.blocked_for >= self.wait_s:
                i = t.route.nodes.index(a)
                self.travelled = self._cum(t.route)[i]           # turned round; heading back towards `a`
                self.status.position, self.reported = a, True
                self.status.message = f"Corridor {a}-{b} blocked - turned round, asked backend for another route"
                self._emit("PATH_BLOCKED", task_id=t.task_id, edge=[a, b], at_node=a, heading_from=b,
                           mid_segment=True)

    def _arrive(self, t: NavTask):
        t.state, t.progress = NavState.ARRIVED, 1.0
        node = t.route.nodes[-1] if t.route and t.route.nodes else t.destination_station
        self.status.state, self.status.position, self.status.task_id = NavState.ARRIVED, node, None
        self.status.message = None
        data = dict(task_id=t.task_id, patient_id=t.patient_id, destination=t.destination_station, node=node,
                    came_from=came_from(t.route))
        if t.route and t.route.room_id:
            data.update(room_id=t.route.room_id, room_occupied=bool(self.room_occupied(t.route.room_id)))
        self._emit("ARRIVED", **data)

    # ---- manual obstacle in front of the robot (dashboard button / ultrasonic)
    def simulate_obstacle(self, present: bool = True):
        t = self.current
        if not t or t.state not in ACTIVE_STATES:
            return self.status
        if present and t.state == NavState.NAVIGATING and t.route.edge_lengths:
            i = self._edge_index()
            self.manual_obstacle = True
            self.blocked_edge, self.blocked_for, self.reported = (t.route.nodes[i], t.route.nodes[i + 1]), 0.0, False
            t.state = self.status.state = NavState.BLOCKED
            self.status.message = "Obstacle ahead - stopped"
            self._emit("BLOCKED", task_id=t.task_id, edge=list(self.blocked_edge))
        elif not present:
            self.manual_obstacle = False
            if self.status.state == NavState.BLOCKED and not self.reported:
                t.state = self.status.state = NavState.NAVIGATING
                self.status.message, self.blocked_edge = None, None
                self._emit("OBSTACLE_CLEARED", task_id=t.task_id)
        return self.status

    def get_robot_status(self) -> RobotStatus:
        return self.status

    def _settle_position(self):
        t = self.current
        if t and t.route and t.route.nodes and t.state in (*ACTIVE_STATES, NavState.EMERGENCY_STOP):
            self.status.position = t.route.nodes[self._edge_index()]

    def cancel_navigation(self, task_id=None) -> RobotStatus:
        t = self.current
        if t and t.state in ACTIVE_STATES and (task_id is None or task_id == t.task_id):
            self._settle_position()
            t.state, t.reason = NavState.FAILED, "cancelled"
            self._emit("CANCELLED", task_id=t.task_id)
        if self.status.state != NavState.EMERGENCY_STOP:
            self.status.state, self.status.task_id, self.status.message = NavState.IDLE, None, None
        return self.status

    def fail(self, task_id: str, reason: str):
        t = self.tasks.get(task_id)
        if t and t.state in ACTIVE_STATES:
            self._settle_position()
            t.state, t.reason = NavState.FAILED, reason
            self.status.state, self.status.task_id, self.status.message = NavState.IDLE, None, reason
        return self.status

    def stop_robot(self) -> RobotStatus:
        self.status.state, self.status.message = NavState.EMERGENCY_STOP, "Emergency stop - manual resume required"
        self._emit("EMERGENCY_STOP", task_id=self.current.task_id if self.current else None)
        return self.status

    def resume_navigation(self) -> RobotStatus:
        if self.status.state != NavState.EMERGENCY_STOP:
            return self.status
        t = self.current
        if t and t.state in ACTIVE_STATES:
            t.state = self.status.state = NavState.BLOCKED if self.blocked_edge and not self.reported else NavState.NAVIGATING
        else:
            self.status.state = NavState.IDLE
        self.status.message = None
        self._emit("RESUMED", task_id=t.task_id if t else None)
        return self.status

    def report_arrival(self, task_id) -> RobotStatus:
        t = self.tasks.get(task_id)
        if not t:
            raise NavigationError(f"Unknown task {task_id}")
        if t.state in ACTIVE_STATES:
            self.travelled = self._cum(t.route)[-1] if t.route else 0
            self._arrive(t)
        return self.status
