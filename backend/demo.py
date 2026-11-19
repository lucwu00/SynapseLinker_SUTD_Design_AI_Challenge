"""Seven reproducible demo scenes (fictional patients). Each returns a narration for the presenter
that says which parts are ML, which are rules and which are simulated."""
from __future__ import annotations
import json
from pathlib import Path

from backend.service import SynapseService, ServiceError

ROOT = Path(__file__).resolve().parents[1]

PATIENTS = {
    "ORTHO_001": dict(patient_id="ORTHO_001", display_name="Mr Tan (fictional)", visit_type="new",
                      referral_category="Fracture", age=58, mobility="walking_aid", doctor_id="DR01",
                      appointment_time=None),
    "ORTHO_002": dict(patient_id="ORTHO_002", display_name="Mdm Lim (fictional)", visit_type="pre_op",
                      referral_category="Knee/Hip", age=67, doctor_id="DR02", pre_consult_tests=["XRAY", "PHLEBOTOMY", "ECG"]),
    "ORTHO_003": dict(patient_id="ORTHO_003", display_name="Ms Kumar (fictional)", visit_type="follow_up",
                      referral_category="Post-op", age=49, doctor_id="DR02", pre_consult_tests=["XRAY"]),
}


def _exists(svc, pid):
    return svc.db.one("SELECT 1 FROM patients WHERE patient_id=?", (pid,)) is not None


def _rec(svc, pid):
    return (svc._patient(pid).get("recommendation") or {})


def scene(svc: SynapseService, n: int) -> dict:
    return {1: s1, 2: s2, 3: s3, 4: s4, 5: s5, 6: s6, 7: s7, 8: s8, 9: s9}[n](svc)


def s1(svc):
    if not _exists(svc, "ORTHO_001"):
        svc.check_in({**PATIENTS["ORTHO_001"], "appointment_time": svc.clock.to_dict()["clinic_time"], "robot_id": "EDi-01"})
    p = svc._patient("ORTHO_001"); r = p["recommendation"] or {}
    return dict(scene=1, title="Normal operations",
                narration=[f"ORTHO_001 (new fracture referral, walking aid) checks in with EDi-01.",
                           f"RULES: next eligible service = {svc.eligible(p)}.",
                           f"ML: predicted consult wait {r.get('candidates', [{}])[0].get('predicted_wait_min')} min "
                           f"(Model A, {svc.predictor.versions['waiting_time']}).",
                           f"AUTONOMOUS: destination confirmed and navigation task created for EDi-01 (status {p['status']})."])


def s2(svc):
    if not _exists(svc, "ORTHO_002"):
        # SIMULATED starting point: the single ECG machine is free right now. Scene 3 then sends it a burst of
        # ward/ED requests. (Background values are scenario inputs, not predictions.)
        svc.update_station("ECG", background_queue=0, recent_wait_hint=3.0, arrivals_hint=0)
        svc.check_in({**PATIENTS["ORTHO_002"], "appointment_time": svc.clock.to_dict()["clinic_time"]})
    r = _rec(svc, "ORTHO_002")
    lines = [f"{c['step']}: wait {c['predicted_wait_min']} min, risk {c['alert_level']}, "
             f"group finished in ~{c['est_stage_completion_min']} min" for c in r.get("candidates", [])]
    return dict(scene=2, title="Multi-stage optimisation",
                narration=["ORTHO_002 is pre-op: X-ray, blood test and ECG may be done in any order (rule R05).",
                           "SIMULATED: the ECG machine is free at the moment."] + lines +
                          [f"RECOMMENDED ORDER: {' -> '.join(r.get('recommended_sequence', []))}",
                           "ML predicts waits and risk; the look-ahead compares all 6 permitted orders."])


TEST_NAME = {"XRAY": "X-ray", "PHLEBOTOMY": "blood-test", "ECG": "ECG"}
# Simulated congestion levels, mildest first: (waiting, recent mean wait min, arrivals last 15 min).
CONGESTION = [(6, 35.0, 6), (10, 50.0, 10), (16, 70.0, 14), (24, 90.0, 20), (4, 60.0, 12), (8, 120.0, 16)]
_FIELDS = ("background_queue", "recent_wait_hint", "arrivals_hint")


def _what_if(svc, pid, station, q, w, arr):
    """Recommended order for `pid` if `station` were congested - computed without changing anything."""
    with svc.lock:
        row = svc._station(station)
        svc.db.update("stations", "station_id", station, background_queue=q, recent_wait_hint=w, arrivals_hint=arr)
        try:
            now = svc.clock.now_min()
            views, doctor_q = svc.station_views(now)
            p = svc._patient(pid)
            pv = dict(views)
            dv, doc = svc.doctor_view(p, views, doctor_q, now)
            pv["CONSULT"] = dv
            rec = svc.engine.recommend(svc.patient_context(p, now), pv, svc.clinic_context(now, views, doc),
                                       explain=False, bn_stations=views)
            return rec.get("recommended_sequence", [])
        finally:
            svc.db.update("stations", "station_id", station, **{k: row[k] for k in _FIELDS})


def s3(svc):
    # Which test the look-ahead puts first depends on the real time of day. The scene congests the
    # earliest test on ORTHO_002's plan for which the trained models predict that waiting it out is
    # quicker (the learned profile expects that queue to ease), using the mildest congestion that does.
    if not _exists(svc, "ORTHO_002"): s2(svc)
    before = _rec(svc, "ORTHO_002").get("recommended_sequence", [])
    choice = None
    targets = sorted(before, key=lambda x: x != "ECG")                # the ECG story first, then any other test
    ok = [lambda tg, a: a.index(tg) > before.index(tg),               # 1st choice: the congested test moves later
          lambda tg, a: a != before and a.index(tg) >= before.index(tg)]  # else: it reshuffles the others
    for accept in ok:
        for target in targets:
            for lv in CONGESTION:
                after = _what_if(svc, "ORTHO_002", target, *lv)
                if after and target in after and accept(target, after):
                    choice = (target, lv)
                    break
            if choice: break
        if choice: break
    target, (q, w, arr) = choice or ("ECG", CONGESTION[1])
    svc.update_station(target, background_queue=q, recent_wait_hint=w, arrivals_hint=arr)
    pr = svc.station_predictions[target]
    r = _rec(svc, "ORTHO_002")
    after = r.get("recommended_sequence", [])
    tc = next((c for c in r.get("candidates", []) if c["step"] == target), {})
    best = min((c["est_stage_completion_min"] for c in r.get("candidates", [])), default=0)
    name = TEST_NAME.get(target, target)
    moved = target in before and target in after and after.index(target) > before.index(target)
    return dict(scene=3, title="Bottleneck prediction",
                narration=[f"SIMULATED: the {name} room receives urgent ward and ED requests ({q} waiting, recent wait {w:.0f} min).",
                           f"ML: {name} bottleneck probability (next 30 min) = {pr['bottleneck_probability_next_30min']:.2f} "
                           f"-> alert {pr['alert_level']}; predicted wait for a new arrival {pr['predicted_wait_new_arrival_min']} min.",
                           f"AUTONOMOUS: ORTHO_002 order {' -> '.join(before)}  ==>  {' -> '.join(after)}",
                           (f"{name} moves later: the learned profile expects its queue to ease, so going there now would take "
                            f"~{max(tc.get('est_stage_completion_min', 0) - best, 0):.0f} min longer.") if moved else
                           (f"The learned profile expects the {name} queue to stay long, so waiting it out does not help; "
                            f"the other tests are reordered around it."
                            if after != before else
                            f"Order kept: at this time of day the models expect the {name} queue to stay long, so moving it does not save time.")])


def s4(svc):
    if not _exists(svc, "ORTHO_002"): s2(svc)
    svc.update_station("PHLEBOTOMY", available=False)
    r = _rec(svc, "ORTHO_002")
    return dict(scene=4, title="Unexpected disruption",
                narration=["SIMULATED: the blood-test room becomes unavailable (staff/sensor update).",
                           f"RULES: excluded -> {[c for c in r.get('rule_checks', []) if 'unavailable' in c]}",
                           f"New recommendation for ORTHO_002: {r.get('recommended_station')} "
                           f"(order {' -> '.join(r.get('recommended_sequence', []))})."])


def s5(svc):
    if not _exists(svc, "ORTHO_001"): s1(svc)
    p = svc._patient("ORTHO_001")
    if p["status"] == "AWAITING_GUIDANCE":
        svc.confirm_destination("ORTHO_001", p["recommendation"]["recommended_station"], robot_id="EDi-01")
    a = svc.adapters["EDi-01"]
    task = a.current.command() if a.current else None
    with svc.lock:
        for _ in range(120):                          # fast-forward the simulated drive (~0.5 s steps)
            if a.get_robot_status().state.value != "NAVIGATING": break
            a.tick(0.5)
    p = svc._patient("ORTHO_001")
    return dict(scene=5, title="Robot navigation",
                narration=["Backend issued this command (same contract the ESP32 will receive):", json.dumps(task),
                           f"SIMULATION ADAPTER: robot state {a.get_robot_status().state.value}.",
                           f"Patient status {p['status']} at {p['current_station']} - arrival is NOT service completion.",
                           "Physical OjanBot execution requires the ESP32 adapter + real map (not claimed yet)."])


def s6(svc):
    if not _exists(svc, "ORTHO_001") or svc._patient("ORTHO_001")["status"] in ("AWAITING_GUIDANCE", "GUIDED"):
        s5(svc)
    p = svc._patient("ORTHO_001")
    if p["status"] == "QUEUED":
        svc.start_service("ORTHO_001")
    if svc._patient("ORTHO_001")["status"] == "IN_SERVICE":
        svc.complete_service("ORTHO_001", "CONSULT", added_steps=["XRAY", "REVIEW", "CAST_ROOM", "PHARMACY"],
                             recorded_by="DR01 (simulated clinician)")
    if not _exists(svc, "ORTHO_003"):
        svc.check_in({**PATIENTS["ORTHO_003"], "appointment_time": svc.clock.to_dict()["clinic_time"]})
    svc.assign_robot("EDi-01", "ORTHO_003", force=True)
    sess = svc.assign_robot("EDi-02", "ORTHO_001")
    rec = sess["journey"]["patient"].get("recommendation") or {}
    c = next((x for x in rec.get("candidates", []) if x["step"] == rec.get("recommended_station")), {})
    return dict(scene=6, title="Robot handover",
                narration=["ORTHO_001 finished the consult; the clinician (simulated) ordered X-ray -> review -> cast + pharmacy.",
                           "EDi-01 is reassigned to ORTHO_003.",
                           f"EDi-02 retrieves ORTHO_001's SAME journey: completed {sess['journey']['patient']['completed_steps']}.",
                           f"Next approved service: {rec.get('recommended_station')}, estimated wait {c.get('predicted_wait_min')} min."])


def s7(svc):
    path = ROOT / "reports" / "replay_results.json"
    if not path.exists():
        raise ServiceError("Run `python -m flow.evaluate_replay` first", 404)
    r = json.loads(path.read_text(encoding="utf-8"))
    eff = {(e["policy"], e["group"]): e for e in r["effects"]}
    f, a = eff[("synapse", "order-flexible visits")], eff[("synapse", "all visits")]
    return dict(scene=7, title="Before/after evaluation", results=r,
                narration=[f"Held-out replay {r['period']}: {r['visits']:,} synthetic visits, identical arrivals and durations.",
                           f"Order-flexible patients: {f['diff_min']} min ({f['pct_reduction']}% faster), 95% CI {f['ci95']}.",
                           f"All visits: {a['pct_reduction']}% (CI {a['ci95']} - no clinic-wide change).",
                           "Measured by simulation on synthetic data - not observed in a real hospital."])


XRAY_PATIENT = dict(patient_id="ORTHO_201", display_name="Mdm Wong (fictional)", visit_type="follow_up",
                    referral_category="Fracture", age=71, mobility="walking_aid", doctor_id="DR01",
                    pre_consult_tests=["XRAY"])


def _xray_rooms_by_distance(svc):
    """X-ray rooms ordered by how far EDi-01 must drive from where it stands now."""
    a = svc.adapters.get("EDi-01")
    here = svc.planner.node_of(a.get_robot_status().position if a else None)
    hd = svc.robot_heading.get("EDi-01")
    rooms = [(svc.planner.distance(here, svc.planner.room_node(r), hd), r) for r in svc.planner.rooms_for("XRAY")]
    return [r for d, r in sorted(x for x in rooms if x[0] is not None)]


def _physical(svc) -> bool:
    return svc.adapters.get("EDi-01").__class__.__name__ == "ESP32Adapter"


def _cm(p):
    return f"({p['x'] * 100:.0f}, {p['y'] * 100:.0f}) cm"


def _label(svc, room):
    r = svc.planner.map["rooms"][room]
    return f"{r['label']} ({r.get('classroom_label', room)})"


def s8(svc):
    """Starts from a clean clinic. Runs live: watch the floor map."""
    svc.reset()
    rooms = _xray_rooms_by_distance(svc)
    first, other = rooms[0], (rooms[1] if len(rooms) > 1 else None)
    svc.update_room(first, person_present=True)             # someone walked in; nobody updated the system
    svc.check_in({**XRAY_PATIENT, "appointment_time": svc.clock.to_dict()["clinic_time"], "robot_id": "EDi-01"})
    p = svc._patient("ORTHO_201")
    zone = svc.planner.map["rooms"][first].get("zone")
    setup = (f"ON THE TABLE: put the robot on START facing right, and a person block at {_cm(zone)}. "
             if _physical(svc) and zone else "")
    return dict(scene=8, title="Room occupied, re-guide",
                narration=[setup + f"Clean start. A person is sitting in {_label(svc, first)}, but the system still thinks it is free.",
                           f"Queue no. {p['queue_no']} (follow-up fracture, walking aid) needs an X-ray before seeing the doctor.",
                           f"EDi-01 guides the patient to the nearest free X-ray room: {_label(svc, p['assigned_room'])}.",
                           "WATCH THE FLOOR MAP: on arrival EDi-01 turns to scan the room, finds it occupied, checks room "
                           f"availability and re-guides the patient to {_label(svc, other) if other else 'another room'}.",
                           "If that room is also occupied, the system picks another permitted service or asks the patient to wait."])


def s9(svc):
    """Starts from a clean clinic. Runs live: watch the floor map.
    The map's "demo.scene9" says which room the robot heads for and which corridor the trolley blocks;
    without it, the robot goes to the X-ray room furthest away and the corridor before it is blocked."""
    svc.reset()
    cfg = svc.planner.map.get("demo", {}).get("scene9")
    rooms = _xray_rooms_by_distance(svc)
    if cfg:
        target, closed_rooms = cfg["room"], [r for r in cfg.get("close_rooms", [])]
    else:
        target, closed_rooms = rooms[-1], rooms[:-1]
    for r in closed_rooms:
        svc.update_room(r, staffed=False)
    a = svc.adapters["EDi-01"]
    if cfg:
        edge = tuple(cfg["edge"])
    else:
        here = svc.planner.node_of(a.get_robot_status().position)
        n = svc.planner.plan_to_node(here, svc.planner.room_node(target), "XRAY", svc.robot_heading.get("EDi-01")).nodes
        edge = (n[-3], n[-2]) if len(n) >= 4 else (n[0], n[1])
    svc.set_obstacle(*edge, True)
    svc.check_in({**XRAY_PATIENT, "appointment_time": svc.clock.to_dict()["clinic_time"], "robot_id": "EDi-01"})
    t = a.current
    wait = svc.planner.map.get("obstacle_wait_s", 5)
    closed = ", ".join(_label(svc, r) for r in closed_rooms) or "none"
    box = (cfg or {}).get("box")
    where = (f"ON THE TABLE: robot on START facing right, trolley box at {_cm(box)}. " if _physical(svc) and box else
             f"A trolley (simulated) is blocking the corridor {edge[0]}-{edge[1]}. ")
    return dict(scene=9, title="Corridor blocked, reroute",
                narration=[f"Clean start. Closed rooms: {closed}. " + where + "The system does not know yet.",
                           f"EDi-01 sets off for {_label(svc, target)} on the shortest route "
                           f"{' -> '.join(t.route.nodes) if t else ''}.",
                           f"WATCH: it detects the obstacle and stops. If it is still there after {wait} s, the robot turns "
                           "round on the line and reports the corridor blocked; the planner sends it the other way, "
                           "turning at the T-junctions into the alternative corridor.",
                           "Remove the obstacle within the wait time and the robot simply continues.",
                           "To show a completely blocked track, block the middle corridor too: there is no "
                           "way left, so the robot stops and staff are alerted."])
