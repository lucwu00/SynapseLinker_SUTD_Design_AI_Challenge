"""Journey state machine, autonomous loop, robots/handover and navigation safety.  Run: python -m pytest -q"""
import pytest

from backend.service import SynapseService, ServiceError
from backend import demo
from navigation.route_planner import RoutePlanner
from navigation.map_loader import validate_map, MapError, load_map
from navigation.simulation_adapter import SimulationAdapter
from navigation.esp32_adapter import ESP32Adapter
from navigation.navigation_interface import NavigationError
from navigation.navigation_models import NavState, ACTIVE_STATES
from flow.predictor import Predictor

PRED = Predictor()


@pytest.fixture
def svc():
    s = SynapseService(db_path=":memory:", predictor=PRED)
    yield s
    s.stop()


def pre_op(pid="T1", **kw):
    return dict(patient_id=pid, visit_type="pre_op", referral_category="Knee/Hip", age=66, doctor_id="DR02",
                pre_consult_tests=["XRAY", "PHLEBOTOMY", "ECG"], **kw)


# ---------------------------------------------------------------- journey
def test_check_in_runs_inference_and_rules(svc):
    j = svc.check_in(pre_op())
    rec = j["patient"]["recommendation"]
    assert j["patient"]["completed_steps"] == ["REGISTRATION"]
    assert rec["status"] == "ROUTE" and rec["decision_source"] == "ml+rules"
    assert set(j["eligible_now"]) == {"XRAY", "PHLEBOTOMY", "ECG"}
    assert all(c["predicted_wait_min"] >= 0 for c in rec["candidates"])


def test_invalid_and_duplicate_check_in(svc):
    svc.check_in(pre_op())
    with pytest.raises(ServiceError):
        svc.check_in(pre_op())
    with pytest.raises(ServiceError):
        svc.check_in(dict(patient_id="X", visit_type="new", referral_category="Fracture", age=40, doctor_id="DR99"))


def test_arrival_is_not_completion_and_completion_is_explicit(svc):
    svc.check_in(pre_op())
    step = svc._patient("T1")["recommendation"]["recommended_station"]
    svc.confirm_destination("T1", step)
    j = svc.arrive_at_station("T1")
    assert j["patient"]["status"] == "QUEUED" and step not in j["patient"]["completed_steps"]
    svc.start_service("T1")
    j = svc.complete_service("T1", step)
    assert step in j["patient"]["completed_steps"] and j["patient"]["status"] == "AWAITING_GUIDANCE"
    assert j["history"][0]["actual_wait_min"] is not None


def test_prerequisites_and_closed_rooms_enforced(svc):
    svc.check_in(pre_op())
    with pytest.raises(ServiceError):
        svc.confirm_destination("T1", "CONSULT")
    svc.update_station("ECG", available=False)
    assert "ECG" not in [c["step"] for c in svc._patient("T1")["recommendation"]["candidates"]]
    with pytest.raises(ServiceError):
        svc.confirm_destination("T1", "ECG")


def test_closing_destination_reroutes_guided_patient(svc):
    svc.check_in(pre_op())
    svc.confirm_destination("T1", "ECG")
    svc.update_station("ECG", available=False)
    p = svc._patient("T1")
    assert p["status"] == "AWAITING_GUIDANCE" and p["recommendation"]["recommended_station"] != "ECG"


def test_clinician_orders_validated(svc):
    svc.check_in(dict(patient_id="N1", visit_type="new", referral_category="Fracture", age=50, doctor_id="DR01"))
    svc.confirm_destination("N1", "CONSULT"); svc.arrive_at_station("N1"); svc.start_service("N1")
    with pytest.raises(ServiceError):
        svc.complete_service("N1", "CONSULT", added_steps=["REVIEW"])        # review without X-ray breaks R07
    j = svc.complete_service("N1", "CONSULT", added_steps=["XRAY", "REVIEW", "PHARMACY"])
    assert j["eligible_now"] == ["XRAY"] and j["patient"]["required_steps"][-1] == "PHARMACY"


def test_escalation_suspends_routing_and_robot(svc):
    svc.check_in(dict(patient_id="N1", visit_type="new", referral_category="Fracture", age=50, robot_id="EDi-01"))
    assert svc.adapters["EDi-01"].get_robot_status().state == NavState.NAVIGATING
    svc.escalate("N1", "feels faint")
    assert svc._patient("N1")["recommendation"]["status"] == "ESCALATED"
    assert svc.adapters["EDi-01"].get_robot_status().state == NavState.IDLE
    with pytest.raises(ServiceError):
        svc.confirm_destination("N1", "CONSULT")


def test_station_load_changes_ml_prediction(svc):
    before = svc.station_predictions["PHARMACY"]["predicted_wait_new_arrival_min"]
    svc.update_station("PHARMACY", background_queue=40, recent_wait_hint=45.0, arrivals_hint=20)
    after = svc.station_predictions["PHARMACY"]
    assert after["predicted_wait_new_arrival_min"] > before + 10
    assert after["alert_level"] in ("MEDIUM", "HIGH")


# ---------------------------------------------------------------- robots
def test_autonomous_dispatch_and_arrival(svc):
    j = svc.check_in(dict(patient_id="N1", visit_type="new", referral_category="Fracture", age=50, robot_id="EDi-01"))
    assert j["patient"]["status"] == "GUIDED" and j["navigation_task"]["state"] == "NAVIGATING"
    a = svc.adapters["EDi-01"]
    for _ in range(200):
        a.tick(0.5)
    assert svc._patient("N1")["status"] == "QUEUED"


def test_duplicate_navigation_returns_same_task(svc):
    j = svc.check_in(dict(patient_id="N1", visit_type="new", referral_category="Fracture", age=50, robot_id="EDi-01"))
    t1 = j["navigation_task"]["task_id"]
    assert svc.create_nav_task("EDi-01", "N1", "CONSULT")["task_id"] == t1


def test_virtual_robot_never_moves(svc):
    svc.check_in(pre_op(robot_id="EDi-02"))
    assert svc._patient("T1")["status"] == "AWAITING_GUIDANCE"        # no auto-dispatch for a virtual robot
    r = svc.confirm_destination("T1", "XRAY", robot_id="EDi-02")
    assert r["navigation_task"] is None


def test_handover_preserves_journey(svc):
    for n in (1, 5, 6):
        demo.scene(svc, n)
    s2 = svc.robot_session("EDi-02")
    assert s2["patient_id"] == "ORTHO_001"
    assert s2["journey"]["patient"]["completed_steps"] == ["REGISTRATION", "CONSULT"]
    assert s2["journey"]["eligible_now"] == ["XRAY"]
    assert svc.robot_session("EDi-01")["patient_id"] == "ORTHO_003"
    assert svc._patient("ORTHO_001")["robot_id"] == "EDi-02"


def test_all_demo_scenes_run(svc):
    for n in range(1, 8):
        assert demo.scene(svc, n)["narration"]


def test_scene3_changes_order(svc):
    demo.scene(svc, 2)
    before = svc._patient("ORTHO_002")["recommendation"]["recommended_sequence"]
    demo.scene(svc, 3)
    after = svc._patient("ORTHO_002")["recommendation"]["recommended_sequence"]
    assert after != before and after[0] != before[0]          # the congested first test moved later


# ---------------------------------------------------------------- navigation
def test_route_turns_and_known_blocked_corridor():
    rp = RoutePlanner()
    r = rp.plan_to_node("START", rp.room_node("ROOM_B"), "XRAY")
    assert r.nodes == ["START", "J1", "J2", "N_B"] and r.junction_instructions[-1]["action"] == "STOP"
    rp.block("J1", "J2")
    r2 = rp.plan_to_node("J1", rp.room_node("ROOM_B"), "XRAY", heading_from="START")
    assert ("J1", "J2") not in zip(r2.nodes, r2.nodes[1:]) and r2.nodes[:2] == ["J1", "J4"]
    assert r2.junction_instructions[0]["action"] == "RIGHT"
    r3 = rp.plan_to_node("J1", "START", "START", heading_from="START")
    assert r3.junction_instructions[0]["action"] == "U_TURN"


def test_map_validation_rejects_bad_maps():
    m = load_map()
    with pytest.raises(MapError):
        validate_map(dict(m, units="feet"))
    with pytest.raises(MapError):
        validate_map(dict(m, rooms={**m["rooms"], "ROOM_X": {"node": "NOWHERE", "station": "XRAY", "label": "x"}}))
    with pytest.raises(MapError):
        validate_map(dict(m, restricted_nodes=["N_B"]))


def _drive(svc, n=600):
    a = svc.adapters["EDi-01"]
    for _ in range(n):
        a.tick(0.5)


def test_occupied_room_scan_reguides_to_free_room(svc):
    demo.scene(svc, 8)
    assert svc._patient("ORTHO_201")["assigned_room"] == "ROOM_B"
    _drive(svc)
    p = svc._patient("ORTHO_201")
    assert p["status"] == "QUEUED" and p["assigned_room"] == "ROOM_C"
    rooms = {r["room_id"]: r for r in svc.db.query("SELECT * FROM rooms")}
    assert rooms["ROOM_B"]["occupied"] == 1 and "scan" in rooms["ROOM_B"]["occupancy_source"]


def test_no_free_room_releases_destination(svc):
    svc.update_room("ROOM_B", person_present=True)
    svc.update_room("ROOM_C", person_present=True)
    svc.check_in({**demo.XRAY_PATIENT, "robot_id": "EDi-01"})
    _drive(svc)
    p = svc._patient("ORTHO_201")
    assert p["status"] == "AWAITING_GUIDANCE" and p["recommendation"]["status"] == "WAIT_UNAVAILABLE"
    svc.update_room("ROOM_C", person_present=False)                         # the person leaves...
    svc.update_room("ROOM_C", occupied=False, source="IoT sensor")          # ...and the sensor reports it
    _drive(svc)
    p = svc._patient("ORTHO_201")
    assert p["status"] == "QUEUED" and p["assigned_room"] == "ROOM_C"       # auto re-dispatched


def test_blocked_corridor_reroutes(svc):
    demo.scene(svc, 9)
    _drive(svc)
    p = svc._patient("ORTHO_201")
    assert p["status"] == "QUEUED" and p["assigned_room"] == "ROOM_C"
    assert ("J2", "J3") in svc.planner.blocked
    types = [e["type"] for e in svc.db.query("SELECT type FROM events")]
    assert "ROBOT_BLOCKED" in types and "CORRIDOR_BLOCKED" in types and "REROUTED" in types


def test_obstacle_cleared_in_time_continues_without_reroute(svc):
    svc.set_obstacle("J1", "J2", True)
    svc.check_in({**demo.XRAY_PATIENT, "robot_id": "EDi-01"})
    a = svc.adapters["EDi-01"]
    for _ in range(60):
        a.tick(0.5)
        if a.get_robot_status().state == NavState.BLOCKED:
            break
    svc.set_obstacle("J1", "J2", False)
    _drive(svc)
    assert svc._patient("ORTHO_201")["status"] == "QUEUED" and not svc.planner.blocked


def test_fully_blocked_track_alerts_staff(svc):
    svc.set_obstacle("J1", "J2", True)
    svc.set_obstacle("J4", "J5", True)
    svc.check_in({**demo.XRAY_PATIENT, "robot_id": "EDi-01"})
    _drive(svc)
    p = svc._patient("ORTHO_201")
    assert p["status"] == "ESCALATED" and "blocked" in p["escalation_reason"]
    assert svc.adapters["EDi-01"].get_robot_status().state == NavState.IDLE


def test_station_off_track_gives_directions_only(svc):
    svc.check_in(pre_op(robot_id="EDi-01"))
    p = svc._patient("T1")
    if p["recommendation"]["recommended_station"] != "XRAY":
        assert p["status"] == "GUIDED" and p["assigned_room"] is None


def test_queue_number_and_patients_ahead(svc):
    j = svc.check_in(pre_op())
    assert j["patient"]["queue_no"] == "1001"
    svc.confirm_destination("T1", "XRAY"); j = svc.arrive_at_station("T1")
    assert j["patients_ahead"] == svc._station("XRAY")["background_queue"]


def test_simulation_adapter_safety():
    rp = RoutePlanner()
    a = SimulationAdapter("R", speed_m_s=1.0, nodes=rp.map["nodes"])
    route = rp.plan(None, "XRAY")
    with pytest.raises(NavigationError):
        a.navigate("P", "XRAY", "T0", None)                       # invalid route -> no motion
    a.navigate("P", "XRAY", "T1", route)
    assert a.navigate("P", "XRAY", "T1", route).task_id == "T1"   # idempotent
    with pytest.raises(NavigationError):
        a.navigate("P", "ECG", "T2", route)                       # busy
    a.simulate_obstacle(True); a.tick(1)
    assert a.get_robot_status().state == NavState.BLOCKED and a.current.progress == 0
    a.simulate_obstacle(False); a.stop_robot(); a.tick(100)
    assert a.get_robot_status().state == NavState.EMERGENCY_STOP   # no auto-resume
    with pytest.raises(NavigationError):
        a.navigate("P", "ECG", "T3", route)
    a.resume_navigation(); a.tick(100)
    assert a.get_robot_status().state == NavState.ARRIVED


def test_esp32_contract_and_timeout():
    ev = []
    a = ESP32Adapter("EDi-01", on_event=lambda k, d: ev.append(k), comm_timeout_s=0.0)
    a.navigate("P", "XRAY", "T1", RoutePlanner().plan(None, "XRAY"))
    cmd = a.poll_command()
    assert cmd["command"] == "NAVIGATE" and cmd["task_id"] == "T1" and cmd["junction_instructions"]
    a.stop_robot()
    assert a.poll_command()["command"] == "STOP"                  # stop jumps the queue
    a.resume_navigation()
    assert a.poll_command()["command"] == "RESUME"
    a.handle_status({"task_id": "T1", "event": "BLOCKED"})
    assert a.get_robot_status().state == NavState.BLOCKED
    a.handle_status({"task_id": "T1", "event": "PATH_BLOCKED", "marks_passed": 1, "turned_around": True})
    assert "PATH_BLOCKED" in ev
    a.status.last_seen -= 10
    a.tick(1)
    assert "COMM_LOST" in ev and a.tasks["T1"].state == NavState.FAILED


# ---------------------------------------------------------------- 60 x 60 cm double-loop table
from navigation.map_loader import TABLE_MAP


def table_planner():
    return RoutePlanner(load_map(TABLE_MAP))


def feats(r):
    return [(f["node"], f["pattern"], f["action"]) for f in r.marks]


def test_double_loop_features_and_turns():
    rp = table_planner()
    assert rp.map["node_kind"]["J_L"] == "junction" and rp.map["node_kind"]["S1"] == "station"
    r = rp.plan_to_node("START", "S1", "XRAY")                          # robot faces right at START
    assert r.nodes == ["START", "J_R", "S1"] and r.start_action == "GO"
    assert feats(r) == [("J_R", "L", "PASS"), ("S1", "C", "STOP")] and r.scan_side == "LEFT"
    assert r.marks[0]["dist_cm"] == pytest.approx(36.6, abs=0.2)        # corner rounded to 8 cm radius
    r = rp.plan_to_node("START", "S3", "CONSULT")                       # behind it -> turn round first
    assert r.start_action == "U_TURN" and feats(r) == [("S3", "C", "STOP")] and r.scan_side == "RIGHT"
    r = rp.plan_to_node("S1", "S3", "CONSULT", heading_from="J_R")      # over the top, past the left T
    assert r.nodes == ["S1", "S2", "J_L", "S3"] and feats(r)[1] == ("J_L", "L", "PASS")


def test_double_loop_blocked_uses_middle_corridor():
    rp = table_planner()
    ev = []
    a = ESP32Adapter("EDi-01", on_event=lambda k, d: ev.append((k, d)), nodes=rp.map["nodes"])
    a.navigate("P", "XRAY", "T1", rp.plan_to_node("START", "S1", "XRAY"))
    a.handle_status({"task_id": "T1", "event": "PATH_BLOCKED", "marks_passed": 0, "turned_around": True})
    d = [d for k, d in ev if k == "PATH_BLOCKED"][0]
    assert d["edge"] == ["START", "J_R"] and d["mid_segment"]
    rp.block(*d["edge"])
    r = rp.plan_to_node("START", "S1", "XRAY", heading_from=d["heading_from"], mid_segment=True)
    assert r.nodes == ["START", "S3", "J_L", "J_R", "S1"] and r.start_action == "GO"
    assert feats(r) == [("START", "C", "PASS"), ("S3", "C", "PASS"), ("J_L", "R", "RIGHT"),
                        ("J_R", "C", "LEFT"), ("S1", "C", "STOP")]
    assert r.marks[0]["dist_cm"] == 0                                   # unknown: turned round mid-corridor
    rp.block("J_L", "J_R")                                              # middle corridor too -> over the top
    r = rp.plan_to_node("START", "S1", "XRAY", heading_from="J_R", mid_segment=True)
    assert r.nodes == ["START", "S3", "J_L", "S2", "S1"] and feats(r)[2] == ("J_L", "R", "PASS")
    rp.block("S2", "J_L")
    with pytest.raises(MapError):
        rp.plan_to_node("START", "S1", "XRAY", heading_from="J_R", mid_segment=True)


def test_blocked_before_leaving_start_mark():
    rp = table_planner()
    ev = []
    a = ESP32Adapter("EDi-01", on_event=lambda k, d: ev.append((k, d)), nodes=rp.map["nodes"])
    a.navigate("P", "XRAY", "T1", rp.plan_to_node("START", "S1", "XRAY"))
    a.handle_status({"task_id": "T1", "event": "PATH_BLOCKED", "marks_passed": 0, "turned_around": True, "on_mark": True})
    d = [d for k, d in ev if k == "PATH_BLOCKED"][0]
    assert not d["mid_segment"]
    rp.block(*d["edge"])
    r = rp.plan_to_node("START", "S1", "XRAY", heading_from=d["heading_from"], mid_segment=d["mid_segment"])
    assert r.start_action == "GO" and feats(r)[0] == ("S3", "C", "PASS")   # START not counted again


def test_table_command_contract():
    rp = table_planner()
    a = ESP32Adapter("EDi-01", nodes=rp.map["nodes"])
    r = rp.plan_to_node("START", "S1", "XRAY"); r.room_id = "ROOM_C"
    a.navigate("P", "XRAY", "T1", r)
    c = a.poll_command()
    assert c["start_action"] == "GO" and c["scan_room_on_arrival"] and c["scan_side"] == "LEFT"
    assert [(f["t"], f["p"], f["a"]) for f in c["features"]] == [("J", "L", "PASS"), ("S", "C", "STOP")]
    assert c["features"][1]["d"] == 8.0 and c["features"][1]["m"] == 16.0


@pytest.fixture
def table_svc():
    s = SynapseService(db_path=":memory:", predictor=PRED)
    s.planner = table_planner()
    s.reset()
    yield s
    s.stop()


def test_table_scenes_8_and_9(table_svc):
    svc = table_svc
    demo.scene(svc, 8)
    assert svc._patient("ORTHO_201")["assigned_room"] == "ROOM_C"
    _drive(svc)
    p = svc._patient("ORTHO_201")
    assert p["status"] == "QUEUED" and p["assigned_room"] == "ROOM_B"
    assert svc.robot_heading["EDi-01"] == "S1"
    demo.scene(svc, 9)
    _drive(svc)
    p = svc._patient("ORTHO_201")
    assert p["status"] == "QUEUED" and p["assigned_room"] == "ROOM_C"
    assert ("J_R", "START") in svc.planner.blocked or ("START", "J_R") in svc.planner.blocked
    types = [e["type"] for e in svc.db.query("SELECT type FROM events")]
    assert "CORRIDOR_BLOCKED" in types and "REROUTED" in types


def test_esp32_radio_off_while_driving():
    """AN wire on an ADC2 pin: the robot goes quiet while driving; that is not a comm loss."""
    ev = []
    a = ESP32Adapter("EDi-01", on_event=lambda k, d: ev.append(k), comm_timeout_s=6.0, radio_off_timeout_s=60.0)
    a.navigate("P", "XRAY", "T1", RoutePlanner().plan(None, "XRAY"))
    a.poll_command()
    a.handle_status({"task_id": "T1", "event": "ACK"})
    a.handle_status({"task_id": "T1", "event": "RADIO_OFF"})
    a.status.last_seen -= 20                                     # 20 s silence while driving
    a.tick(1)
    assert "COMM_LOST" not in ev and a.tasks["T1"].state in ACTIVE_STATES
    a.handle_status({"task_id": "T1", "event": "ARRIVED"})       # back online on arrival
    assert a.tasks["T1"].state == NavState.ARRIVED and not a.radio_off
    a.status.last_seen -= 10                                     # normal 6 s limit applies again
    a.tick(1)
    assert "COMM_LOST" in ev
    a2 = ESP32Adapter("EDi-01", on_event=lambda k, d: ev.append(k), radio_off_timeout_s=60.0)
    a2.navigate("P", "XRAY", "T2", RoutePlanner().plan(None, "XRAY"))
    a2.poll_command(); a2.handle_status({"task_id": "T2", "event": "RADIO_OFF"})
    a2.status.last_seen -= 61                                    # never came back: fail the task
    a2.tick(1)
    assert a2.tasks["T2"].state == NavState.FAILED


def _serve(svc, pid):
    """Guide, arrive, start and complete whatever is recommended next; returns the step."""
    p = svc._patient(pid)
    step = p["assigned_destination"] or p["recommendation"]["recommended_station"]
    if p["status"] == "AWAITING_GUIDANCE":
        svc.confirm_destination(pid, step, source="staff")
    if svc._patient(pid)["status"] == "GUIDED":
        svc.arrive_at_station(pid)
    if svc._patient(pid)["status"] == "QUEUED":
        svc.start_service(pid)
    return step


def test_staff_change_orders(svc):
    svc.auto_dispatch = False
    svc.check_in(dict(patient_id="N1", visit_type="new", referral_category="Fracture", age=50, doctor_id="DR01"))
    # before the consult: pre-consult tests can be added and removed
    svc.change_orders("N1", add=["XRAY", "ECG"])
    assert svc._patient("N1")["required_steps"][:4] == ["REGISTRATION", "XRAY", "ECG", "CONSULT"]
    svc.change_orders("N1", remove=["ECG"])
    with pytest.raises(ServiceError):
        svc.change_orders("N1", add=["CAST_ROOM"])            # treatment only after the consult
    with pytest.raises(ServiceError):
        svc.change_orders("N1", remove=["CONSULT"])
    while "CONSULT" not in svc._patient("N1")["completed_steps"]:
        step = _serve(svc, "N1")
        svc.complete_service("N1", step,
                             added_steps=["PROCEDURE", "REVIEW", "XRAY", "CAST_ROOM"] if step == "CONSULT" else None)
    req = svc._patient("N1")["required_steps"]
    i = req.index("CONSULT")
    assert req[i + 1:] == ["XRAY", "REVIEW", "CAST_ROOM", "PROCEDURE", "PAYMENT"]     # clicked order sorted
    svc.change_orders("N1", remove=["PROCEDURE"], add=["DRESSING", "PHARMACY"])
    assert svc._patient("N1")["required_steps"][i + 1:] == ["XRAY", "REVIEW", "CAST_ROOM", "DRESSING", "PAYMENT", "PHARMACY"]
    svc.change_orders("N1", remove=["XRAY"])                 # the doctor review of it goes too (R07)
    assert svc._patient("N1")["required_steps"][i + 1:] == ["CAST_ROOM", "DRESSING", "PAYMENT", "PHARMACY"]
    step = _serve(svc, "N1")                                   # now being served at the cast room
    with pytest.raises(ServiceError):
        svc.change_orders("N1", remove=[step])
    assert svc._patient("N1")["recommendation"] is not None


def test_check_in_with_name_only_gets_an_id(svc):
    j = svc.check_in(dict(display_name="Mr Z", visit_type="follow_up", referral_category="Knee/Hip", age=60))
    j2 = svc.check_in(dict(display_name="Mdm Y", visit_type="follow_up", referral_category="Knee/Hip", age=61))
    assert j["patient"]["patient_id"] == "ORTHO_001" and j2["patient"]["patient_id"] == "ORTHO_002"
    assert j["patient"]["display_name"] == "Mr Z"


def test_patient_id_with_spaces(svc):
    from backend.schemas import CheckInRequest
    CheckInRequest(patient_id="Mr Z", visit_type="follow_up", referral_category="Knee/Hip", age=60)
    for bad in [" Mr Z", "Mr  Z", "Mr@Z"]:
        with pytest.raises(Exception):
            CheckInRequest(patient_id=bad, visit_type="follow_up", referral_category="Knee/Hip", age=60)
    j = svc.check_in(dict(patient_id="Mr Z", visit_type="follow_up", referral_category="Knee/Hip", age=60))
    assert j["patient"]["patient_id"] == "Mr Z"
    svc.change_orders("Mr Z", add=["ECG"])
