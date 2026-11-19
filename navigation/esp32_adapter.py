"""ESP32 OjanBot adapter - HTTP polling contract (integration stub; hardware NOT yet verified).

The ESP32 firmware is expected to:
  1. GET  /robot/{robot_id}/next-command   every 0.5-2 s   -> one command or {"command": "NONE"}
  2. POST /robot/status                    on every change -> {"robot_id", "task_id", "event", "marker"}
     events: ACK, ARRIVED (+ "room_scan": "OCCUPIED"|"FREE" when asked to scan), BLOCKED,
             OBSTACLE_CLEARED, PATH_BLOCKED (obstacle still there after the wait; the robot has turned
             round on the line; "marks_passed" = tape marks crossed on this route), ESTOP_PRESSED, ERROR
  3. Stop the motors by itself if it cannot reach the backend for > comm_timeout_s
     (the backend cannot stop a robot it cannot talk to).

Commands sent to the robot:
  NAVIGATE {task_id, patient_id, destination_station, route_id, waypoints, junction_instructions}
  STOP     (emergency stop, always sent before anything else)
  RESUME   (only after an explicit operator action)
  CANCEL   {task_id}
  REROUTE  {task_id, ... same fields as NAVIGATE}  new route from the junction where the robot stopped;
           the first junction instruction may be U_TURN
"""
from __future__ import annotations
import math
import time
from collections import deque

from navigation.navigation_interface import NavigationAdapter, NavigationError
from navigation.navigation_models import NavState, NavTask, RobotStatus, Route, ACTIVE_STATES
from navigation.simulation_adapter import came_from


class ESP32Adapter(NavigationAdapter):
    RADIO_OFF_MSG = "Driving with Wi-Fi off - updates when it stops"

    def __init__(self, robot_id: str, on_event=None, comm_timeout_s: float = 6.0, ack_timeout_s: float = 5.0,
                 nodes: dict | None = None, radio_off_timeout_s: float = 60.0, est_speed_m_s: float = 0.08):
        super().__init__(robot_id, on_event)
        self.marker_node = {n.get("marker"): nid for nid, n in (nodes or {}).items() if n.get("marker")}
        self.nodes = nodes or {}
        self.status = RobotStatus(robot_id=robot_id, kind="physical", adapter="esp32", position="START",
                                  comm_ok=False, message="Waiting for ESP32 to poll")
        self.queue: deque[dict] = deque()
        self.current: NavTask | None = None
        self.comm_timeout_s, self.ack_timeout_s = comm_timeout_s, ack_timeout_s
        # Line sensor on an ADC2 pin: the firmware switches Wi-Fi off while driving (RADIO_OFF) and
        # reconnects when it stops, so a longer silence is expected until the next contact.
        self.radio_off_timeout_s, self.radio_off = radio_off_timeout_s, False
        self.sent_at: dict[str, float] = {}
        self.drive_t0: dict[str, float] = {}       # when the robot took the current route (for the estimated dot)
        self.est_speed_m_s = est_speed_m_s
        self.acked: set[str] = set()

    def _emit(self, kind, **data):
        self.on_event(kind, {"robot_id": self.robot_id, **data})

    # ---------------------------------------------------------------- contract
    def navigate(self, patient_id, destination_station, task_id, route: Route | None = None) -> NavTask:
        if task_id in self.tasks:
            return self.tasks[task_id]
        if self.status.state == NavState.EMERGENCY_STOP:
            raise NavigationError("Robot is in EMERGENCY_STOP; an operator must resume it first")
        if self.current and self.current.state in ACTIVE_STATES:
            raise NavigationError(f"Robot busy with task {self.current.task_id}")
        if route is None or not route.nodes:
            raise NavigationError("Invalid or missing route; robot will not move")
        task = NavTask(task_id, self.robot_id, patient_id, destination_station, route)
        self.tasks[task_id], self.current = task, task
        self.status.state, self.status.task_id = NavState.NAVIGATING, task_id
        self.queue.append(task.command())
        self._emit("NAV_QUEUED", task_id=task_id, destination=destination_station)
        return task

    def get_robot_status(self) -> RobotStatus:
        return self.status

    def cancel_navigation(self, task_id=None) -> RobotStatus:
        t = self.current
        if t and t.state in ACTIVE_STATES and (task_id is None or task_id == t.task_id):
            t.state, t.reason = NavState.FAILED, "cancelled"
            self.queue.appendleft({"command": "CANCEL", "task_id": t.task_id})
            self._emit("CANCELLED", task_id=t.task_id)
        if self.status.state != NavState.EMERGENCY_STOP:
            self.status.state, self.status.task_id = NavState.IDLE, None
        return self.status

    def stop_robot(self) -> RobotStatus:
        self.queue.appendleft({"command": "STOP"})
        self.status.state, self.status.message = NavState.EMERGENCY_STOP, "Emergency stop sent - manual resume required"
        self._emit("EMERGENCY_STOP", task_id=self.current.task_id if self.current else None)
        return self.status

    def resume_navigation(self) -> RobotStatus:
        if self.status.state != NavState.EMERGENCY_STOP:
            return self.status
        self.queue.append({"command": "RESUME"})
        t = self.current
        self.status.state = NavState.NAVIGATING if t and t.state in ACTIVE_STATES else NavState.IDLE
        self.status.message = None
        self._emit("RESUMED", task_id=t.task_id if t else None)
        return self.status

    def reroute(self, task_id: str, route: Route) -> NavTask:
        t = self.tasks.get(task_id)
        if not t:
            raise NavigationError(f"Unknown task {task_id}")
        t.route, t.destination_station, t.state = route, route.destination_station, NavState.NAVIGATING
        self.current, self.status.state, self.status.task_id = t, NavState.NAVIGATING, task_id
        cmd = t.command(); cmd["command"] = "REROUTE"
        self.queue.append(cmd)
        self.acked.discard(task_id); self.sent_at.pop(task_id, None)
        self._emit("REROUTED", task_id=task_id, nodes=route.nodes, room_id=route.room_id)
        return t

    def fail(self, task_id: str, reason: str):
        t = self.tasks.get(task_id)
        if t and t.state in ACTIVE_STATES:
            t.state, t.reason = NavState.FAILED, reason
            self.queue.appendleft({"command": "CANCEL", "task_id": task_id})
            self.status.state, self.status.task_id, self.status.message = NavState.IDLE, None, reason
        return self.status

    def report_arrival(self, task_id, room_scan: str | None = None) -> RobotStatus:
        """Staff confirm the robot is on the destination bar (it did not report, e.g. Wi-Fi or a stall).
        The ESP32 is told to drop the task so it does not drive on by itself."""
        t = self.tasks.get(task_id)
        if t and t.state in ACTIVE_STATES:
            self.queue.appendleft({"command": "CANCEL", "task_id": task_id})
        p = {"robot_id": self.robot_id, "task_id": task_id, "event": "ARRIVED"}
        if room_scan:
            p["room_scan"] = room_scan
        return self.handle_status(p)

    # ---------------------------------------------------------------- HTTP side
    def poll_command(self) -> dict:
        now = time.time()
        self.status.last_seen, self.status.comm_ok = now, True
        self._radio_back()
        if self.status.message == "Waiting for ESP32 to poll":
            self.status.message = None
        if not self.queue:
            return {"command": "NONE"}
        cmd = self.queue.popleft()
        if cmd.get("command") == "NAVIGATE":
            self.sent_at[cmd["task_id"]] = now
        if cmd.get("command") in ("NAVIGATE", "REROUTE"):
            self.drive_t0[cmd["task_id"]] = now
        return cmd

    def position_xy(self) -> dict | None:
        """ESTIMATE of where the robot is on its route, from the time since it took the command and its
        usual speed (it reports nothing while Wi-Fi is off). Never shown as arrived: capped before the end."""
        t = self.current
        if not (t and t.route and t.state == NavState.NAVIGATING and t.task_id in self.drive_t0):
            return None
        pts = [self.nodes.get(n) for n in t.route.nodes]
        if not all(pts) or len(pts) < 2:
            return None
        segs = [math.dist((a["x"], a["y"]), (b["x"], b["y"])) for a, b in zip(pts, pts[1:])]
        d = min((time.time() - self.drive_t0[t.task_id]) * self.est_speed_m_s, 0.92 * sum(segs))
        for a, b, L in zip(pts, pts[1:], segs):
            if d <= L:
                k = d / (L or 1)
                return {"x": a["x"] + k * (b["x"] - a["x"]), "y": a["y"] + k * (b["y"] - a["y"]), "estimated": True}
            d -= L
        return None

    def _radio_back(self):
        if self.radio_off:
            self.radio_off = False
            if self.status.message == self.RADIO_OFF_MSG:
                self.status.message = None

    def handle_status(self, payload: dict) -> RobotStatus:
        self.status.last_seen, self.status.comm_ok = time.time(), True
        ev, tid = payload.get("event"), payload.get("task_id")
        if ev == "RADIO_OFF":
            self.radio_off = True
            if not self.status.message:
                self.status.message = self.RADIO_OFF_MSG
            return self.status
        self._radio_back()
        t = self.tasks.get(tid) if tid else self.current
        if payload.get("marker"):
            self.status.position = self.marker_node.get(payload["marker"], payload["marker"])
        if ev == "ACK" and tid:
            self.acked.add(tid); self._emit("ACK", task_id=tid)
        elif ev == "ARRIVED" and t and t.state in ACTIVE_STATES:
            t.state = NavState.ARRIVED
            node = t.route.nodes[-1] if t.route and t.route.nodes else t.destination_station
            self.status.state, self.status.position, self.status.task_id = NavState.ARRIVED, node, None
            data = dict(task_id=t.task_id, patient_id=t.patient_id, destination=t.destination_station, node=node,
                        came_from=came_from(t.route))
            if t.route and t.route.room_id:
                scan = payload.get("room_scan")
                data.update(room_id=t.route.room_id, room_occupied=(scan == "OCCUPIED") if scan else False,
                            room_scan_reported=scan is not None)
            self._emit("ARRIVED", **data)
        elif ev == "BLOCKED" and t and t.state in ACTIVE_STATES:
            t.state = self.status.state = NavState.BLOCKED
            self.status.message = "Obstacle detected - stopped"
            self._emit("BLOCKED", task_id=t.task_id)
        elif ev == "PATH_BLOCKED" and t and t.state in ACTIVE_STATES and t.route:
            self._path_blocked(t, payload)
        elif ev == "OBSTACLE_CLEARED" and t and t.state == NavState.BLOCKED:
            t.state = self.status.state = NavState.NAVIGATING
            self.status.message = None
            self._emit("OBSTACLE_CLEARED", task_id=t.task_id)
        elif ev == "ESTOP_PRESSED":
            self.status.state, self.status.message = NavState.EMERGENCY_STOP, "Robot e-stop button pressed"
            self._emit("EMERGENCY_STOP", task_id=t.task_id if t else None)
        elif ev == "ERROR":
            if t and t.state in ACTIVE_STATES:
                t.state, t.reason = NavState.FAILED, payload.get("message", "robot error")
            self.status.state, self.status.message = NavState.FAILED, payload.get("message", "robot error")
            self._emit("FAILED", task_id=tid, reason=self.status.message)
        return self.status

    def _path_blocked(self, t: NavTask, payload: dict):
        """Work out which corridor is blocked from how many tape marks the robot crossed."""
        r, nodes = t.route, t.route.nodes
        t.state = self.status.state = NavState.BLOCKED
        if len(nodes) < 2:
            return self._emit("TRAPPED", task_id=t.task_id)
        if payload.get("marks_passed") is not None:
            k = max(0, min(int(payload["marks_passed"]), len(r.marks)))
            if k == 0 and r.mid_segment:
                # blocked again before reaching the first mark (or before even moving): obstacles on both sides
                return self._emit("TRAPPED", task_id=t.task_id)
            a = r.mark_nodes[k - 1] if k > 0 else nodes[0]
        else:                                           # older firmware: last marker it saw
            a = self.marker_node.get(payload.get("marker"), payload.get("marker"))
            a = a if a in nodes else nodes[0]
        i = min(nodes.index(a), len(nodes) - 2)
        a, b = nodes[i], nodes[i + 1]
        turned = payload.get("turned_around") is not False
        on_mark = bool(payload.get("on_mark")) and k == 0 if payload.get("marks_passed") is not None else False
        self.status.position = a
        # turned round mid-corridor -> it will cross the mark at `a` again; turned round on the mark -> it won't
        self._emit("PATH_BLOCKED", task_id=t.task_id, edge=[a, b], at_node=a,
                   heading_from=b if turned else (nodes[i - 1] if i > 0 else r.heading_from),
                   mid_segment=bool(turned) and not on_mark)

    def tick(self, dt_s: float):
        now = time.time()
        t = self.current
        limit = self.radio_off_timeout_s if self.radio_off else self.comm_timeout_s
        if self.status.comm_ok and now - self.status.last_seen > limit:
            self.radio_off = False
            self.status.comm_ok = False
            self.status.message = "Communication lost - robot firmware must stop itself"
            if t and t.state in ACTIVE_STATES:
                t.state, t.reason = NavState.FAILED, "COMM_TIMEOUT"
                self.status.state = NavState.FAILED
            self._emit("COMM_LOST", task_id=t.task_id if t else None)
        if t and t.state in ACTIVE_STATES and t.task_id in self.sent_at and t.task_id not in self.acked \
                and not self.radio_off \
                and now - self.sent_at[t.task_id] > self.ack_timeout_s:
            self.sent_at.pop(t.task_id)
            self.queue.append(t.command())          # resend once more; duplicate task_id is safe on the robot
            self._emit("ACK_TIMEOUT_RESEND", task_id=t.task_id)
