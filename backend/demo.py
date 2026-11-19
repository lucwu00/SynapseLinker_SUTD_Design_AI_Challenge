"""Reproducible demo scenes (fictional patients): the 5-scene story first, then the extra scenes. Each returns a narration for the presenter
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
    """Scenes 1-5: the demo story (two patients, two robots). Scenes 6-10: the extra scenes."""
    story = {1: d1, 2: d2, 3: d3, 4: d4, 5: d5}
    extra = {6: s1, 7: s2, 8: s3, 9: s4, 10: s9}     # s5-s8 are covered by the story (still used by the tests)
    out = (story.get(n) or extra[n])(svc)
    out["scene"] = n
    out["title"] = TITLES.get(n, out.get("title"))
    return out


SCENE_NUMBERS = range(1, 11)
TITLES = {6: "Patient checks in", 7: "Another patient: pre-op test order first", 8: "ECG room congestion",
          9: "Blood-test room closes", 10: "Corridor blocked, reroute"}


# ============================================================================ the demo story
# Mr Tan (A): new fracture referral -> consultation -> the doctor orders an X-ray -> X-ray -> doctor review.
# Mdm Lee (B): pre-op, needs an X-ray before her consultation -> X-ray -> consultation.
# EDi-01 is the physical OjanBot on the table; EDi-02 is the virtual assistant (second screen, voice).
A, B = "ORTHO_001", "ORTHO_101"
PATIENT_B = dict(patient_id=B, display_name="Mdm Lee (fictional)", visit_type="pre_op", referral_category="Knee/Hip",
                 age=67, doctor_id="DR02", pre_consult_tests=["XRAY"])


def _room_label(svc, room):
    r = svc.planner.map["rooms"].get(room or "", {})
    return r.get("label") or r.get("station_label") or room or "-"


def _status(svc, pid):
    return svc._patient(pid)["status"] if _exists(svc, pid) else None


def _need(svc, pid, statuses, msg):
    if _status(svc, pid) not in statuses:
        raise ServiceError(msg, 409)


def _xray_room_1(svc):
    rooms = [k for k, v in svc.planner.map["rooms"].items() if v.get("station") == "XRAY"]
    for k in rooms:
        if "1" in (svc.planner.map["rooms"][k].get("label") or ""):
            return k
    return rooms[0] if rooms else None


def d1(svc):
    """Mr Tan checks in; EDi-01 takes him to Consultation Room 1."""
    svc.reset()
    svc.check_in({**PATIENTS[A], "appointment_time": svc.clock.to_dict()["clinic_time"], "robot_id": "EDi-01"})
    p = svc._patient(A); r = p["recommendation"] or {}
    wait = next((c.get("predicted_wait_min") for c in r.get("candidates", [])), None)
    table = ("ON THE TABLE: EDi-01 on START (middle of the bottom line) facing left; all rooms empty. "
             if _physical(svc) else "")
    return dict(title="Patient A checks in",
                narration=[table + "Mr Tan (queue 1001, new fracture referral, walking aid) checks in. EDi-01 is assigned to him.",
                           f"RULES: a new referral sees the doctor first; next eligible service = {svc.eligible(p)}.",
                           f"ML: predicted consultation wait {wait} min (Model A).",
                           f"AUTONOMOUS: EDi-01 guides him to {_room_label(svc, p.get('assigned_room'))}: straight, right at the "
                           "corner, stop on the bar, turn to scan the room (free), turn back.",
                           "When EDi-01 shows ARRIVED, go to scene 2."])


def d2(svc):
    """Mr Tan goes in to the doctor; Mdm Lee checks in and needs an X-ray first (EDi-02 shows her the way)."""
    if not _exists(svc, A):
        raise ServiceError("Run scene 1 first", 409)
    _need(svc, A, ("QUEUED", "IN_SERVICE"), "Wait until EDi-01 has brought Mr Tan to the consultation room (ARRIVED)")
    if _status(svc, A) == "QUEUED":
        svc.start_service(A)                                 # Mr Tan is now with the doctor
    room = _xray_room_1(svc)
    if not _exists(svc, B):
        svc.check_in({**PATIENT_B, "appointment_time": svc.clock.to_dict()["clinic_time"], "robot_id": "EDi-02"})
        rec = _rec(svc, B)
        svc.confirm_destination(B, "XRAY", source="staff")
        svc.db.update("patients", "patient_id", B, assigned_room=room)
        svc.show_virtual_walk("EDi-02", B, room)            # drawn on the floor map only; nothing on the table moves
        svc.arrive_at_station(B)                             # she is in the room; her X-ray is not started in the system yet
        svc.update_room(room, person_present=True)
    lbl = _room_label(svc, room)
    table = f"ON THE TABLE: put the white block in {lbl} - that is Mdm Lee. Leave EDi-01 where it is. " if _physical(svc) else ""
    return dict(title="Patient B checks in, X-ray first",
                narration=["Mr Tan is now with the doctor in Consultation Room 1.",
                           "Mdm Lee (queue 1002, pre-op knee) checks in. RULES: her pre-op test must be done before she "
                           "sees the doctor. Blood test and ECG are not on this table, so her test today is an X-ray.",
                           f"EDi-01 is busy with Mr Tan, so EDi-02 (virtual assistant) guides her to {lbl}. On the floor "
                           "map EDi-02 walks her there on a dashed line; on the table only EDi-01 is real, so nothing moves.",
                           table + f"She is waiting inside {lbl}, but staff have not started her X-ray in the system yet, "
                                   "so the system still lists the room as FREE. Go to scene 3."])


def d3(svc):
    """Mr Tan's consultation ends with an X-ray order; EDi-01 finds X-ray Room 1 occupied and re-guides him."""
    _need(svc, A, ("IN_SERVICE", "QUEUED"), "Run scenes 1 and 2 first")
    p = svc._patient(A)
    if p["assigned_destination"] != "CONSULT":
        raise ServiceError("Mr Tan is not in the consultation any more - press Reset demo and start from scene 1", 409)
    svc.complete_service(A, "CONSULT", added_steps=["XRAY", "REVIEW"], recorded_by="Doctor")
    if _status(svc, A) == "AWAITING_GUIDANCE":             # auto-dispatch did not run (e.g. robot not ready)
        svc.confirm_destination(A, "XRAY", robot_id="EDi-01", source="staff")
    p = svc._patient(A)
    first = _room_label(svc, p.get("assigned_room"))
    return dict(title="A to X-ray: room occupied, re-guide",
                narration=["The doctor finishes Mr Tan's consultation and orders an X-ray, then a doctor review "
                           "(clinician order). Consultation Room 1 is free again.",
                           f"RULES + ML: next service X-ray. EDi-01 takes him to the nearest X-ray room the system believes "
                           f"is free: {first}.",
                           "WATCH: on arrival EDi-01 turns to scan the room, finds Mdm Lee there (OCCUPIED), updates the room "
                           "status and re-guides Mr Tan round the top-right corner to the other X-ray room.",
                           "Nobody told the system the room was taken: the robot sensed it. When EDi-01 shows ARRIVED at "
                           "X-ray Room 2, go to scene 4."])


def d4(svc):
    """Mdm Lee's X-ray is done; EDi-01 takes over and brings her to Consultation Room 1."""
    _need(svc, A, ("QUEUED", "IN_SERVICE"), "Wait until EDi-01 has brought Mr Tan to X-ray Room 2 (ARRIVED)")
    if _status(svc, A) == "QUEUED":
        svc.start_service(A)                                 # Mr Tan's X-ray starts in X-ray Room 2
    _need(svc, B, ("QUEUED", "IN_SERVICE"), "Mdm Lee is not at the X-ray - run scene 2 first")
    room = svc._patient(B).get("assigned_room") or _xray_room_1(svc)
    svc.complete_service(B, "XRAY")
    svc.update_room(room, person_present=False)
    svc._event("ROBOT_CHOSEN", "system", "EDi-02 is now helping another patient at Registration. EDi-01 has just "
               "dropped Mr Tan at X-ray Room 2, is nearby and free: EDi-01 takes over Mdm Lee", patient_id=B, robot_id="EDi-01")
    svc.assign_robot("EDi-01", B, force=True)
    svc.robot_notes["EDi-01"] = (B, "takeover")
    if _status(svc, B) == "AWAITING_GUIDANCE":
        svc.confirm_destination(B, "CONSULT", robot_id="EDi-01", source="staff")
    p = svc._patient(B)
    table = (f"ON THE TABLE: take the white block out of {_room_label(svc, room)}; Consultation Room 1 stays empty. "
             if _physical(svc) else "")
    return dict(title="B to consultation (EDi-01)",
                narration=[table + "Mr Tan's X-ray has started in X-ray Room 2.",
                           "Mdm Lee's X-ray is done. RULES: her test is complete, so she may now see the doctor.",
                           "EDi-02 has been taken by another patient at Registration. EDi-01 has just dropped Mr Tan at "
                           "X-ray Room 2, so it is nearby and free.",
                           f"HANDOVER: EDi-01 takes over her journey (it was EDi-02's) and guides her to "
                           f"{_room_label(svc, p.get('assigned_room'))}. It chooses the route along the outside corners, "
                           "which it can follow reliably, and scans the room on arrival (free).",
                           "Go to scene 5 while it drives."])


def d5(svc):
    """Mr Tan's X-ray is done; he finds EDi-02 free and types his queue number; it picks up his journey."""
    _need(svc, A, ("QUEUED", "IN_SERVICE"), "Mr Tan is not at the X-ray - run scenes 1 to 4 first")
    if svc._patient(A)["assigned_destination"] != "XRAY":
        raise ServiceError("Mr Tan is not at the X-ray - run scenes 1 to 4 first", 409)
    svc.complete_service(A, "XRAY")
    svc.db.update("robots", "robot_id", "EDi-02", patient_id=None)       # EDi-02 is free again
    p = svc._patient(A)
    est = svc.journey(A).get("estimate") or {}
    return dict(title="A after X-ray: EDi-02 voice",
                narration=["Mr Tan's X-ray is done. EDi-01 is still busy with Mdm Lee, and EDi-02 is free again.",
                           f"OPEN the EDi-02 screen (localhost:8000/#/robot/EDi-02) and type his queue number "
                           f"{p.get('queue_no') or '1001'}, then press Find. EDi-02 picks up his whole journey: it already "
                           "knows he has had his consultation and X-ray.",
                           f"Next for him: {', '.join(svc.eligible(p)) or '-'} (doctor review of the X-ray). "
                           f"Estimated time left: about {est.get('remaining_min', '?')} min, if the queues stay as they are.",
                           "Tap Ask me and ask, for example: 'What is my next step?', 'How long will the rest of my visit take?', "
                           "'Can I go to the cafe while waiting?', or switch to 华文 and ask '我下一步去哪里？'.",
                           "End of the story. Scenes 6-10 are stress test scenes (press Reset demo first)."])



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
    targets = ["ECG"]                                                 # scene: ECG room congestion
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
    """Room occupied, re-guide. Run right after scene 1 (EDi-01 has delivered Mr T to the consult room) and it
    continues from there: Mr T is now being seen, a second patient needs an X-ray first. Run on its own and it
    starts from a clean clinic with EDi-01 on START."""
    a = svc.adapters.get("EDi-01")
    here = svc.planner.node_of(a.get_robot_status().position) if a else None
    cont = (_exists(svc, "ORTHO_001") and a is not None and here not in (None, svc.planner.map.get("start_node"))
            and a.get_robot_status().state.value in ("ARRIVED", "IDLE"))
    if not cont:
        svc.reset()
    rooms = _xray_rooms_by_distance(svc)
    first, other = rooms[0], (rooms[1] if len(rooms) > 1 else None)
    svc.update_room(first, person_present=True)             # someone walked in; nobody updated the system
    first_line = ""
    if cont:
        p1 = svc._patient("ORTHO_001")
        if p1["status"] == "QUEUED":
            svc.start_service("ORTHO_001")                   # Mr T goes in: the consult room is now occupied
        svc.check_in({**XRAY_PATIENT, "appointment_time": svc.clock.to_dict()["clinic_time"]})
        svc.assign_robot("EDi-01", "ORTHO_201", force=True)
        first_line = "Mr T is now with the doctor, so the consult room is occupied. "
    else:
        svc.check_in({**XRAY_PATIENT, "appointment_time": svc.clock.to_dict()["clinic_time"], "robot_id": "EDi-01"})
    p = svc._patient("ORTHO_201")
    zone = svc.planner.map["rooms"][first].get("zone")
    where = "leave EDi-01 where it stopped" if cont else "put EDi-01 on START facing left (towards Consult Room 1)"
    setup = f"ON THE TABLE: {where}, and put a person block in {_label(svc, first)} at {_cm(zone)}. " \
        if _physical(svc) and zone else ""
    return dict(scene=8, title="Room occupied, re-guide",
                narration=[setup + first_line + f"A person is sitting in {_label(svc, first)}, but the system still thinks it is free.",
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
    where = (f"ON THE TABLE: robot on START facing left, trolley box at {_cm(box)}. " if _physical(svc) and box else
             f"A trolley (simulated) is blocking the corridor {edge[0]}-{edge[1]}. ")
    return dict(scene=9, title="Corridor blocked, reroute",
                narration=[f"Clean start. Closed rooms: {closed}. " + where + "The system does not know yet.",
                           f"EDi-01 sets off for {_label(svc, target)} on the shortest route "
                           f"{' -> '.join(t.route.nodes) if t else ''}.",
                           f"WATCH: it meets the obstacle and stops. Still there after {wait} s, so the robot turns round on "
                           "the line, reports the corridor blocked, and the planner sends it through the middle connecting "
                           "corridor instead.",
                           "THEN: click the middle corridor on the floor map. The trolley moves there: the first corridor is "
                           "clear again, the robot meets the new obstacle, turns round and takes the first route to "
                           f"{_label(svc, target)}.",
                           "Remove the obstacle within the wait time and the robot simply continues."])
