"""Run:  python -m pytest -q"""
import joblib
import numpy as np
import pandas as pd
import pytest

from ml.preprocessing import (WAIT_FEATURES, WAIT_FORBIDDEN, BN_FEATURES, BN_FORBIDDEN, MODELS, DATA,
                              assert_no_leakage, split_labels, as_categorical, WAIT_CAT, BN_CAT)
from flow.dependencies import WorkflowRules, eligible_next, remaining_stages

RULES = WorkflowRules.load()


# ---------------------------------------------------------------- leakage & splits
def test_feature_lists_exclude_forbidden_columns():
    assert_no_leakage(WAIT_FEATURES, WAIT_FORBIDDEN)
    assert_no_leakage(BN_FEATURES, BN_FORBIDDEN)
    for col in ["start_time", "end_time", "service_min", "server_id", "wait_min"]:
        assert col not in WAIT_FEATURES
    for col in ["future_n_joiners_30min", "future_mean_wait_30min", "bottleneck_next_30min"]:
        assert col not in BN_FEATURES


def test_leakage_guard_raises():
    with pytest.raises(ValueError):
        assert_no_leakage(WAIT_FEATURES + ["service_min"], WAIT_FORBIDDEN)


def test_chronological_split_boundaries():
    s = split_labels(pd.Series(["2025-07-31", "2025-08-01", "2025-09-30", "2025-10-01"]))
    assert list(s) == ["train", "val", "val", "test"]


def test_no_visit_spans_splits():
    ev = pd.read_csv(DATA / "ortho_service_events.csv", usecols=["visit_id", "date"])
    ev["split"] = split_labels(ev.date)
    assert ev.groupby("visit_id").split.nunique().max() == 1


# ---------------------------------------------------------------- saved models
@pytest.fixture(scope="module")
def wait_bundle():
    return joblib.load(MODELS / "waiting_time_model.joblib")


@pytest.fixture(scope="module")
def bn_bundle():
    return joblib.load(MODELS / "bottleneck_model.joblib")


def _wait_row(**kw):
    row = dict(station="XRAY", visit_type="pre_op", referral_category="Knee/Hip", clinician_priority="routine",
               mobility="independent", age=65, join_hour=10, day_of_week=1, step_index=1, is_review=0,
               minutes_since_clinic_arrival=5, station_open=1, queue_length_at_join=2, servers_on_duty=4,
               servers_busy=4, recent_mean_wait_30min=5.0, arrivals_last_15min=4,
               consult_completions_last_15min=6, total_consult_queue=10, patients_ahead_for_doctor=np.nan,
               doctor_present=np.nan, doctor_running_delay_min=np.nan, minutes_to_appointment=np.nan)
    row.update(kw)
    return as_categorical(pd.DataFrame([row])[WAIT_FEATURES], WAIT_CAT)


def test_waiting_model_loads_and_predicts(wait_bundle):
    p = wait_bundle["model"].predict(_wait_row())[0]
    assert np.isfinite(p)


def test_waiting_prediction_increases_with_queue(wait_bundle):
    short = wait_bundle["model"].predict(_wait_row(queue_length_at_join=1))[0]
    long = wait_bundle["model"].predict(_wait_row(queue_length_at_join=25, recent_mean_wait_30min=40))[0]
    assert long > short + 10


def test_waiting_model_beats_baseline():
    comp = pd.read_csv(MODELS.parent.parent / "reports" / "model_a_comparison.csv")
    t = comp[comp.split == "test"].set_index("model")
    assert t.loc["HistGradientBoosting", "MAE"] < t.loc["Baseline: station x hour median", "MAE"]


def test_bottleneck_model_probability_and_alert(bn_bundle):
    row = dict(station="PHARMACY", time_of_day_min=720, day_of_week=0, queue_length=40, servers_on_duty=7,
               servers_busy=7, recent_mean_wait_30min=35.0, arrivals_last_15min=15,
               consult_completions_last_15min=8, booked_appointments_next_30min=np.nan)
    X = as_categorical(pd.DataFrame([row])[BN_FEATURES], BN_CAT)
    p = bn_bundle["model"].predict_proba(X)[0, 1]
    assert 0 <= p <= 1 and p >= bn_bundle["threshold"]
    assert bn_bundle["alert_levels"]["MEDIUM"] < bn_bundle["alert_levels"]["HIGH"]


# ---------------------------------------------------------------- workflow rules
def test_pre_op_tests_are_order_flexible():
    st = RULES.build_stages(["REGISTRATION", "XRAY", "PHLEBOTOMY", "ECG", "CONSULT", "PAYMENT"], "pre_op")
    assert st[1] == ["XRAY", "PHLEBOTOMY", "ECG"]
    assert set(eligible_next(st, ["REGISTRATION"])) == {"XRAY", "PHLEBOTOMY", "ECG"}
    assert set(eligible_next(st, ["REGISTRATION", "PHLEBOTOMY"])) == {"XRAY", "ECG"}


def test_follow_up_xray_required_before_consult():
    st = RULES.build_stages(["REGISTRATION", "XRAY", "CONSULT", "PAYMENT"], "follow_up")
    assert eligible_next(st, ["REGISTRATION"]) == ["XRAY"]          # never straight to CONSULT
    assert eligible_next(st, ["REGISTRATION", "XRAY"]) == ["CONSULT"]


def test_follow_up_two_tests_flexible_by_team_decision():
    st = RULES.build_stages(["REGISTRATION", "XRAY", "PHLEBOTOMY", "CONSULT", "PAYMENT"], "follow_up")
    assert set(eligible_next(st, ["REGISTRATION"])) == {"XRAY", "PHLEBOTOMY"}


def test_post_consult_xray_then_review_is_sequential():
    steps = ["REGISTRATION", "CONSULT", "XRAY", "REVIEW", "CAST_ROOM", "PAYMENT", "PHARMACY"]
    st = RULES.build_stages(steps, "new")
    done = ["REGISTRATION", "CONSULT"]
    assert eligible_next(st, done) == ["XRAY"]
    assert eligible_next(st, done + ["XRAY"]) == ["REVIEW"]
    assert eligible_next(st, steps) == []
    assert remaining_stages(st, done) == [["XRAY"], ["REVIEW"], ["CAST_ROOM"], ["PAYMENT"], ["PHARMACY"]]


@pytest.mark.parametrize("bad", [
    ["CONSULT", "PAYMENT"],                                  # no registration
    ["REGISTRATION", "XRAY", "PAYMENT"],                     # no consult
    ["REGISTRATION", "CONSULT", "REVIEW", "PAYMENT"],         # review without X-ray
    ["REGISTRATION", "CONSULT", "PHARMACY", "PAYMENT"],       # pharmacy before payment
])
def test_invalid_journeys_rejected(bad):
    with pytest.raises(ValueError):
        RULES.validate(bad)


def test_every_dataset_journey_is_valid():
    ap = pd.read_csv(DATA / "ortho_appointments.csv", usecols=["planned_steps", "visit_type"])
    for steps, vt in ap[["planned_steps", "visit_type"]].drop_duplicates().itertuples(index=False):
        RULES.build_stages(steps.split("|"), vt)
