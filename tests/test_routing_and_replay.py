"""Routing safety and replay determinism.  Run:  python -m pytest -q"""
import math
import pytest

from flow.predictor import Predictor, PredictionInputError
from flow.routing_engine import RoutingEngine, StationView, PatientContext, ClinicContext
from flow import replay_simulator as rs


@pytest.fixture(scope="module")
def engine():
    return RoutingEngine(Predictor())


def stations(**over):
    base = {s: StationView(s, servers_on_duty=4, queue_length=2, servers_busy=2, recent_mean_wait_30min=4.0)
            for s in ["REGISTRATION", "XRAY", "PHLEBOTOMY", "ECG", "CONSULT", "PAYMENT", "PHARMACY", "CAST_ROOM"]}
    base["ECG"] = StationView("ECG", servers_on_duty=1, queue_length=1, servers_busy=1, recent_mean_wait_30min=8.0)
    base.update(over)
    return base


def pre_op(**kw):
    d = dict(patient_id="ORTHO_001", visit_type="pre_op", referral_category="Knee/Hip", clinician_priority="routine",
             mobility="independent", age=66,
             required_steps=["REGISTRATION", "XRAY", "PHLEBOTOMY", "ECG", "CONSULT", "PAYMENT"],
             completed_steps=["REGISTRATION"], minutes_since_clinic_arrival=5)
    d.update(kw)
    return PatientContext(**d)


CLINIC = ClinicContext(now_min=600, day_of_week=1, consult_completions_last_15min=6, total_consult_queue=10)


def test_pre_op_gets_ml_recommendation_among_permitted_tests(engine):
    r = engine.recommend(pre_op(), stations(), CLINIC)
    assert r["status"] == "ROUTE" and r["decision_source"] == "ml+rules"
    assert r["recommended_station"] in {"XRAY", "PHLEBOTOMY", "ECG"}
    assert sorted(r["recommended_sequence"]) == ["ECG", "PHLEBOTOMY", "XRAY"]
    assert {c["step"] for c in r["candidates"]} == {"XRAY", "PHLEBOTOMY", "ECG"}
    assert all(c["predicted_wait_min"] >= 0 for c in r["candidates"])
    assert "explanation" in r and "ML predictions" in r["explanation"]


def test_congested_station_not_recommended_first(engine):
    jam = StationView("XRAY", servers_on_duty=4, queue_length=40, servers_busy=4, recent_mean_wait_30min=60.0,
                      arrivals_last_15min=15)
    r = engine.recommend(pre_op(), stations(XRAY=jam), CLINIC)
    xray = next(c for c in r["candidates"] if c["step"] == "XRAY")
    assert xray["predicted_wait_min"] > 30 and xray["alert_level"] in ("MEDIUM", "HIGH")


def test_follow_up_never_routed_to_consult_before_xray(engine):
    p = pre_op(visit_type="follow_up", required_steps=["REGISTRATION", "XRAY", "CONSULT", "PAYMENT"])
    empty_consult = StationView("CONSULT", servers_on_duty=1, queue_length=0, servers_busy=0)
    r = engine.recommend(p, stations(CONSULT=empty_consult), CLINIC)
    assert r["recommended_station"] == "XRAY"
    assert all(c["step"] != "CONSULT" for c in r["candidates"])


def test_unavailable_room_excluded_and_all_unavailable_waits(engine):
    r = engine.recommend(pre_op(), stations(PHLEBOTOMY=StationView("PHLEBOTOMY", available=False)), CLINIC)
    assert r["recommended_station"] != "PHLEBOTOMY"
    assert any("unavailable" in c for c in r["rule_checks"])
    p = pre_op(visit_type="follow_up", required_steps=["REGISTRATION", "XRAY", "CONSULT", "PAYMENT"])
    r = engine.recommend(p, stations(XRAY=StationView("XRAY", available=False)), CLINIC)
    assert r["status"] == "WAIT_UNAVAILABLE" and r["recommended_station"] is None


def test_escalated_patient_is_not_routed(engine):
    r = engine.recommend(pre_op(escalated=True), stations(), CLINIC)
    assert r["status"] == "ESCALATED" and r["recommended_station"] is None


def test_locked_destination_not_rerouted(engine):
    jam = StationView("ECG", servers_on_duty=1, queue_length=30, servers_busy=1, recent_mean_wait_30min=90.0)
    r = engine.recommend(pre_op(locked_destination="ECG"), stations(ECG=jam), CLINIC)
    assert r["recommended_station"] == "ECG" and r["changed"] is False


def test_hysteresis_keeps_previous_for_marginal_gain(engine):
    r0 = engine.recommend(pre_op(), stations(), CLINIC)
    other = next(c["step"] for c in r0["candidates"] if c["step"] != r0["recommended_station"])
    big = RoutingEngine(engine.predictor, hysteresis_min=1000)
    r1 = big.recommend(pre_op(previous_recommendation=other), stations(), CLINIC)
    assert r1["recommended_station"] == other and r1["changed"] is False


def test_journey_complete(engine):
    steps = ["REGISTRATION", "CONSULT", "PAYMENT"]
    r = engine.recommend(pre_op(visit_type="follow_up", required_steps=steps, completed_steps=steps), stations(), CLINIC)
    assert r["status"] == "COMPLETE"


def test_predictor_rejects_invalid_input(engine):
    with pytest.raises(PredictionInputError):
        engine.predictor.predict_wait([{"station": "MRI"}])
    row = engine.wait_row(pre_op(), "XRAY", stations()["XRAY"], CLINIC)
    row["queue_length_at_join"] = -1
    with pytest.raises(PredictionInputError):
        engine.predictor.predict_wait([row])


# ---------------------------------------------------------------- replay
@pytest.fixture(scope="module")
def inputs():
    return rs.load_inputs("2025-10-01", "2025-10-02")


def _day(inputs, policy, engine=None):
    ap, _, dur, doc_open, servers = inputs
    day = sorted(ap.date.unique())[0]
    visits = ap[ap.date == day].to_dict("records")
    return rs.simulate_day(day, visits, dur, doc_open, servers, policy, engine, rs.WorkflowRules.load())


def test_replay_is_deterministic(inputs):
    a, _ = _day(inputs, "fixed"); b, _ = _day(inputs, "fixed")
    assert [x["total_time"] for x in a] == [x["total_time"] for x in b]


def test_fixed_policy_makes_no_changes_and_all_finish(inputs):
    v, _ = _day(inputs, "fixed")
    assert sum(x["route_changes"] for x in v) == 0
    assert all(not math.isnan(x["total_time"]) and x["total_time"] > 0 for x in v)


def test_synapse_only_reorders_flexible_visits(inputs, engine):
    v, _ = _day(inputs, "synapse", engine)
    assert all(x["flexible"] == 1 for x in v if x["route_changes"] > 0)
    assert all(not math.isnan(x["total_time"]) for x in v)
