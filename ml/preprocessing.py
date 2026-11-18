"""Shared feature definitions, chronological splits and preprocessing pipelines.

Every feature listed here must be computable by the backend at the moment a patient
joins a queue (Model A) or at a snapshot time (Model B). See reports/phase1_dataset_audit.md.
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd
import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler
from sklearn.impute import SimpleImputer

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "original"
MODELS = ROOT / "ml" / "models"
REPORTS = ROOT / "reports"
FIGS = REPORTS / "figures"

TRAIN_END, VAL_END = "2025-08-01", "2025-10-01"   # train < Aug; val Aug-Sep; test Oct-Dec

# ---------------------------------------------------------------- Model A (waiting time)
WAIT_TARGET = "wait_min"
WAIT_CAT = ["station", "visit_type", "referral_category", "clinician_priority", "mobility"]
WAIT_NUM = [
    "age", "join_hour", "day_of_week", "step_index", "is_review", "minutes_since_clinic_arrival",
    "station_open", "queue_length_at_join", "servers_on_duty", "servers_busy",
    "recent_mean_wait_30min", "arrivals_last_15min", "consult_completions_last_15min",
    "total_consult_queue",
    # consultation-only (blank for other stations)
    "patients_ahead_for_doctor", "doctor_present", "doctor_running_delay_min", "minutes_to_appointment",
]
WAIT_FEATURES = WAIT_CAT + WAIT_NUM
WAIT_FORBIDDEN = ["start_time", "end_time", "service_min", "server_id", "event_id", "visit_id", "patient_id",
                  "synthetic", "join_time", "date", "month", "doctor_id", WAIT_TARGET]

# ---------------------------------------------------------------- Model B (bottleneck)
BN_TARGET = "bottleneck_next_30min"
BN_CAT = ["station"]
BN_NUM = ["time_of_day_min", "day_of_week", "queue_length", "servers_on_duty", "servers_busy",
          "recent_mean_wait_30min", "arrivals_last_15min", "consult_completions_last_15min",
          "booked_appointments_next_30min"]
BN_FEATURES = BN_CAT + BN_NUM
BN_FORBIDDEN = ["future_n_joiners_30min", "future_mean_wait_30min", "bottleneck_threshold_min",
                "snapshot_time", "date", "month", "synthetic", BN_TARGET]

CATEGORIES = {
    "station": ["REGISTRATION", "XRAY", "PHLEBOTOMY", "ECG", "CONSULT", "CAST_ROOM", "DRESSING",
                "PROCEDURE", "PAYMENT", "PHARMACY"],
    "visit_type": ["new", "follow_up", "pre_op"],
    "referral_category": ["Knee/Hip", "Spine/Back", "Shoulder/Elbow", "Hand/Wrist", "Foot/Ankle",
                          "Fracture", "Post-op"],
    "clinician_priority": ["routine", "expedited"],
    "mobility": ["independent", "walking_aid", "wheelchair"],
}


def split_labels(dates: pd.Series) -> pd.Series:
    return np.where(dates < TRAIN_END, "train", np.where(dates < VAL_END, "val", "test"))


def load_events(columns: list[str] | None = None) -> pd.DataFrame:
    """columns: load only these (plus date) to save memory, e.g. on a small cloud instance."""
    usecols = list(dict.fromkeys((columns or []) + ["date"])) if columns else None
    df = pd.read_csv(DATA / "ortho_service_events.csv", usecols=usecols,
                     dtype={c: "category" for c in WAIT_CAT} if columns else None)
    df["split"] = split_labels(df["date"])
    return df


def load_snapshots() -> pd.DataFrame:
    df = pd.read_csv(DATA / "ortho_station_snapshots.csv")
    t = pd.to_datetime(df["snapshot_time"])
    df["time_of_day_min"] = t.dt.hour * 60 + t.dt.minute
    df["split"] = split_labels(df["date"])
    return df


def as_categorical(X: pd.DataFrame, cats: list[str]) -> pd.DataFrame:
    X = X.copy()
    for c in cats:
        X[c] = pd.Categorical(X[c], categories=CATEGORIES[c])
    return X


def assert_no_leakage(features: list[str], forbidden: list[str]):
    bad = set(features) & set(forbidden)
    if bad:
        raise ValueError(f"Forbidden columns in features: {bad}")


def linear_preprocessor(cat, num):
    """One-hot + median impute + missing indicators + scaling (for Ridge / LogisticRegression)."""
    return ColumnTransformer([
        ("cat", OneHotEncoder(categories=[CATEGORIES[c] for c in cat], handle_unknown="ignore"), cat),
        ("num", Pipeline([("imp", SimpleImputer(strategy="median", add_indicator=True)),
                          ("sc", StandardScaler())]), num),
    ])


def tree_preprocessor(cat, num):
    """Ordinal-encode categories; leave NaN (RandomForest in sklearn>=1.4 handles missing values)."""
    return ColumnTransformer([
        ("cat", OrdinalEncoder(categories=[CATEGORIES[c] for c in cat], handle_unknown="use_encoded_value",
                               unknown_value=-1), cat),
        ("num", "passthrough", num),
    ])
