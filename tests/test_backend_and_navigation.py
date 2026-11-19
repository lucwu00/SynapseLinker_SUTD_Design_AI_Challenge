import json
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
    for f in (demo.s1, demo.s5, demo.s6):
        f(svc)
    s2 = svc.robot_session("EDi-02")
    assert s2["patient_id"] == "ORTHO_001"
    assert s2["journey"]["patient"]["completed_steps"] == ["REGISTRATION", "CONSULT"]
    assert s2["journey"]["eligible_now"] == ["XRAY"]
    assert svc.robot_session("EDi-01")["patient_id"] == "ORTHO_003"
    assert svc._patient("ORTHO_001")["robot_id"] == "EDi-02"


def test_all_demo_scenes_run(svc):
    for n in range(6, 11):
        assert demo.scene(svc, n)["narration"]


def test_scene3_changes_order(svc):
    demo.scene(svc, 7)
    assert svc._patient("ORTHO_002")["assigned_destination"] == "XRAY"          # starts with the X-ray
    demo.scene(svc, 8)
    pend = svc._pending(svc._patient("ORTHO_002"))
    assert pend.index("ECG") == pend.index("CONSULT") - 1                       # congested ECG goes to the back


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
    demo.s8(svc)
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
    demo.s9(svc)
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
    """The table as built: square corners are nodes where the robot turns; START has no tape bar."""
    rp = table_planner()
    k = rp.map["node_kind"]
    assert k["J_L"] == "junction" and k["S1"] == "station" and k["C_BL"] == "junction" and k["START"] == "waypoint"
    r = rp.plan_to_node("START", "S3", "CONSULT")                       # robot faces left at START
    assert r.nodes == ["START", "C_BL", "S3"] and r.start_action == "GO" and r.scan_side == "RIGHT"
    assert feats(r) == [("C_BL", "R", "RIGHT"), ("S3", "C", "STOP")]
    assert r.marks[0]["kind"] == "CORNER" and r.marks[0]["dist_cm"] == pytest.approx(25.0)
    assert r.marks[1]["dist_cm"] == pytest.approx(14.0)
    r = rp.plan_to_node("START", "S1", "XRAY")                          # behind it -> turn round first
    assert r.start_action == "U_TURN" and r.nodes == ["START", "C_BR", "J_R", "S1"] and r.scan_side == "LEFT"
    assert feats(r) == [("C_BR", "L", "LEFT"), ("J_R", "L", "PASS"), ("S1", "C", "STOP")]
    r = rp.plan_to_node("S3", "S2", "XRAY", heading_from="C_BL")         # scene 8: past the left T, round the corner
    assert r.start_action == "GO" and r.nodes == ["S3", "J_L", "C_TL", "S2"] and r.scan_side == "RIGHT"
    assert feats(r) == [("J_L", "R", "PASS"), ("C_TL", "R", "RIGHT"), ("S2", "C", "STOP")]
    r = rp.plan_to_node("S2", "S1", "XRAY", heading_from="C_TL")         # re-guide to the other X-ray room
    assert feats(r) == [("C_TR", "R", "RIGHT"), ("S1", "C", "STOP")] and r.scan_side == "RIGHT"


def test_double_loop_blocked_uses_middle_corridor():
    rp = table_planner()
    ev = []
    a = ESP32Adapter("EDi-01", on_event=lambda k, d: ev.append((k, d)), nodes=rp.map["nodes"])
    a.navigate("P", "XRAY", "T1", rp.plan_to_node("START", "S1", "XRAY"))
    a.handle_status({"task_id": "T1", "event": "PATH_BLOCKED", "marks_passed": 0, "turned_around": True})
    d = [d for k, d in ev if k == "PATH_BLOCKED"][0]
    assert d["edge"] == ["START", "C_BR"] and d["mid_segment"]
    rp.block(*d["edge"])
    r = rp.plan_to_node("START", "S1", "XRAY", heading_from=d["heading_from"], mid_segment=True)
    # junction_turn_cost_m: the outer loop (corner turns only) beats turning at the two T-junctions
    assert r.nodes == ["START", "C_BL", "S3", "J_L", "C_TL", "S2", "C_TR", "S1"] and r.start_action == "GO"
    assert feats(r) == [("C_BL", "R", "RIGHT"), ("S3", "C", "PASS"), ("J_L", "R", "PASS"), ("C_TL", "R", "RIGHT"),
                        ("S2", "C", "PASS"), ("C_TR", "R", "RIGHT"), ("S1", "C", "STOP")]
    # START has no tape bar: 25 cm to the corner PLUS however far past START the robot was (it adds that)
    assert r.marks[0]["dist_cm"] == -25.0 and r.marks[0]["max_cm"] > 25 + 25
    rp.block("J_L", "J_R")                                              # middle corridor too -> over the top
    r = rp.plan_to_node("START", "S1", "XRAY", heading_from="C_BR", mid_segment=True)
    assert r.nodes == ["START", "C_BL", "S3", "J_L", "C_TL", "S2", "C_TR", "S1"]
    rp.block("S2", "C_TL")
    with pytest.raises(MapError):
        rp.plan_to_node("START", "S1", "XRAY", heading_from="C_BR", mid_segment=True)


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
    assert r.start_action == "GO" and feats(r)[0] == ("C_BL", "R", "RIGHT") and r.marks[0]["dist_cm"] == 25.0


def test_table_command_contract():
    rp = table_planner()
    a = ESP32Adapter("EDi-01", nodes=rp.map["nodes"])
    r = rp.plan_to_node("START", "S3", "CONSULT"); r.room_id = "ROOM_A"
    a.navigate("P", "CONSULT", "T1", r)
    c = a.poll_command()
    assert c["start_action"] == "GO" and c["scan_room_on_arrival"] and c["scan_side"] == "RIGHT"
    assert [(f["t"], f["p"], f["a"]) for f in c["features"]] == [("K", "R", "RIGHT"), ("S", "C", "STOP")]
    assert c["features"][0]["d"] == 25.0 and c["features"][1]["d"] == 14.0 and c["features"][1]["m"] == 22.0


@pytest.fixture
def table_svc():
    s = SynapseService(db_path=":memory:", predictor=PRED)
    s.planner = table_planner()
    s.reset()
    yield s
    s.stop()


def test_table_scenes_8_and_9(table_svc):
    svc = table_svc
    demo.s8(svc)
    assert svc._patient("ORTHO_201")["assigned_room"] == "ROOM_C"
    _drive(svc)
    p = svc._patient("ORTHO_201")
    assert p["status"] == "QUEUED" and p["assigned_room"] == "ROOM_B"
    assert svc.robot_heading["EDi-01"] == "C_TR"                      # came round the top-right corner
    demo.s9(svc)
    _drive(svc)
    p = svc._patient("ORTHO_201")
    assert p["status"] == "QUEUED" and p["assigned_room"] == "ROOM_B"
    assert ("C_TL", "J_L") in svc.planner.blocked                       # went through the middle corridor instead
    types = [e["type"] for e in svc.db.query("SELECT type FROM events")]
    assert "CORRIDOR_BLOCKED" in types and "REROUTED" in types


def test_table_demo_story(table_svc):
    """Scenes 1-5: Mr Tan + Mdm Lee, EDi-01 on the table, EDi-02 virtual."""
    svc = table_svc
    st = lambda pid: svc._patient(pid)
    demo.scene(svc, 1); _drive(svc)
    assert st("ORTHO_001")["status"] == "QUEUED" and st("ORTHO_001")["assigned_room"] == "ROOM_A"
    demo.scene(svc, 2)
    assert st("ORTHO_101")["assigned_room"] == "ROOM_B" and "ROOM_B" in svc.world["people"]
    v = [r for r in svc.floor_map()["robots"] if r["robot_id"] == "EDi-02"]
    assert v and v[0]["virtual"] and v[0]["route"][-1] == "S2"            # EDi-02 drawn walking her there
    demo.scene(svc, 3)
    assert svc._patient("ORTHO_101")["status"] == "IN_SERVICE"            # clicking 3 started her X-ray
    assert st("ORTHO_001")["assigned_room"] == "ROOM_C"                 # X-ray Room 1 occupied: to Room 2...
    assert svc.adapters["EDi-01"].current.route.pauses == {"S2": 2.0}   # ...stopping 2 s at Room 1 on the way
    _drive(svc)
    assert st("ORTHO_001")["status"] == "QUEUED" and st("ORTHO_001")["assigned_room"] == "ROOM_C"
    demo.scene(svc, 4)
    assert st("ORTHO_101")["status"] == "GUIDED" and st("ORTHO_101")["assigned_destination"] == "CONSULT"
    assert "S2" in svc.adapters["EDi-01"].current.route.nodes           # passes X-ray Room 1, where she is
    _drive(svc)
    assert st("ORTHO_101")["status"] == "QUEUED" and st("ORTHO_101")["assigned_room"] == "ROOM_A"
    assert st("ORTHO_101")["robot_id"] == "EDi-01"
    demo.scene(svc, 5)
    assert st("ORTHO_001")["robot_id"] is None and svc.eligible(st("ORTHO_001")) == ["REVIEW"]
    svc.assign_robot("EDi-02", "ORTHO_001")                              # he types 1001 on EDi-02
    assert svc.robot_session("EDi-02")["journey"]["patient"]["completed_steps"] == ["REGISTRATION", "CONSULT", "XRAY"]
    fm = svc.floor_map()
    assert {p["patient_id"]: p["room_id"] for p in fm["patients"]}["ORTHO_101"] == "ROOM_A"


def test_table_scene10_moving_the_obstacle(table_svc):
    """Scene 10: blocked between Station 3 and Station 2 -> middle corridor; move the trolley onto the
    middle corridor -> the robot turns round again and takes the first route, now clear."""
    svc = table_svc
    demo.scene(svc, 10)
    a, moved, n = svc.adapters["EDi-01"], False, 0
    for _ in range(1200):
        a.tick(0.5)
        t = a.current
        if not moved and t and t.route and t.route.nodes[0] == "J_L":
            n += 1
            if n == 4:
                svc.set_obstacle("J_R", "J_L", True, exclusive=True)
                moved = True
        if a.get_robot_status().state.value == "ARRIVED":
            break
    assert moved and svc._patient("ORTHO_201")["assigned_room"] == "ROOM_B"
    assert svc.world["obstacles"] == {("J_L", "J_R")} and svc.robot_heading["EDi-01"] == "C_TL"


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


def test_assistant_uses_live_journey(table_svc, monkeypatch):
    """Claude assistant: off without a key; with one, it sends the live journey and returns the reply."""
    from backend import assistant
    svc = table_svc
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(assistant, "KEY_FILE", assistant.ROOT / "no_such_key_file")
    assert assistant.ask(svc, "EDi-01", "hi", "en", [], [])["reason"] == "not_configured"
    demo.scene(svc, 1)
    sent = {}

    def fake_post(url, headers, body, timeout):
        sent.update(body)
        return 200, json.dumps({"content": [{"type": "text", "text": "Your next stop is Consultation Room 1."}]})

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(assistant, "_post", fake_post)
    out = assistant.ask(svc, "EDi-01", "where do I go?", "en", ["The toilets are next to Registration."],
                        [{"who": "you", "text": "hello"}, {"who": "me", "text": "Hello!"}])
    assert out["ok"] and "Consultation" in out["reply"]
    assert '"queue_number": "1001"' in sent["system"] and "Mr Tan" not in sent["system"]      # queue number only
    assert sent["messages"][-1] == {"role": "user", "content": "where do I go?"}
    assert [m["role"] for m in sent["messages"]] == ["user", "assistant", "user"]


def test_mark_arrived_uses_demo_world_for_the_scan():
    """Presenter 'Mark arrived' for a real robot that did not report: the room is as the demo world has it."""
    from backend.service import SynapseService
    svc = SynapseService(db_path=":memory:", predictor=PRED)
    svc.planner = table_planner()
    svc.adapters["EDi-01"] = ESP32Adapter("EDi-01", on_event=svc._robot_event, nodes=svc.planner.map["nodes"])
    svc.reset()
    svc.adapters["EDi-01"] = ESP32Adapter("EDi-01", on_event=svc._robot_event, nodes=svc.planner.map["nodes"])
    svc.update_room("ROOM_B", person_present=True)
    svc.check_in({**demo.XRAY_PATIENT, "robot_id": "EDi-01"})
    t = svc.adapters["EDi-01"].current
    assert t and t.route.room_id == "ROOM_C"
    svc.update_room("ROOM_C", person_present=True)
    svc.robot_command("EDi-01", "report_arrival", t.task_id)
    rooms = {r["room_id"]: r for r in svc.db.query("SELECT * FROM rooms")}
    assert rooms["ROOM_C"]["occupied"] == 1                                    # scan taken from the demo world
    svc.stop()


def test_table_demo_story_with_manual_buttons(table_svc):
    """Staff also press Start service / Add X-ray / Complete X-ray by hand between scenes: no errors."""
    svc, A, B = table_svc, "ORTHO_001", "ORTHO_101"
    demo.scene(svc, 1); _drive(svc)
    svc.start_service(A)
    svc.change_orders(A, add=["XRAY"])                                   # ordered during the consultation
    assert svc._patient(A)["required_steps"] == ["REGISTRATION", "CONSULT", "XRAY", "REVIEW", "PAYMENT"]
    demo.scene(svc, 2)
    v = [r for r in svc.floor_map()["robots"] if r["robot_id"] == "EDi-02"][0]
    assert v["state"] == "NAVIGATING"                                    # EDi-02 parks short until Mark arrived
    svc.arrive_at_station(B)                                             # Mark arrived
    svc.start_service(B)                                                 # system now knows X-ray Room 1 is busy
    demo.scene(svc, 3); _drive(svc)
    assert svc._patient(A)["assigned_room"] == "ROOM_C"
    svc.complete_service(B, "XRAY")
    demo.scene(svc, 4); _drive(svc)
    assert svc._patient(B)["status"] == "QUEUED" and svc._patient(B)["assigned_room"] == "ROOM_A"
    svc.complete_service(A, "XRAY")                                      # scene 4 already started his X-ray
    demo.scene(svc, 5)
    assert svc.eligible(svc._patient(A)) == ["REVIEW"]


def test_staff_drag_order_is_followed(svc):
    svc.check_in(pre_op())
    j = svc.reorder_steps("T1", ["ECG", "XRAY", "PHLEBOTOMY", "CONSULT", "PAYMENT"])
    assert j["eligible_now"] == ["ECG"]                                        # router follows staff's order
    assert svc._patient("T1")["recommendation"]["recommended_station"] == "ECG"
    with pytest.raises(ServiceError):                                          # cannot cross the consultation
        svc.reorder_steps("T1", ["ECG", "XRAY", "CONSULT", "PHLEBOTOMY", "PAYMENT"])
    with pytest.raises(ServiceError):                                          # rules still apply
        svc.reorder_steps("T1", ["ECG", "XRAY", "PHLEBOTOMY", "PAYMENT", "CONSULT"])
    j = svc.reorder_steps("T1", None, unlock=True)
    assert len(j["eligible_now"]) == 3


def test_nurse_priority_and_queue_order(svc):
    for pid in ("T1", "T2"):
        svc.check_in(pre_op(pid))
        svc.confirm_destination(pid, "XRAY")
        svc.arrive_at_station(pid)
    assert [x["patient_id"] for x in svc.suggested_queue("XRAY")] == ["T1", "T2"]       # first come
    svc.set_priority("T2", "expedited")
    assert svc.queue_order("XRAY") == ["T2", "T1"]                                      # staff priority first
    assert svc.journey("T1")["patients_ahead"] == svc._station("XRAY")["background_queue"] + 1
    svc.set_queue_order("XRAY", ["T1", "T2"])                                           # nurse overrides
    assert svc.queue_order("XRAY") == ["T1", "T2"]
    assert any(q["station_id"] == "XRAY" and q["manual"] for q in svc.dashboard_state()["station_queues"])
    svc.set_queue_order("XRAY", [])
    assert svc.queue_order("XRAY") == ["T2", "T1"]


def test_xray_added_while_walking_to_consult_is_a_doctors_order(table_svc):
    svc, A = table_svc, "ORTHO_001"
    demo.scene(svc, 1)                                                   # EDi-01 still driving him there
    svc.change_orders(A, add=["XRAY"])
    assert svc._patient(A)["required_steps"] == ["REGISTRATION", "CONSULT", "XRAY", "REVIEW", "PAYMENT"]
    _drive(svc)
    demo.scene(svc, 2); demo.scene(svc, 3)                               # no R07 error
    assert svc._patient(A)["assigned_destination"] == "XRAY"


def test_staff_mark_arrived_also_lands_the_robot():
    """Real robot did not report (Wi-Fi off while driving): staff press Mark arrived on the patient."""
    svc = SynapseService(db_path=":memory:", predictor=PRED)
    svc.planner = table_planner()
    svc.reset()
    svc.adapters["EDi-01"] = ESP32Adapter("EDi-01", on_event=svc._robot_event, nodes=svc.planner.map["nodes"])
    demo.scene(svc, 1)
    a = svc.adapters["EDi-01"]
    assert a.current and a.current.state.value == "NAVIGATING"
    svc.arrive_at_station("ORTHO_001")
    assert svc._patient("ORTHO_001")["status"] == "QUEUED"
    assert a.get_robot_status().state.value == "ARRIVED" and a.get_robot_status().position == "S3"
    svc.stop()


def test_esp32_story_with_staff_confirmations(monkeypatch):
    """Real robot that never reports (Wi-Fi off): staff confirm arrivals; scene 4 collects Mdm Lee first."""
    from backend import service as SV
    monkeypatch.setitem(SV.CONFIG["robots"]["EDi-01"], "adapter", "esp32")
    svc = SV.SynapseService(db_path=":memory:", predictor=PRED)
    svc.planner = table_planner()
    svc.reset()
    A, B = "ORTHO_001", "ORTHO_101"
    demo.scene(svc, 1)
    svc.adapters["EDi-01"].poll_command()
    assert svc.floor_map()["robots"][0]["position"].get("estimated")             # moving dot while it drives
    svc.change_orders(A, add=["PHLEBOTOMY"])                                       # tests can be doctor's orders
    assert svc._patient(A)["required_steps"].index("PHLEBOTOMY") > svc._patient(A)["required_steps"].index("CONSULT")
    svc.change_orders(A, remove=["PHLEBOTOMY"])
    svc.arrive_at_station(A)
    demo.scene(svc, 2); demo.scene(svc, 3)
    a = svc.adapters["EDi-01"]
    assert svc._patient(A)["assigned_room"] == "ROOM_C"                            # Room 1 occupied (her X-ray)
    demo.scene(svc, 4)                                                             # lands EDi-01 at Room 2 first
    assert svc._patient(B)["status"] == "GUIDED" and "S2" in a.current.route.nodes
    svc.arrive_at_station(B)                                                       # staff: EDi-01 brought her
    p = svc._patient(B)
    assert p["status"] == "QUEUED" and p["assigned_destination"] == "CONSULT" and p["assigned_room"] == "ROOM_A"
    assert a.get_robot_status().position == "S3"
    svc.stop()


def test_floor_map_keeps_patients_and_draws_only_the_route_ahead(table_svc):
    svc = table_svc
    demo.scene(svc, 1)
    for _ in range(6):
        svc.adapters["EDi-01"].tick(0.5)
    r = svc.floor_map()["robots"][0]
    assert r["ahead"][0] == {"x": r["position"]["x"], "y": r["position"]["y"]} and "START" not in str(r["ahead"][1:2])
    _drive(svc)
    demo.scene(svc, 2); demo.scene(svc, 3); _drive(svc); demo.scene(svc, 4); _drive(svc); demo.scene(svc, 5)
    rooms = {p["patient_id"]: p["room_id"] for p in svc.floor_map()["patients"]}
    assert rooms == {"ORTHO_001": "ROOM_C", "ORTHO_101": "ROOM_A"}                 # both still on the map


def test_stress_scenes_push_the_stressed_test_back(table_svc):
    svc = table_svc
    demo.scene(svc, 7)
    assert svc._patient("ORTHO_002")["robot_id"] == "EDi-02"
    assert any(r["robot_id"] == "EDi-02" for r in svc.floor_map()["robots"])      # her walk is on the map
    demo.scene(svc, 8)
    pend = svc._pending(svc._patient("ORTHO_002"))
    assert pend.index("ECG") == pend.index("CONSULT") - 1                        # ECG last of her tests
    demo.scene(svc, 9)
    pend = svc._pending(svc._patient("ORTHO_002"))
    assert pend.index("PHLEBOTOMY") == pend.index("CONSULT") - 1                 # blood test now last


def test_esp32_map_estimate_respects_the_trolley(monkeypatch):
    """Scene 10 with the real-robot adapter: the map ball stops at the trolley, then is re-routed."""
    import types
    import navigation.esp32_adapter as E
    from backend import service as SV
    clock = [1000.0]
    monkeypatch.setattr(E, "time", types.SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setitem(SV.CONFIG["robots"]["EDi-01"], "adapter", "esp32")
    svc = SV.SynapseService(db_path=":memory:", predictor=PRED)
    svc.planner = table_planner()
    svc.reset()
    demo.scene(svc, 10)
    a = svc.adapters["EDi-01"]
    for _ in range(40):
        clock[0] += 0.5
        a.tick(0.5)
    assert a.current.route.nodes[:2] == ["J_L", "J_R"]                          # turned round, middle corridor
    assert ("C_TL", "J_L") in svc.planner.blocked
    svc.stop()
