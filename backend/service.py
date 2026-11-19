"""Synapse Linker core service (framework-independent; FastAPI routes in main.py are thin wrappers).

Autonomous operational inference loop (refresh):
  event (check-in, queue change, room/staff change, robot event, clock tick)
    -> build prediction-time features from live state
    -> Model A (waiting time) + Model B (bottleneck risk)            [ML]
    -> eligible services from workflow rules, availability checks    [rules]
    -> look-ahead routing recommendation + explanation               [ML + rules]
    -> dashboard update (server-sent events)
    -> robot navigation task when a destination is confirmed and the patient is eligible
"""
from __future__ import annotations
import datetime
import json
import os
import math
import queue
from collections import deque
import threading
import time
from pathlib import Path

from backend.database import Database
from flow.dependencies import WorkflowRules, eligible_next, remaining_stages
from flow.predictor import Predictor
from flow.routing_engine import (RoutingEngine, StationView, PatientContext, ClinicContext, STATIONS,
                                 expected_service)
from navigation.navigation_interface import NavigationError
from navigation.navigation_models import NavState, ACTIVE_STATES
from navigation.route_planner import RoutePlanner
from navigation.map_loader import edge_key, MapError, load_map
from navigation.simulation_adapter import SimulationAdapter
from navigation.esp32_adapter import ESP32Adapter

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config" / "backend_config.json").read_text(encoding="utf-8"))
# Demo-day switch without editing JSON:  set SYNAPSE_ROBOT_ADAPTER=esp32  -> EDi-01 is the real OjanBot
if os.environ.get("SYNAPSE_ROBOT_ADAPTER") in ("esp32", "simulation"):
    CONFIG["robots"]["EDi-01"]["adapter"] = os.environ["SYNAPSE_ROBOT_ADAPTER"]
POST_CONSULT = {"XRAY", "REVIEW", "CAST_ROOM", "DRESSING", "PROCEDURE", "PHARMACY"}
POST_ORDER = ["XRAY", "REVIEW", "CAST_ROOM", "DRESSING", "PROCEDURE", "PHARMACY"]   # phase order R07-R12
PRE_CONSULT = ["XRAY", "PHLEBOTOMY", "ECG"]
FIXED_STEPS = {"REGISTRATION", "CONSULT", "PAYMENT"}
LABEL_TXT = {"REGISTRATION": "Registration", "CONSULT": "Consultation", "PAYMENT": "Payment"}
ACTIVE_PATIENT = ("AWAITING_GUIDANCE", "GUIDED", "QUEUED", "IN_SERVICE", "ESCALATED")


class ServiceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def hm_to_min(s: str) -> float:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def fmt_min(m: float) -> str:
    m = max(m, 0)
    return f"{int(m // 60):02d}:{int(m % 60):02d}"


class DemoClock:
    """Clinic clock. Models depend on time of day, so the demo can start at a chosen clinic time."""
    def __init__(self, start: str, day_of_week: int, speed: float = 1.0):
        self.set(start, day_of_week, speed)

    SGT = datetime.timezone(datetime.timedelta(hours=8))     # Singapore time, also on a UTC server (Render)

    def set(self, start: str, day_of_week=None, speed: float | None = None):
        # "auto": start from the real Singapore time and weekday (config clinic_start / day_of_week)
        real = datetime.datetime.now(self.SGT)
        self.mode = "real" if start == "auto" else "set"
        if start == "auto":
            start = real.strftime("%H:%M")
        if day_of_week == "auto":
            day_of_week = real.weekday()
        self.base_min, self.real0 = hm_to_min(start), time.time()
        if start == real.strftime("%H:%M"):
            self.base_min += real.second / 60.0
        if day_of_week is not None: self.day_of_week = day_of_week
        if speed is not None: self.speed = speed

    def now_min(self) -> float:
        return self.base_min + (time.time() - self.real0) / 60.0 * self.speed

    def advance(self, minutes: float):
        self.base_min += minutes

    def to_dict(self):
        return {"clinic_time": fmt_min(self.now_min()), "now_min": round(self.now_min(), 2),
                "day_of_week": self.day_of_week, "speed": self.speed, "mode": self.mode}


class Broadcaster:
    def __init__(self):
        self.subs: list[queue.Queue] = []
        self.lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=200)
        with self.lock: self.subs.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subs: self.subs.remove(q)

    def publish(self, msg: dict):
        with self.lock:
            for q in list(self.subs):
                try: q.put_nowait(msg)
                except queue.Full: pass


class SynapseService:
    def __init__(self, db_path=None, predictor: Predictor | None = None, start_loop: bool = False, config=None):
        self.cfg = config or CONFIG
        self.db = Database(db_path)
        self.lock = threading.RLock()
        self.rules = WorkflowRules.load()
        self.predictor = predictor or Predictor()
        self.engine = RoutingEngine(self.predictor, self.rules)
        self.planner = RoutePlanner()
        self.bus = Broadcaster()
        self.clock = DemoClock(self.cfg["clinic_start"], self.cfg["day_of_week"], self.cfg["clock_speed"])
        self.auto_dispatch = self.cfg["auto_dispatch"]
        self.adapters = {}
        self.station_predictions: dict = {}
        self.task_seq = 0
        self._stop = threading.Event()
        self.reset()
        if start_loop:
            threading.Thread(target=self._loop, daemon=True).start()

    # ================================================================== setup
    def reset(self):
        with self.lock:
            self.db.reset()
            self.clock.set(self.cfg["clinic_start"], self.cfg["day_of_week"], self.cfg["clock_speed"])
            now = time.time()
            for st, cfg in STATIONS.items():
                if st == "CONSULT":
                    c = self.cfg["consult"]
                    row = dict(station_id=st, available=1, servers_on_duty=c["doctors_in_session"],
                               background_queue=c["background_queue"], recent_wait_hint=None, arrivals_hint=0,
                               doctor_delay_min=c["doctor_delay_min"])
                else:
                    b = self.cfg["station_background"][st]
                    row = dict(station_id=st, available=1, servers_on_duty=cfg["base_servers"],
                               background_queue=b["background_queue"], recent_wait_hint=b["recent_wait_hint"],
                               arrivals_hint=b["arrivals_hint"], doctor_delay_min=None)
                self.db.upsert("stations", "station_id", {**row, "occupancy_sensor": None, "prediction": None,
                                                          "updated_at": now})
            # simulated physical world (only the simulated robot "sees" it; the backend learns via reports)
            self.world = {"obstacles": set(), "people": set()}
            self.virtual_walks = {}
            self.robot_notes = {}        # robot -> (patient, note shown on its screen), e.g. a takeover      # virtual robot -> the walk it is showing a patient on the floor map
            self.planner.blocked.clear()
            m = self.planner.map
            for room_id, r in m["rooms"].items():
                self.db.upsert("rooms", "room_id", dict(room_id=room_id, station_id=r["station"], label=r["label"],
                                                        classroom_label=r.get("classroom_label"), node=r["node"],
                                                        staffed=1, occupied=0, occupancy_source="start of day",
                                                        updated_at=now))
            self.adapters = {}
            for rid, r in self.cfg["robots"].items():
                if r["adapter"] == "simulation":
                    self.adapters[rid] = SimulationAdapter(
                        rid, m.get("robot_speed_m_s", 0.15), on_event=self._robot_event, kind=r["kind"],
                        obstacle_wait_s=m.get("obstacle_wait_s", 5), nodes=m["nodes"], start_node=m["start_node"],
                        detect_m=m.get("obstacle_detect_m", 0.25),
                        obstacle_on=lambda a, b: edge_key(a, b) in self.world["obstacles"],
                        room_occupied=lambda room: room in self.world["people"])
                elif r["adapter"] == "esp32":
                    self.adapters[rid] = ESP32Adapter(rid, on_event=self._robot_event, nodes=m["nodes"])
                self.db.upsert("robots", "robot_id", dict(robot_id=rid, kind=r["kind"], adapter=r["adapter"],
                                                          patient_id=None, status="IDLE", updated_at=now))
            self.robot_heading = {rid: None for rid in self.cfg["robots"]}   # node each robot faces away from
            self.task_seq, self.queue_seq = 0, 1000
            # task ids must differ after a reset: the robot treats a repeated id as a resend and ignores it
            self.resets = getattr(self, "resets", int(time.time()) % 997) + 1
            self.run_tag = f"{self.resets % 1000:03d}_"
            self.history = {}
            self.station_predictions = {}
            self._event("SYSTEM_RESET", "system", "Demo reset")
            self.refresh("reset")

    def _loop(self):
        last_refresh, last = time.time(), time.time()
        while not self._stop.is_set():
            time.sleep(self.cfg["tick_seconds"])
            now = time.time()
            with self.lock:
                for a in self.adapters.values():
                    a.tick(now - last)
                if now - last_refresh >= self.cfg["refresh_every_s"]:
                    self.refresh("periodic"); last_refresh = now
            last = now

    def stop(self):
        self._stop.set()

    # ================================================================== helpers
    ALERTS = {   # event type -> (severity, title); operational alerts staff must acknowledge
        "ESCALATED": ("critical", "Staff assistance needed"),
        "ROBOT_EMERGENCY_STOP": ("critical", "Robot emergency stop"),
        "ROBOT_COMM_LOST": ("critical", "Robot connection lost"),
        "ROBOT_FAILED": ("critical", "Robot task failed"),
        "CORRIDOR_BLOCKED": ("warning", "Corridor blocked"),
        "NO_FREE_ROOM": ("warning", "No free room"),
        "REROUTE_REQUIRED": ("warning", "Room closed, patient re-routed"),
        "AUTO_DISPATCH_SKIPPED": ("info", "Robot dispatch skipped"),
    }

    def _event(self, type_, source, message="", **kw):
        e = self.db.log_event(type_, source, message, clinic_time=fmt_min(self.clock.now_min()), **kw)
        a = self.ALERTS.get(type_)
        if type_ == "ROOM_SCANNED" and "OCCUPIED" in message:
            a = ("info", "Room occupied on arrival")
        if a:
            self.raise_alert(a[0], type_, a[1], message, **{k: kw.get(k) for k in ("patient_id", "station_id", "robot_id")})
        self.bus.publish({"type": "event", "event": e})
        return e

    def raise_alert(self, severity, type_, title, message, patient_id=None, station_id=None, robot_id=None):
        self.db.execute("INSERT INTO alerts (ts, clinic_time, severity, type, title, message, patient_id, station_id, robot_id)"
                        " VALUES (?,?,?,?,?,?,?,?,?)", (time.time(), fmt_min(self.clock.now_min()), severity, type_, title,
                                                       message, patient_id, station_id, robot_id))

    def ack_alert(self, alert_id: int, by: str) -> dict:
        with self.lock:
            a = self.db.one("SELECT * FROM alerts WHERE id=?", (alert_id,))
            if not a:
                raise ServiceError(f"Unknown alert {alert_id}", 404)
            if not a["acknowledged_by"]:
                self.db.update("alerts", "id", alert_id, acknowledged_by=by, acknowledged_at=fmt_min(self.clock.now_min()))
                self.db.log_event("ALERT_ACKNOWLEDGED", "staff", f"{a['title']} acknowledged by {by}",
                                  clinic_time=fmt_min(self.clock.now_min()))
            self.bus.publish({"type": "state", "reason": "alert_ack"})
            return self.db.one("SELECT * FROM alerts WHERE id=?", (alert_id,))

    def _patient(self, pid) -> dict:
        p = self.db.one("SELECT * FROM patients WHERE patient_id=?", (pid,))
        if not p:
            raise ServiceError(f"Unknown patient {pid}", 404)
        return p

    def _station(self, sid) -> dict:
        s = self.db.one("SELECT * FROM stations WHERE station_id=?", (sid,))
        if not s:
            raise ServiceError(f"Unknown station {sid}", 404)
        return s

    def _stages(self, p):
        return self.rules.build_stages(p["required_steps"], p["visit_type"])

    def eligible(self, p) -> list[str]:
        return eligible_next(self._stages(p), p["completed_steps"])

    # ================================================================== live state -> features
    def station_views(self, now: float) -> tuple[dict, dict]:
        """Returns (views for every station, consult per-doctor queues)."""
        views, doctor_q = {}, {}
        open_entries = self.db.query("SELECT * FROM queue_entries WHERE ended_min IS NULL")
        recent = self.db.query("SELECT * FROM queue_entries WHERE started_min IS NOT NULL AND started_min >= ?",
                               (now - 30,))
        joins = self.db.query("SELECT station_id FROM queue_entries WHERE joined_min >= ?", (now - 15,))
        rooms_by_station = {}
        for r in self.db.query("SELECT * FROM rooms"):
            rooms_by_station.setdefault(r["station_id"], []).append(r)
        for s in self.db.query("SELECT * FROM stations"):
            sid = s["station_id"]
            waiting = [e for e in open_entries if e["station_id"] == sid and e["started_min"] is None]
            serving = [e for e in open_entries if e["station_id"] == sid and e["started_min"] is not None]
            waits = [e["started_min"] - e["joined_min"] for e in recent if e["station_id"] == sid]
            if s["recent_wait_hint"] is not None:
                waits += [s["recent_wait_hint"]] * 3          # station system report counts as 3 observations
            servers = int(s["servers_on_duty"])
            bq = int(s["background_queue"])
            rooms = rooms_by_station.get(sid)
            room_free = True if not rooms else any(r["staffed"] and not r["occupied"] for r in rooms)
            views[sid] = StationView(
                station=sid, available=bool(s["available"]) and room_free, is_open=True,
                queue_length=bq + len(waiting), servers_on_duty=servers,
                servers_busy=min(servers, len(serving) + (servers if bq > 0 else 0)),
                recent_mean_wait_30min=float(sum(waits) / len(waits)) if waits else None,
                arrivals_last_15min=int(s["arrivals_hint"]) + sum(1 for j in joins if j["station_id"] == sid),
                booked_appointments_next_30min=None)
            if sid == "CONSULT":
                for e in waiting:
                    doctor_q.setdefault(e["doctor_id"], []).append(e)
        booked = sum(1 for p in self.db.query("SELECT appointment_min FROM patients WHERE status != 'COMPLETED'")
                     if p["appointment_min"] is not None and now <= p["appointment_min"] < now + 30)
        views["CONSULT"].booked_appointments_next_30min = float(self.cfg["consult"]["booked_next_30min"] + booked)
        return views, doctor_q

    def clinic_context(self, now, views, doctor=None) -> ClinicContext:
        done = self.db.query("SELECT 1 FROM queue_entries WHERE station_id='CONSULT' AND ended_min >= ?", (now - 15,))
        return ClinicContext(now_min=now, day_of_week=self.clock.day_of_week,
                             consult_completions_last_15min=self.cfg["clinic_background"]["consult_completions_last_15min"] + len(done),
                             total_consult_queue=views["CONSULT"].queue_length, doctor=doctor)

    def doctor_view(self, p, views, doctor_q, now) -> tuple[StationView, dict]:
        """Model A sees the patient's own doctor: one server, that doctor's queue."""
        c = self._station("CONSULT")
        n_doc = max(int(c["servers_on_duty"]), 1)
        mine = len(doctor_q.get(p["doctor_id"], []))
        q = int(round(c["background_queue"] / n_doc)) + mine
        v = StationView("CONSULT", available=bool(c["available"]), queue_length=q, servers_on_duty=1,
                        servers_busy=1 if q > 0 else 0, recent_mean_wait_30min=views["CONSULT"].recent_mean_wait_30min,
                        arrivals_last_15min=views["CONSULT"].arrivals_last_15min)
        doc = dict(patients_ahead_for_doctor=q, doctor_present=int(bool(c["available"])),
                   doctor_running_delay_min=float(c["doctor_delay_min"] or 0),
                   minutes_to_appointment=(p["appointment_min"] - now) if p["appointment_min"] is not None else None)
        return v, doc

    def patient_context(self, p, now) -> PatientContext:
        rec = p.get("recommendation") or {}
        return PatientContext(
            patient_id=p["patient_id"], visit_type=p["visit_type"], referral_category=p["referral_category"],
            clinician_priority=p["clinician_priority"], mobility=p["mobility"], age=int(p["age"]),
            required_steps=p["required_steps"], completed_steps=p["completed_steps"],
            minutes_since_clinic_arrival=now - p["checked_in_min"], escalated=bool(p["escalated"]),
            locked_destination=p["assigned_destination"] if p["status"] == "GUIDED" else None,
            previous_recommendation=rec.get("recommended_station"), booked_order=p["required_steps"])

    # ================================================================== autonomous inference loop
    def refresh(self, reason: str = "event") -> dict:
        with self.lock:
            now = self.clock.now_min()
            views, doctor_q = self.station_views(now)
            # --- Model B for every station + Model A for a reference follow-up patient joining now
            ctx = self.clinic_context(now, views)
            bn = self.predictor.predict_bottleneck([self.engine.bn_row(views[s], ctx, ctx.consult_completions_last_15min)
                                                    for s in views])
            ref = PatientContext("REF", "follow_up", "Knee/Hip", "routine", "independent", 60,
                                 ["REGISTRATION", "CONSULT"], ["REGISTRATION"], 20)
            wrows = []
            for s in views:
                v = views[s]
                if s == "CONSULT":
                    n = max(v.servers_on_duty, 1)
                    v = StationView("CONSULT", queue_length=round(v.queue_length / n), servers_on_duty=1,
                                    servers_busy=1, recent_mean_wait_30min=v.recent_mean_wait_30min,
                                    arrivals_last_15min=v.arrivals_last_15min)
                    c = self._station("CONSULT")
                    ctx_s = self.clinic_context(now, views, dict(patients_ahead_for_doctor=v.queue_length, doctor_present=1,
                                                               doctor_running_delay_min=float(c["doctor_delay_min"] or 0),
                                                               minutes_to_appointment=0))
                else:
                    ctx_s = ctx
                wrows.append(self.engine.wait_row(ref, s, v, ctx_s))
            wp = self.predictor.predict_wait(wrows)
            changes = []
            for s, b, w in zip(views, bn, wp):
                pred = dict(predicted_wait_new_arrival_min=w["predicted_wait_min"], expected_range_min=w["expected_range_min"],
                            **b, queue_length=views[s].queue_length, servers_on_duty=views[s].servers_on_duty,
                            servers_busy=views[s].servers_busy, recent_mean_wait_30min=views[s].recent_mean_wait_30min,
                            computed_at=fmt_min(now))
                old = self.station_predictions.get(s, {})
                if old and old.get("alert_level") != b["alert_level"] and b["alert_level"] in ("HIGH", "MEDIUM") \
                        and old.get("alert_level") != "HIGH":
                    self.raise_alert("warning" if b["alert_level"] == "HIGH" else "info", "BOTTLENECK_FORECAST",
                                     f"{s}: bottleneck risk {b['alert_level'].lower()}",
                                     f"Model B forecasts p={b['bottleneck_probability_next_30min']:.2f} that waits exceed "
                                     f"{STATIONS[s]['threshold']:.0f} min in the next 30 min", station_id=s)
                if old and old.get("alert_level") != b["alert_level"]:
                    changes.append(s)
                    self._event("BOTTLENECK_RISK_CHANGED", "ml",
                                f"{s} bottleneck risk (next 30 min) {old.get('alert_level')} -> {b['alert_level']} "
                                f"(p={b['bottleneck_probability_next_30min']:.2f})", station_id=s)
                self.station_predictions[s] = pred
                h = self.history.setdefault(s, deque(maxlen=60))
                h.append(dict(t=fmt_min(now), w=w["predicted_wait_min"], p=b["bottleneck_probability_next_30min"],
                              q=views[s].queue_length))
                self.db.update("stations", "station_id", s, prediction=pred)

            # --- per-patient recommendations
            for p in self.db.query("SELECT * FROM patients WHERE status IN ('AWAITING_GUIDANCE','GUIDED','ESCALATED')"):
                self._recommend_patient(p, now, views, doctor_q, reason)
            self.bus.publish({"type": "state", "reason": reason})
            return {"refreshed_at": fmt_min(now), "bottleneck_changes": changes}

    def _recommend_patient(self, p, now, views, doctor_q, reason, explain=True) -> dict:
        pv = dict(views)
        dv, doc = self.doctor_view(p, views, doctor_q, now)
        pv["CONSULT"] = dv
        ctx = self.clinic_context(now, views, doc)
        rec = self.engine.recommend(self.patient_context(p, now), pv, ctx, explain=explain, bn_stations=views)
        rec["computed_at"] = fmt_min(now)
        old = p.get("recommendation") or {}
        self.db.update("patients", "patient_id", p["patient_id"], recommendation=rec, updated_at=time.time())
        if rec.get("recommended_station") != old.get("recommended_station") or rec["status"] != old.get("status"):
            self._event("RECOMMENDATION_UPDATED", rec.get("decision_source", "rules"),
                        rec.get("explanation", rec["status"]), patient_id=p["patient_id"],
                        station_id=rec.get("recommended_station"), payload={"trigger": reason})
        if (self.auto_dispatch and rec["status"] == "ROUTE" and p["status"] == "AWAITING_GUIDANCE"
                and p["robot_id"] and self.cfg["robots"][p["robot_id"]]["kind"] == "physical"):
            r = self.adapters.get(p["robot_id"])
            if r and r.get_robot_status().state in (NavState.IDLE, NavState.ARRIVED):
                try:
                    self.confirm_destination(p["patient_id"], rec["recommended_station"], source="autonomous")
                except ServiceError as e:
                    self._event("AUTO_DISPATCH_SKIPPED", "system", str(e), patient_id=p["patient_id"])
        return rec

    # ================================================================== journey operations
    def check_in(self, req: dict) -> dict:
        with self.lock:
            pid = req.get("patient_id")
            if not pid:                                   # staff typed only a name: give the next free ID
                n = self.db.one("SELECT COUNT(*) AS n FROM patients")["n"] + 1
                while self.db.one("SELECT 1 FROM patients WHERE patient_id=?", (f"ORTHO_{n:03d}",)):
                    n += 1
                pid = req["patient_id"] = f"ORTHO_{n:03d}"
            if self.db.one("SELECT 1 FROM patients WHERE patient_id=?", (pid,)):
                raise ServiceError(f"Patient {pid} already checked in", 409)
            steps = ["REGISTRATION"] + list(req.get("pre_consult_tests", [])) + ["CONSULT"] + \
                list(req.get("post_consult_steps", [])) + ["PAYMENT"] + (["PHARMACY"] if req.get("pharmacy") else [])
            try:
                self.rules.build_stages(steps, req["visit_type"])
            except ValueError as e:
                raise ServiceError(f"Invalid journey: {e}", 422)
            if req.get("doctor_id") and req["doctor_id"] not in self.cfg["doctors"]:
                raise ServiceError(f"Unknown doctor {req['doctor_id']}", 422)
            now = self.clock.now_min()
            appt = hm_to_min(req["appointment_time"]) if req.get("appointment_time") else now
            self.queue_seq += 1
            self.db.upsert("patients", "patient_id", dict(
                patient_id=pid, queue_no=str(self.queue_seq), display_name=req.get("display_name") or pid,
                visit_type=req["visit_type"],
                referral_category=req["referral_category"], clinician_priority=req.get("clinician_priority", "routine"),
                mobility=req.get("mobility", "independent"), age=int(req["age"]),
                doctor_id=req.get("doctor_id") or self.cfg["doctors"][0], appointment_min=appt, checked_in_min=now,
                required_steps=steps, completed_steps=["REGISTRATION"], status="AWAITING_GUIDANCE",
                current_station="REGISTRATION", assigned_destination=None, assigned_room=None, robot_id=None, escalated=0,
                escalation_reason=None, recommendation=None, updated_at=time.time()))
            self._event("CHECKED_IN", "staff", f"{pid} checked in, queue no. {self.queue_seq} ({req['visit_type']}); "
                        f"required: {' -> '.join(steps)}",
                        patient_id=pid)
            if req.get("robot_id"):
                self.assign_robot(req["robot_id"], pid)
            self.refresh("check_in")
            return self.journey(pid)

    def journey(self, pid: str) -> dict:
        p = self._patient(pid)
        stages = self._stages(p)
        el = eligible_next(stages, p["completed_steps"]) if p["status"] != "COMPLETED" else []
        rem = [s for st in remaining_stages(stages, p["completed_steps"]) for s in st]
        q = self.db.one("SELECT * FROM queue_entries WHERE patient_id=? AND ended_min IS NULL ORDER BY id DESC", (pid,))
        task = self.db.one("SELECT * FROM nav_tasks WHERE patient_id=? ORDER BY created_at DESC", (pid,))
        now = self.clock.now_min()
        history = self.db.query("SELECT step, station_id, joined_min, started_min, ended_min, predicted_wait_min "
                                "FROM queue_entries WHERE patient_id=? ORDER BY id", (pid,))
        for h in history:
            h["actual_wait_min"] = round(h["started_min"] - h["joined_min"], 1) if h["started_min"] is not None else None
            for k in ("joined_min", "started_min", "ended_min"):
                h[k.replace("_min", "")] = fmt_min(h.pop(k)) if h[k] is not None else None
        ahead = None
        if q:
            st = self._station(q["station_id"])
            bq = st["background_queue"]
            if q["station_id"] == "CONSULT":
                bq = round(bq / max(int(st["servers_on_duty"]), 1))
            earlier = self.db.query("SELECT 1 FROM queue_entries WHERE station_id=? AND started_min IS NULL AND ended_min IS NULL "
                                    "AND joined_min < ?" + (" AND doctor_id=?" if q["station_id"] == "CONSULT" else ""),
                                    (q["station_id"], q["joined_min"]) + ((q["doctor_id"],) if q["station_id"] == "CONSULT" else ()))
            ahead = (bq + len(earlier)) if q["started_min"] is None else 0
        room = self.db.one("SELECT room_id, label, classroom_label, node FROM rooms WHERE room_id=?",
                           (p["assigned_room"],)) if p.get("assigned_room") else None
        return dict(patient=p, stages=stages, eligible_now=el, remaining_steps=rem, patients_ahead=ahead, room=room,
                    estimate=self._visit_estimate(p, q, now),
                    queue=dict(station=q["station_id"], step=q["step"], waiting_min=round(now - q["joined_min"], 1),
                               predicted_wait_min=q["predicted_wait_min"], started=q["started_min"] is not None) if q else None,
                    navigation_task=task, history=history, clinic_time=fmt_min(now))

    def _visit_estimate(self, p: dict, q: dict | None, now: float) -> dict | None:
        """How long the rest of the visit is likely to take if the queues stay as they are now: for every step
        still to do, the Model A predicted wait for someone joining now + the usual service time + walking.
        It is an estimate that changes as the clinic changes (see the reasons the robot gives)."""
        if p["status"] == "COMPLETED":
            return None
        from flow.routing_engine import expected_service, expected_travel
        left = list(p["completed_steps"]); pend = []
        for st in p["required_steps"]:
            if st in left: left.remove(st)
            else: pend.append(st)
        cur = p["assigned_destination"] if p["status"] in ("GUIDED", "QUEUED", "IN_SERVICE") else None
        mob, vt = p.get("mobility") or "independent", p["visit_type"]
        steps, total, upper = [], 0.0, 0.0
        for i, st in enumerate(pend):
            pred = self.station_predictions.get("CONSULT" if st == "REVIEW" else st, {})
            wait = float(pred.get("predicted_wait_new_arrival_min") or 0)
            hi = float((pred.get("expected_range_min") or [0, wait])[-1] or wait)
            svc = float(expected_service(st, vt, mob))
            walk = 0.0 if (st == cur and p["status"] != "GUIDED") else float(expected_travel(mob))
            if st == cur and q:                                     # already in that queue / being seen
                if q.get("started_min") is not None:
                    wait, hi, svc = 0.0, 0.0, max(svc - (now - q["started_min"]), 1.0)
                else:
                    gone = now - q["joined_min"]
                    wait = max((q.get("predicted_wait_min") or wait) - gone, 0.0); hi = max(hi - gone, wait)
            steps.append(dict(step=st, wait_min=round(wait), service_min=round(svc)))
            total += wait + svc + walk
            upper += hi + svc + walk
        return dict(remaining_min=round(total), upper_min=round(max(upper, total)), finish_at=fmt_min(now + total),
                    steps=steps, before_consult="CONSULT" not in p["completed_steps"], current=cur)

    def confirm_destination(self, pid: str, step: str, robot_id: str | None = None, source: str = "patient") -> dict:
        """Patient/staff accepts a destination. Re-checks rules and availability; creates a robot task."""
        with self.lock:
            p = self._patient(pid)
            if p["escalated"]:
                raise ServiceError("Patient escalated to staff; automatic guidance suspended", 409)
            if p["status"] not in ("AWAITING_GUIDANCE", "GUIDED"):
                raise ServiceError(f"Patient status {p['status']} cannot be guided now", 409)
            if step not in self.eligible(p):
                raise ServiceError(f"{step} is not eligible yet (prerequisites unmet). Eligible: {self.eligible(p)}", 422)
            st = self.rules.station_of(step)
            if not self._station(st)["available"]:
                raise ServiceError(f"{st} is currently unavailable", 409)
            rid = robot_id or p["robot_id"]
            self.db.update("patients", "patient_id", pid, status="GUIDED", assigned_destination=step, updated_at=time.time())
            self._event("DESTINATION_CONFIRMED", source, f"{pid} -> {step}", patient_id=pid, station_id=st, robot_id=rid)
            task = None
            if rid:
                task = self.create_nav_task(rid, pid, step)
            self.bus.publish({"type": "state", "reason": "destination"})
            return {"journey": self.journey(pid), "navigation_task": task}

    def arrive_at_station(self, pid: str, source: str = "staff") -> dict:
        with self.lock:
            p = self._patient(pid)
            step = p["assigned_destination"]
            if p["status"] != "GUIDED" or not step:
                raise ServiceError("Patient has no confirmed destination", 409)
            st = self.rules.station_of(step)
            now = self.clock.now_min()
            pred = (p.get("recommendation") or {}).get("candidates") or []
            pw = next((c["predicted_wait_min"] for c in pred if c["step"] == step), None)
            self.db.execute("INSERT INTO queue_entries (patient_id, station_id, step, room_id, doctor_id, joined_min, "
                            "predicted_wait_min) VALUES (?,?,?,?,?,?,?)",
                            (pid, st, step, p.get("assigned_room"), p["doctor_id"] if st == "CONSULT" else None, now, pw))
            self.db.update("patients", "patient_id", pid, status="QUEUED", current_station=st, updated_at=time.time())
            self._event("JOINED_QUEUE", source, f"{pid} arrived and joined the {st} queue (not yet served)",
                        patient_id=pid, station_id=st)
            self.refresh("queue_join")
            return self.journey(pid)

    def start_service(self, pid: str) -> dict:
        with self.lock:
            p = self._patient(pid)
            if p["status"] != "QUEUED":
                raise ServiceError("Patient is not queued", 409)
            now = self.clock.now_min()
            q = self.db.one("SELECT * FROM queue_entries WHERE patient_id=? AND ended_min IS NULL ORDER BY id DESC", (pid,))
            self.db.update("queue_entries", "id", q["id"], started_min=now)
            self.db.update("patients", "patient_id", pid, status="IN_SERVICE", updated_at=time.time())
            if p.get("assigned_room"):
                self.db.update("rooms", "room_id", p["assigned_room"], occupied=1, occupancy_source="service started",
                               updated_at=time.time())
            self._event("SERVICE_STARTED", "staff", f"{pid} service started at {q['station_id']} "
                        f"(waited {now - q['joined_min']:.1f} min; predicted {q['predicted_wait_min']})",
                        patient_id=pid, station_id=q["station_id"])
            self.refresh("service_start")
            return self.journey(pid)

    def complete_service(self, pid: str, step: str | None = None, added_steps: list[str] | None = None,
                         recorded_by: str = "staff") -> dict:
        """Service completion is recorded by staff - never inferred from robot arrival."""
        with self.lock:
            p = self._patient(pid)
            step = step or p["assigned_destination"]
            if p["status"] not in ("IN_SERVICE", "QUEUED") or step != p["assigned_destination"]:
                raise ServiceError(f"{pid} is not at {step}", 409)
            req = list(p["required_steps"])
            if added_steps:
                if step not in ("CONSULT", "REVIEW"):
                    raise ServiceError("Only a clinician at CONSULT/REVIEW can add services", 403)
                bad = [s for s in added_steps if s not in POST_CONSULT]
                if bad:
                    raise ServiceError(f"Cannot add {bad}; allowed: {sorted(POST_CONSULT)}", 422)
                clinical = sorted((s for s in added_steps if s != "PHARMACY"), key=POST_ORDER.index)
                pay = req.index("PAYMENT")
                req = req[:pay] + clinical + req[pay:]
                if "PHARMACY" in added_steps and "PHARMACY" not in req:
                    req.append("PHARMACY")
                try:
                    self.rules.build_stages(req, p["visit_type"])
                except ValueError as e:
                    raise ServiceError(f"Clinician order breaks workflow rules: {e}", 422)
            now = self.clock.now_min()
            q = self.db.one("SELECT * FROM queue_entries WHERE patient_id=? AND ended_min IS NULL ORDER BY id DESC", (pid,))
            if q:
                self.db.update("queue_entries", "id", q["id"], started_min=q["started_min"] if q["started_min"] is not None else now,
                               ended_min=now)
            done = p["completed_steps"] + [step]
            p2 = {**p, "required_steps": req, "completed_steps": done}
            nxt = self.eligible(p2)
            status = "COMPLETED" if not nxt else "AWAITING_GUIDANCE"
            if p.get("assigned_room"):
                self.db.update("rooms", "room_id", p["assigned_room"], occupied=0, occupancy_source="service completed",
                               updated_at=time.time())
            self.db.update("patients", "patient_id", pid, required_steps=req, completed_steps=done, status=status,
                           assigned_destination=None, assigned_room=None, updated_at=time.time(),
                           recommendation=None if status == "COMPLETED" else p.get("recommendation"))
            msg = f"{pid} completed {step} (recorded by {recorded_by})"
            if added_steps:
                msg += f"; clinician added {added_steps}"
            self._event("SERVICE_COMPLETED", "staff", msg, patient_id=pid, station_id=self.rules.station_of(step))
            if status == "COMPLETED":
                if p["robot_id"]:
                    self.db.update("robots", "robot_id", p["robot_id"], patient_id=None, updated_at=time.time())
                    self.db.update("patients", "patient_id", pid, robot_id=None)
                self._event("JOURNEY_COMPLETED", "system", f"{pid} journey complete", patient_id=pid)
            self.refresh("service_complete")
            return self.journey(pid)

    def change_orders(self, pid: str, add: list[str] | None = None, remove: list[str] | None = None,
                      recorded_by: str = "Clinician") -> dict:
        """Staff add or cancel steps that are not done yet. Before the consult: pre-consult tests;
        after it: post-consult orders. Registration, consult and payment cannot be removed, nor the
        step the patient is being guided to or served at. The workflow rules still apply."""
        with self.lock:
            p = self._patient(pid)
            if p["status"] == "COMPLETED":
                raise ServiceError(f"{pid} has finished the visit", 409)
            req, done = list(p["required_steps"]), list(p["completed_steps"])
            busy = p["assigned_destination"] if p["status"] in ("GUIDED", "QUEUED", "IN_SERVICE") else None
            consulted = "CONSULT" in done
            for step in remove or []:
                if step in FIXED_STEPS:
                    raise ServiceError(f"{LABEL_TXT.get(step, step)} cannot be removed", 422)
                if req.count(step) <= done.count(step):
                    raise ServiceError(f"{step} is not a pending step", 409)
                if step == busy and req.count(step) - done.count(step) == 1:
                    raise ServiceError(f"{pid} is already going to {step}", 409)
                i = len(req) - 1 - req[::-1].index(step)          # the last (pending) occurrence
                req.pop(i)
                if step == "XRAY" and "REVIEW" in req[i:] and consulted and req.count("XRAY") <= done.count("XRAY"):
                    req.remove("REVIEW")                          # no post-consult X-ray left to review (R07)
            for step in add or []:
                if not consulted:
                    if step not in PRE_CONSULT:
                        raise ServiceError(f"Before the consult only {PRE_CONSULT} can be added", 422)
                    if step in req[:req.index("CONSULT")]:
                        continue
                    req.insert(req.index("CONSULT"), step)
                else:
                    if step not in POST_CONSULT:
                        raise ServiceError(f"After the consult only {POST_ORDER} can be added", 422)
                    pend = req[len(req) - 1 - req[::-1].index("CONSULT") + 1:]
                    if step in pend and req.count(step) > done.count(step):
                        continue
                    if step == "PHARMACY":
                        req.append(step)
                    else:                                         # keep the phase order before PAYMENT
                        pay = req.index("PAYMENT")
                        j = pay
                        while j > 0 and req[j - 1] in POST_ORDER[:-1] and \
                                POST_ORDER.index(req[j - 1]) > POST_ORDER.index(step) and j - 1 >= len(done):
                            j -= 1
                        req.insert(j, step)
            try:
                self.rules.build_stages(req, p["visit_type"])
            except ValueError as e:
                raise ServiceError(f"That change breaks the workflow rules: {e}", 422)
            if req == p["required_steps"]:
                return self.journey(pid)
            upd = dict(required_steps=req, updated_at=time.time())
            if p["status"] == "AWAITING_GUIDANCE":
                upd["recommendation"] = None
            self.db.update("patients", "patient_id", pid, **upd)
            parts = ([f"added {add}"] if add else []) + ([f"removed {remove}"] if remove else [])
            self._event("ORDERS_CHANGED", "staff", f"{pid}: {recorded_by} {' and '.join(parts)}; journey now "
                        f"{' -> '.join(req)}", patient_id=pid)
            self.refresh("orders_changed")
            return self.journey(pid)

    def escalate(self, pid: str, reason: str, active: bool = True) -> dict:
        with self.lock:
            p = self._patient(pid)
            status = "ESCALATED" if active else ("AWAITING_GUIDANCE" if p["status"] == "ESCALATED" else p["status"])
            self.db.update("patients", "patient_id", pid, escalated=int(active), escalation_reason=reason if active else None,
                           status=status, updated_at=time.time())
            if active and p["robot_id"] and p["robot_id"] in self.adapters:
                a = self.adapters[p["robot_id"]]
                if a.current and a.current.patient_id == pid and a.current.state in ACTIVE_STATES:
                    a.cancel_navigation(a.current.task_id)
            self._event("ESCALATED" if active else "ESCALATION_CLEARED", "staff", reason, patient_id=pid)
            self.refresh("escalation")
            return self.journey(pid)

    # ================================================================== stations
    def update_station(self, sid: str, **fields) -> dict:
        with self.lock:
            s = self._station(sid)
            allowed = {"available", "servers_on_duty", "background_queue", "recent_wait_hint", "arrivals_hint",
                       "doctor_delay_min", "occupancy_sensor"}
            upd = {k: v for k, v in fields.items() if k in allowed and v is not None}
            if "available" in upd: upd["available"] = int(bool(upd["available"]))
            for k in ("servers_on_duty", "background_queue", "arrivals_hint"):
                if k in upd and upd[k] < 0:
                    raise ServiceError(f"{k} must be >= 0", 422)
            if not upd:
                raise ServiceError("No valid fields to update", 422)
            upd["updated_at"] = time.time()
            self.db.update("stations", "station_id", sid, **upd)
            desc = ", ".join(f"{k}={v}" for k, v in upd.items() if k != "updated_at")
            self._event("STATION_UPDATED", "staff", f"{sid}: {desc}", station_id=sid)
            if "available" in upd and not upd["available"]:
                # patients heading there are re-routed; nobody is sent to a closed room
                for p in self.db.query("SELECT * FROM patients WHERE status='GUIDED'"):
                    if self.rules.station_of(p["assigned_destination"]) == sid:
                        if p["robot_id"] in self.adapters:
                            a = self.adapters[p["robot_id"]]
                            if a.current and a.current.patient_id == p["patient_id"] and a.current.state in ACTIVE_STATES:
                                a.cancel_navigation(a.current.task_id)
                        self.db.update("patients", "patient_id", p["patient_id"], status="AWAITING_GUIDANCE",
                                       assigned_destination=None)
                        self._event("REROUTE_REQUIRED", "rules", f"{sid} became unavailable; {p['patient_id']} will be re-routed",
                                    patient_id=p["patient_id"], station_id=sid)
            self.refresh("station_update")
            return self._station(sid)

    # ================================================================== robots & navigation
    def assign_robot(self, rid: str, pid: str, force: bool = False) -> dict:
        """Handover: a robot (physical or virtual) takes over a patient's EXISTING journey."""
        with self.lock:
            if rid not in self.cfg["robots"]:
                raise ServiceError(f"Unknown robot {rid}", 404)
            p = self._patient(pid)
            a = self.adapters.get(rid)
            if a and a.current and a.current.state in ACTIVE_STATES and a.current.patient_id != pid:
                if not force:
                    raise ServiceError(f"{rid} is guiding {a.current.patient_id}; use force to reassign", 409)
                a.cancel_navigation(a.current.task_id)
            prev = self.db.one("SELECT patient_id FROM robots WHERE robot_id=?", (rid,))["patient_id"]
            if prev and prev != pid:
                self.db.update("patients", "patient_id", prev, robot_id=None)
            old_robot = p["robot_id"]
            if old_robot and old_robot != rid:
                self.db.update("robots", "robot_id", old_robot, patient_id=None, updated_at=time.time())
            self.db.update("robots", "robot_id", rid, patient_id=pid, updated_at=time.time())
            self.db.update("patients", "patient_id", pid, robot_id=rid, updated_at=time.time())
            done = ", ".join(p["completed_steps"])
            self._event("ROBOT_ASSIGNED", "system",
                        f"{rid} now serving {pid}" + (f" (handover from {old_robot})" if old_robot and old_robot != rid else "")
                        + f"; journey continues - completed: {done}", patient_id=pid, robot_id=rid)
            self.refresh("robot_assigned")
            return self.robot_session(rid)

    def robot_session(self, rid: str) -> dict:
        """What a robot's screen shows: its patient's live journey + next approved service."""
        r = self.db.one("SELECT * FROM robots WHERE robot_id=?", (rid,))
        if not r:
            raise ServiceError(f"Unknown robot {rid}", 404)
        a = self.adapters.get(rid)
        status = a.get_robot_status().to_dict() if a else {"robot_id": rid, "kind": r["kind"], "adapter": "none",
                                                           "state": "IDLE", "message": "Virtual assistant (no motion)"}
        out = dict(robot=status, patient_id=r["patient_id"], clinic_time=fmt_min(self.clock.now_min()))
        note = self.robot_notes.get(rid)
        if note and note[0] == r["patient_id"]:
            out["note"] = note[1]
        if r["patient_id"]:
            j = self.journey(r["patient_id"])
            out["journey"] = j
            p = j["patient"]
            rec = p.get("recommendation") or {}
            dest = p["assigned_destination"] or rec.get("recommended_station")
            out["directions"], out["destination_on_track"] = [], False
            if dest:
                st = self.rules.station_of(dest)
                out["destination_on_track"] = self.planner.on_map(st)
                if out["destination_on_track"]:
                    try:
                        here = self.planner.node_of(p["current_station"])
                        hd = self.robot_heading.get(rid) if a and here == self.planner.node_of(status.get("position")) else None
                        room = p.get("assigned_room") or self._choose_room(st, here, heading_from=hd)
                        if room:
                            out["directions"] = self.planner.plan_to_node(here, self.planner.room_node(room), st,
                                                                          heading_from=hd).junction_instructions
                            out["direction_room"] = self.planner.map["rooms"][room]
                    except (MapError, ServiceError):
                        pass
        return out

    # ------------------------------------------------------------------ rooms on the classroom track
    def _choose_room(self, station: str, from_node: str, exclude: set | None = None,
                     heading_from: str | None = None) -> str | None:
        """Nearest room of the station that is staffed and not known to be occupied, and reachable."""
        exclude = exclude or set()
        best, best_d = None, None
        for r in self.db.query("SELECT * FROM rooms WHERE station_id=? AND staffed=1 AND occupied=0", (station,)):
            if r["room_id"] in exclude:
                continue
            d = self.planner.distance(from_node, r["node"], heading_from)
            if d is not None and (best_d is None or d < best_d):
                best, best_d = r["room_id"], d
        return best

    def create_nav_task(self, rid: str, pid: str, step: str) -> dict | None:
        with self.lock:
            if rid not in self.cfg["robots"]:
                raise ServiceError(f"Unknown robot {rid}", 404)
            st = self.rules.station_of(step)
            if self.cfg["robots"][rid]["kind"] == "virtual" or rid not in self.adapters:
                self._event("DIRECTIONS_SHOWN", "system", f"{rid} is a virtual assistant: showing directions to {step}",
                            patient_id=pid, robot_id=rid)
                return None
            p = self._patient(pid)
            if step != p["assigned_destination"] or p["status"] != "GUIDED":
                raise ServiceError("Navigation requires a confirmed, eligible destination", 409)
            if not self.planner.on_map(st):
                self._event("DIRECTIONS_ONLY", "system", f"{st} is not on the classroom track: {rid} gives directions "
                            "and the patient walks there", patient_id=pid, robot_id=rid, station_id=st)
                return None
            existing = self.db.one("SELECT * FROM nav_tasks WHERE robot_id=? AND patient_id=? AND destination_station=? "
                                   "AND state IN ('NAVIGATING','BLOCKED')", (rid, pid, step))
            if existing:
                return existing                                     # duplicate request -> same task
            a = self.adapters[rid]
            here = self.planner.node_of(a.get_robot_status().position)
            heading = self.robot_heading.get(rid)
            room = self._choose_room(st, here, heading_from=heading)
            if not room:
                raise ServiceError(f"No free, reachable {st} room on the track", 409)
            try:
                route = self.planner.plan_to_node(here, self.planner.room_node(room), st, heading_from=heading)
            except MapError as e:
                raise ServiceError(f"Cannot plan route: {e}", 422)
            route.room_id = room
            self.task_seq += 1
            tid = f"TASK_{self.run_tag}{self.task_seq:03d}"
            try:
                task = a.navigate(pid, st, tid, route)
            except NavigationError as e:
                raise ServiceError(str(e), 409)
            now = time.time()
            self.db.upsert("nav_tasks", "task_id", dict(task_id=tid, robot_id=rid, patient_id=pid, destination_station=step,
                                                         room_id=room, state=task.state.value, command=task.command(),
                                                         reason=None, created_at=now, updated_at=now))
            self.db.update("robots", "robot_id", rid, patient_id=pid, status=task.state.value, updated_at=now)
            self.db.update("patients", "patient_id", pid, assigned_room=room, robot_id=rid)
            label = self.planner.map["rooms"][room]
            self._event("NAV_TASK", "system", f"{tid}: {rid} guiding {pid} to {label['label']} "
                        f"({label.get('classroom_label', room)}) via {' -> '.join(route.nodes)}",
                        patient_id=pid, robot_id=rid, station_id=st)
            return self.db.one("SELECT * FROM nav_tasks WHERE task_id=?", (tid,))

    def _save_route(self, tid: str, a, room: str):
        t = a.tasks[tid]
        self.db.update("nav_tasks", "task_id", tid, state=t.state.value, room_id=room, command=t.command(),
                       updated_at=time.time())

    def _reroute_blocked(self, a, tid: str, edge: list, at_node: str, heading_from: str | None,
                         mid_segment: bool = False):
        """Robot reported a corridor still blocked after waiting: plan around it, else another room,
        else stop and call staff. The robot never steers around obstacles by itself.
        `heading_from`/`mid_segment`: the robot has turned round and is driving back towards `at_node`."""
        t = a.tasks[tid]
        self.planner.block(*edge)
        self._event("CORRIDOR_BLOCKED", "robot", f"Corridor {edge[0]}-{edge[1]} still blocked after "
                    f"{self.planner.map.get('obstacle_wait_s', 5)} s; robot turned round towards {at_node}",
                    robot_id=a.robot_id, patient_id=t.patient_id)
        st, room = t.destination_station, t.route.room_id
        candidates = [room] + [r for r in self.planner.rooms_for(st) if r != room]
        for cand in candidates:
            rr = self.db.one("SELECT * FROM rooms WHERE room_id=?", (cand,))
            if cand != room and (not rr["staffed"] or rr["occupied"]):
                continue
            try:
                route = self.planner.plan_to_node(at_node, self.planner.room_node(cand), st, heading_from=heading_from,
                                                  mid_segment=mid_segment)
            except MapError:
                continue
            route.room_id = cand
            a.reroute(tid, route)
            self.db.update("patients", "patient_id", t.patient_id, assigned_room=cand)
            self._save_route(tid, a, cand)
            first = route.start_action if route.marks else (route.junction_instructions[0]["action"]
                                                            if route.junction_instructions else "")
            self._event("REROUTED", "rules", f"New route to {self.planner.map['rooms'][cand]['label']}: "
                        f"{' -> '.join(route.nodes)} ({route.distance_m} m; first move {first})",
                        robot_id=a.robot_id, patient_id=t.patient_id)
            return
        self._no_path(a, tid, f"Robot cannot reach {st}: every corridor is blocked. Staff please assist.")

    def _no_path(self, a, tid: str, reason: str):
        t = a.tasks[tid]
        a.fail(tid, "NO_PATH")
        self._save_route(tid, a, t.route.room_id if t.route else None)
        self.db.update("patients", "patient_id", t.patient_id, status="AWAITING_GUIDANCE", assigned_destination=None,
                       assigned_room=None)
        self.escalate(t.patient_id, reason)

    def _handle_arrival(self, a, data: dict):
        tid, pid = data["task_id"], data.get("patient_id")
        room = data.get("room_id")
        if room and data.get("room_scan_reported", True):
            occ = bool(data.get("room_occupied"))
            self.db.update("rooms", "room_id", room, occupied=int(occ), occupancy_source=f"{a.robot_id} scan",
                           updated_at=time.time())
            label = self.planner.map["rooms"][room]["label"]
            self._event("ROOM_SCANNED", "robot", f"{a.robot_id} scanned {label}: {'OCCUPIED' if occ else 'free'}",
                        robot_id=a.robot_id, patient_id=pid)
            if occ and pid:
                t = a.tasks[tid]
                st = t.destination_station
                here = data.get("node") or self.planner.room_node(room)
                alt = self._choose_room(st, here, exclude={room}, heading_from=data.get("came_from"))
                self._event("ROOM_AVAILABILITY_CHECK", "rules",
                            f"Checked {st} rooms: " + ", ".join(
                                f"{x['label']} {'occupied' if x['occupied'] else 'free'}"
                                for x in self.db.query("SELECT * FROM rooms WHERE station_id=?", (st,))),
                            patient_id=pid, station_id=st)
                if alt:
                    route = self.planner.plan_to_node(here, self.planner.room_node(alt), st,
                                                      heading_from=data.get("came_from"))
                    route.room_id = alt
                    a.reroute(tid, route)
                    self.db.update("patients", "patient_id", pid, assigned_room=alt)
                    self._save_route(tid, a, alt)
                    self._event("REGUIDED", "rules", f"Re-guiding to {self.planner.map['rooms'][alt]['label']} "
                                f"({' -> '.join(route.nodes)})", robot_id=a.robot_id, patient_id=pid)
                    return
                # no free room of this service: release the destination; the router picks another permitted
                # service (rooms all occupied count as unavailable) or asks the patient to wait
                self.db.update("patients", "patient_id", pid, status="AWAITING_GUIDANCE", assigned_destination=None,
                               assigned_room=None)
                self._event("NO_FREE_ROOM", "rules", f"All {st} rooms occupied: choosing another permitted service "
                            "or asking the patient to wait", patient_id=pid, station_id=st)
                self.refresh("room_occupied")
                return
        if pid:
            p = self._patient(pid)
            if p["status"] == "GUIDED":
                self.arrive_at_station(pid, source="robot")

    def _robot_event(self, kind: str, data: dict):
        """Callback from navigation adapters (simulation or ESP32)."""
        with self.lock:
            rid, tid = data["robot_id"], data.get("task_id")
            a = self.adapters.get(rid)
            state = a.get_robot_status().state.value if a else None
            if tid and self.db.one("SELECT 1 FROM nav_tasks WHERE task_id=?", (tid,)):
                t = a.tasks.get(tid) if a else None
                self.db.update("nav_tasks", "task_id", tid, state=t.state.value if t else state,
                               reason=t.reason if t else None, updated_at=time.time())
            self.db.update("robots", "robot_id", rid, status=state, updated_at=time.time())
            pid = data.get("patient_id") or (a.tasks[tid].patient_id if a and tid in a.tasks else None)
            if kind not in ("REROUTED",):
                extra = f" on {data['edge'][0]}-{data['edge'][1]}" if data.get("edge") else ""
                self._event(f"ROBOT_{kind}", "robot", f"{rid}: {kind}{extra}" + (f" ({tid})" if tid else ""),
                            robot_id=rid, patient_id=pid)
            if kind == "ARRIVED":
                self.robot_heading[rid] = data.get("came_from")
            elif kind in ("REROUTED", "NAV_STARTED", "NAV_QUEUED"):
                pass
            elif kind in ("CANCELLED", "FAILED", "COMM_LOST"):
                self.robot_heading[rid] = None          # unknown: robot may have been moved by hand
            if kind == "PATH_BLOCKED" and a and tid in a.tasks:
                self._reroute_blocked(a, tid, data["edge"], data["at_node"], data.get("heading_from"),
                                      bool(data.get("mid_segment")))
            elif kind == "TRAPPED" and a and tid in a.tasks:
                self._no_path(a, tid, "Robot is boxed in between two obstacles and has stopped. Staff please assist.")
            elif kind == "ARRIVED" and a:
                self._handle_arrival(a, data)
            self.bus.publish({"type": "state", "reason": f"robot_{kind}"})

    # ------------------------------------------------------------------ floor: obstacles, corridors, rooms
    def set_obstacle(self, a: str, b: str, present: bool, exclusive: bool = False) -> dict:
        """SIMULATION ONLY: put/remove a physical obstacle on a tape segment. The planner does not know
        about it until the robot detects it and reports PATH_BLOCKED."""
        with self.lock:
            k = edge_key(a, b)
            if k not in {edge_key(e["from"], e["to"]) for e in self.planner.map["edges"]}:
                raise ServiceError(f"No corridor {a}-{b} on the track", 404)
            if present and exclusive:          # the trolley is moved: its old corridor is clear again
                for o in list(self.world["obstacles"] - {k}):
                    self.world["obstacles"].discard(o)
                    self.planner.unblock(*o)
                    self._event("OBSTACLE_REMOVED", "demo", f"Simulated obstacle moved away from corridor {o[0]}-{o[1]}")
            (self.world["obstacles"].add if present else self.world["obstacles"].discard)(k)
            if not present:
                self.planner.unblock(a, b)
            self._event("OBSTACLE_PLACED" if present else "OBSTACLE_REMOVED", "demo",
                        f"Simulated obstacle {'placed on' if present else 'removed from'} corridor {a}-{b}")
            self.bus.publish({"type": "state", "reason": "obstacle"})
            return self.floor_map()

    def clear_corridor(self, a: str, b: str) -> dict:
        """Staff confirm a corridor is clear again: the planner may use it."""
        with self.lock:
            self.planner.unblock(a, b)
            self.world["obstacles"].discard(edge_key(a, b))
            self._event("CORRIDOR_CLEARED", "staff", f"Corridor {a}-{b} marked clear")
            self.refresh("corridor_cleared")
            return self.floor_map()

    def update_room(self, room_id: str, occupied: bool | None = None, staffed: bool | None = None,
                    person_present: bool | None = None, source: str = "staff") -> dict:
        with self.lock:
            r = self.db.one("SELECT * FROM rooms WHERE room_id=?", (room_id,))
            if not r:
                raise ServiceError(f"Unknown room {room_id}", 404)
            upd = {}
            if occupied is not None:
                upd.update(occupied=int(occupied), occupancy_source=source)
            if staffed is not None:
                upd["staffed"] = int(staffed)
            if upd:
                self.db.update("rooms", "room_id", room_id, updated_at=time.time(), **upd)
                self._event("ROOM_UPDATED", "staff" if source == "staff" else "system",
                            f"{r['label']}: " + ", ".join(f"{k}={v}" for k, v in upd.items() if k != 'occupancy_source')
                            + f" ({source})", station_id=r["station_id"])
            if person_present is not None:      # simulated world: someone walks in/out without telling the system
                (self.world["people"].add if person_present else self.world["people"].discard)(room_id)
                self._event("PERSON_IN_ROOM" if person_present else "PERSON_LEFT_ROOM", "demo",
                            f"Simulated: someone {'is now in' if person_present else 'left'} {r['label']} "
                            "(not reported to the system)")
            self.refresh("room_update")
            return self.floor_map()

    def reload_map(self) -> dict:
        with self.lock:
            busy = [rid for rid, a in self.adapters.items() if a.current and a.current.state in ACTIVE_STATES]
            if busy:
                raise ServiceError(f"Robots still navigating: {busy}", 409)
            try:
                self.planner = RoutePlanner(load_map())
            except MapError as e:
                raise ServiceError(f"Map invalid: {e}", 422)
            self.reset()
            return self.floor_map()

    VIRTUAL_WALK_S = 10.0

    def show_virtual_walk(self, rid: str, pid: str, room: str, from_node: str | None = None):
        """A virtual robot (screen) shows a patient the way: drawn on the floor map as a dashed route and
        a marker that walks it. Nothing on the table moves."""
        with self.lock:
            src = from_node or self.planner.map["start_node"]
            route = self.planner.plan_to_node(src, self.planner.room_node(room), self.planner.map["rooms"][room]["station"])
            self.virtual_walks[rid] = dict(patient_id=pid, room=room, nodes=route.nodes, t0=time.time())
            self._event("DIRECTIONS_SHOWN", "system", f"{rid} (virtual) walks {pid} to "
                        f"{self.planner.map['rooms'][room]['label']} on screen via {' -> '.join(route.nodes)}",
                        patient_id=pid, robot_id=rid)
            self.bus.publish({"type": "state", "reason": "virtual_walk"})

    def _virtual_robots(self) -> list:
        m, out = self.planner.map, []
        for rid, w in list(self.virtual_walks.items()):
            p = self.db.one("SELECT robot_id, status FROM patients WHERE patient_id=?", (w["patient_id"],))
            if not p or p["robot_id"] != rid or p["status"] not in ("GUIDED", "QUEUED", "IN_SERVICE"):
                del self.virtual_walks[rid]          # the patient moved on, or another robot took over
                continue
            pts = [m["nodes"][n] for n in w["nodes"]]
            segs = [math.hypot(b["x"] - a["x"], b["y"] - a["y"]) for a, b in zip(pts, pts[1:])]
            total = sum(segs) or 1.0
            f = min((time.time() - w["t0"]) / self.VIRTUAL_WALK_S, 1.0)
            d, pos = f * total, dict(x=pts[-1]["x"], y=pts[-1]["y"])
            for a, b, L in zip(pts, pts[1:], segs):
                if d <= L:
                    k = d / (L or 1)
                    pos = dict(x=a["x"] + k * (b["x"] - a["x"]), y=a["y"] + k * (b["y"] - a["y"]))
                    break
                d -= L
            out.append(dict(robot_id=rid, virtual=True, state="NAVIGATING" if f < 1 else "ARRIVED", position=pos,
                            message=None, route=w["nodes"], patient_id=w["patient_id"]))
        return out

    def floor_map(self) -> dict:
        m = self.planner.map
        rooms = {r["room_id"]: r for r in self.db.query("SELECT * FROM rooms")}
        robots = []
        for rid, a in self.adapters.items():
            st = a.get_robot_status()
            pos = a.position_xy() if hasattr(a, "position_xy") else None
            if pos is None:
                n = m["nodes"].get(self.planner.node_of(st.position))
                pos = {"x": n["x"], "y": n["y"]} if n else None
            t = a.current
            robots.append(dict(robot_id=rid, state=st.state.value, position=pos, message=st.message,
                               route=t.route.nodes if t and t.route and t.state in ACTIVE_STATES else [],
                               patient_id=t.patient_id if t else None))
        robots += self._virtual_robots()
        return dict(
            path=Path(m["_path"]).name, example=bool(m.get("_WARNING")), units=m["units"], frame=m["frame"],
            track_type=m.get("track_type", "graph"), table=m.get("table"), track=m.get("track"),
            node_kind=m.get("node_kind"), start_node=m["start_node"],
            demo_obstacle=m.get("demo_obstacle"),
            nodes=[dict(id=k, **v) for k, v in m["nodes"].items()],
            edges=[dict(**{"from": e["from"], "to": e["to"]}, via=e.get("via", []),
                        known_blocked=edge_key(e["from"], e["to"]) in self.planner.blocked,
                        obstacle=edge_key(e["from"], e["to"]) in self.world["obstacles"]) for e in m["edges"]],
            rooms=[dict(room_id=k, node=v["node"], station=v["station"], label=v["label"], zone=v.get("zone"),
                        classroom_label=v.get("classroom_label"), staffed=bool(rooms[k]["staffed"]),
                        occupied=bool(rooms[k]["occupied"]), source=rooms[k]["occupancy_source"],
                        person_present=k in self.world["people"]) for k, v in m["rooms"].items() if k in rooms],
            robots=robots, simulated_world=any(a.__class__.__name__ == "SimulationAdapter" for a in self.adapters.values()),
            patients=[dict(patient_id=p["patient_id"], name=p["display_name"], queue_no=p.get("queue_no"),
                           status=p["status"], step=p["assigned_destination"], room_id=p["assigned_room"],
                           robot_id=p["robot_id"],
                           robot_kind=(self.cfg["robots"].get(p["robot_id"]) or {}).get("kind") if p["robot_id"] else None)
                      for p in (self._patient(r["patient_id"]) for r in
                                self.db.query("SELECT patient_id FROM patients WHERE status != 'COMPLETED'"))])

    def robot_command(self, rid: str, action: str, task_id: str | None = None) -> dict:
        with self.lock:
            a = self.adapters.get(rid)
            if not a:
                raise ServiceError(f"{rid} has no navigation adapter (virtual robot)", 409)
            if action == "stop":
                a.stop_robot()
            elif action == "resume":
                a.resume_navigation()
            elif action == "cancel":
                a.cancel_navigation(task_id)
            elif action in ("obstacle", "clear_obstacle") and isinstance(a, SimulationAdapter):
                a.simulate_obstacle(action == "obstacle")
            elif action == "report_arrival":
                if not task_id:
                    raise ServiceError("task_id required", 422)
                a.report_arrival(task_id)
            else:
                raise ServiceError(f"Unknown action {action}", 422)
            st = a.get_robot_status()
            self.db.update("robots", "robot_id", rid, status=st.state.value, updated_at=time.time())
            if st.task_id and self.db.one("SELECT 1 FROM nav_tasks WHERE task_id=?", (st.task_id,)):
                self.db.update("nav_tasks", "task_id", st.task_id, state=a.tasks[st.task_id].state.value,
                               updated_at=time.time())
            self.bus.publish({"type": "state", "reason": f"robot_{action}"})
            return st.to_dict()

    def esp32_poll(self, rid: str) -> dict:
        a = self.adapters.get(rid)
        if not isinstance(a, ESP32Adapter):
            raise ServiceError(f"{rid} is not configured for the ESP32 adapter", 409)
        with self.lock:
            return a.poll_command()

    def esp32_status(self, payload: dict) -> dict:
        a = self.adapters.get(payload["robot_id"])
        if not isinstance(a, ESP32Adapter):
            raise ServiceError(f"{payload['robot_id']} is not configured for the ESP32 adapter", 409)
        with self.lock:
            return a.handle_status(payload).to_dict()

    # ================================================================== prediction API
    def predict_waiting_time(self, patient_id: str | None = None, station: str | None = None,
                             features: dict | None = None) -> dict:
        with self.lock:
            now = self.clock.now_min()
            if features is None:
                if not (patient_id and station):
                    raise ServiceError("Provide either features or patient_id + station", 422)
                p = self._patient(patient_id)
                views, dq = self.station_views(now)
                st = self.rules.station_of(station)
                if st == "CONSULT":
                    v, doc = self.doctor_view(p, views, dq, now)
                else:
                    v, doc = views[st], None
                features = self.engine.wait_row(self.patient_context(p, now), station, v,
                                                self.clinic_context(now, views, doc))
            missing = [k for k in self.predictor.wait["features"] if features.get(k) is None]
            out = self.predictor.predict_wait([features])[0]
            return dict(**out, station=features.get("station"), model_version=self.predictor.versions["waiting_time"],
                        prediction_timestamp=fmt_min(now), missing_optional_inputs=missing,
                        limitations="Trained on synthetic orthopaedic clinic data; not clinically validated. "
                                    "Expected range is the model's 10th-90th percentile (conservative).")

    def predict_bottleneck_risk(self, station: str | None = None, features: dict | None = None) -> dict:
        with self.lock:
            now = self.clock.now_min()
            if features is None:
                if not station:
                    raise ServiceError("Provide station or features", 422)
                views, _ = self.station_views(now)
                if station not in views:
                    raise ServiceError(f"Unknown station {station}", 404)
                ctx = self.clinic_context(now, views)
                features = self.engine.bn_row(views[station], ctx, ctx.consult_completions_last_15min)
            out = self.predictor.predict_bottleneck([features])[0]
            return dict(**out, station=features.get("station"), model_version=self.predictor.versions["bottleneck"],
                        prediction_timestamp=fmt_min(now), horizon="next 30 minutes",
                        note="Forecast of the next 30 minutes, not an observed current bottleneck. "
                             "CONSULT forecasts are weak (recall ~0.26) and advisory only.")

    def recommend(self, pid: str) -> dict:
        with self.lock:
            p = self._patient(pid)
            now = self.clock.now_min()
            views, dq = self.station_views(now)
            if p["status"] not in ("AWAITING_GUIDANCE", "GUIDED", "ESCALATED"):
                return {"status": p["status"], "recommended_station": None,
                        "explanation": f"Patient is {p['status']}; no routing needed now."}
            rec = self._recommend_patient(p, now, views, dq, "api")
            self.bus.publish({"type": "state", "reason": "recommend"})
            return rec

    # ================================================================== dashboard
    def dashboard_state(self) -> dict:
        with self.lock:
            now = self.clock.now_min()
            views, _ = self.station_views(now)
            stations = []
            for s in self.db.query("SELECT * FROM stations"):
                v = views[s["station_id"]]
                stations.append(dict(station_id=s["station_id"], available=bool(s["available"]),
                                     room_free=v.available or not bool(s["available"]),
                                     threshold_min=STATIONS[s["station_id"]]["threshold"],
                                     servers_on_duty=s["servers_on_duty"], servers_busy=v.servers_busy,
                                     queue_length=v.queue_length, background_queue=s["background_queue"],
                                     recent_wait_hint=s["recent_wait_hint"], arrivals_hint=s["arrivals_hint"],
                                     doctor_delay_min=s["doctor_delay_min"], recent_mean_wait_30min=v.recent_mean_wait_30min,
                                     prediction=s["prediction"]))
            order = list(STATIONS)
            stations.sort(key=lambda x: order.index(x["station_id"]))
            patients = []
            for p in self.db.query("SELECT * FROM patients ORDER BY checked_in_min"):
                j = self.journey(p["patient_id"])
                patients.append(dict(**{k: p[k] for k in ("patient_id", "queue_no", "assigned_room", "display_name", "visit_type", "referral_category",
                                                          "clinician_priority", "mobility", "age", "doctor_id", "status",
                                                          "current_station", "assigned_destination", "robot_id",
                                                          "escalated", "escalation_reason", "required_steps",
                                                          "completed_steps", "recommendation", "updated_at")},
                                     eligible_now=j["eligible_now"], queue=j["queue"], patients_ahead=j["patients_ahead"],
                                     room=j["room"],
                                     minutes_in_clinic=round(now - p["checked_in_min"], 1)))
            robots = []
            for r in self.db.query("SELECT * FROM robots"):
                a = self.adapters.get(r["robot_id"])
                st = a.get_robot_status().to_dict() if a else {"state": "IDLE", "message": "Virtual assistant"}
                cur = a.current.to_dict() if a and a.current else None
                robots.append(dict(robot_id=r["robot_id"], kind=r["kind"], adapter=r["adapter"], patient_id=r["patient_id"],
                                   status=st, current_task=cur))
            tasks = self.db.query("SELECT * FROM nav_tasks ORDER BY created_at DESC LIMIT 20")
            events = self.db.query("SELECT id, clinic_time, type, source, patient_id, station_id, robot_id, message "
                                   "FROM events ORDER BY id DESC LIMIT 60")
            alerts = self.db.query("SELECT * FROM alerts ORDER BY (acknowledged_by IS NOT NULL), id DESC LIMIT 40")
            for st in stations:
                st["history"] = list(self.history.get(st["station_id"], []))
            return dict(clock=self.clock.to_dict(), auto_dispatch=self.auto_dispatch, model_versions=self.predictor.versions,
                        stations=stations, patients=patients, robots=robots, nav_tasks=tasks, events=events, alerts=alerts,
                        map=dict(path=Path(self.planner.map["_path"]).name, warning=self.planner.map.get("_WARNING")))

    def set_clock(self, clinic_time: str | None = None, speed: float | None = None, advance_min: float | None = None,
                  day_of_week: int | None = None):
        with self.lock:
            if clinic_time:
                self.clock.set(clinic_time, day_of_week, speed)
            elif speed is not None or day_of_week is not None:
                cur = fmt_min(self.clock.now_min())
                self.clock.set(cur, day_of_week, speed)
            if advance_min:
                self.clock.advance(advance_min)
            if clinic_time or advance_min or (speed is not None and speed != 1):
                self.clock.mode = "set"
            self._event("CLOCK_SET", "demo", f"Clinic clock {fmt_min(self.clock.now_min())} x{self.clock.speed}")
            self.refresh("clock")
            return self.clock.to_dict()
