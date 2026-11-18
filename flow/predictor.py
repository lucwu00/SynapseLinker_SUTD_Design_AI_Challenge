"""Model inference wrapper shared by the routing engine, the replay simulator and the backend.

Validates inputs, fills optional fields with NaN (never zero), and returns predictions with
model versions. This is the only place the trained models are called.
"""
from __future__ import annotations
import math
from pathlib import Path
import joblib
import numpy as np
import pandas as pd

from ml.preprocessing import MODELS, CATEGORIES, as_categorical

WAIT_REQUIRED = ["station", "visit_type", "referral_category", "clinician_priority", "mobility",
                 "join_hour", "day_of_week", "queue_length_at_join", "servers_on_duty", "servers_busy"]
BN_REQUIRED = ["station", "time_of_day_min", "day_of_week", "queue_length", "servers_on_duty", "servers_busy"]


class PredictionInputError(ValueError):
    pass


def _check(rows, required, kind):
    for i, r in enumerate(rows):
        missing = [k for k in required if r.get(k) is None or (isinstance(r.get(k), float) and math.isnan(r[k]))]
        if missing:
            raise PredictionInputError(f"{kind} row {i}: missing required fields {missing}")
        for c, allowed in CATEGORIES.items():
            if c in r and r[c] is not None and r[c] not in allowed:
                raise PredictionInputError(f"{kind} row {i}: {c}={r[c]!r} not in {allowed}")
        for k in ("queue_length_at_join", "queue_length", "servers_on_duty", "servers_busy"):
            if k in r and r[k] is not None and r[k] < 0:
                raise PredictionInputError(f"{kind} row {i}: {k} must be >= 0")


class Predictor:
    def __init__(self, models_dir: Path | str = MODELS):
        self.wait = joblib.load(Path(models_dir) / "waiting_time_model.joblib")
        self.bn = joblib.load(Path(models_dir) / "bottleneck_model.joblib")

    @property
    def versions(self) -> dict:
        return {"waiting_time": self.wait["version"], "bottleneck": self.bn["version"]}

    def _frame(self, rows, features, cats):
        df = pd.DataFrame([{f: r.get(f, np.nan) for f in features} for r in rows])
        df = df.astype({c: "float64" for c in features if c not in cats})
        return as_categorical(df, cats)

    def predict_wait(self, rows: list[dict], with_range: bool = True) -> list[dict]:
        if not rows:
            return []
        _check(rows, WAIT_REQUIRED, "waiting-time")
        X = self._frame(rows, self.wait["features"], self.wait["categorical"])
        p = np.clip(self.wait["model"].predict(X), 0, None)
        out = [{"predicted_wait_min": round(float(v), 1)} for v in p]
        if with_range:
            lo = np.clip(self.wait["q10"].predict(X), 0, None)
            hi = np.clip(self.wait["q90"].predict(X), 0, None)
            for o, a, b, v in zip(out, lo, hi, p):
                o["expected_range_min"] = [round(float(min(a, v)), 1), round(float(max(b, v)), 1)]
        return out

    def alert_level(self, prob: float) -> str:
        lv = self.bn["alert_levels"]
        return "HIGH" if prob >= lv["HIGH"] else "MEDIUM" if prob >= lv["MEDIUM"] else "LOW"

    def predict_bottleneck(self, rows: list[dict]) -> list[dict]:
        if not rows:
            return []
        _check(rows, BN_REQUIRED, "bottleneck")
        X = self._frame(rows, self.bn["features"], self.bn["categorical"])
        p = self.bn["model"].predict_proba(X)[:, 1]
        return [{"bottleneck_probability_next_30min": round(float(v), 3), "alert_level": self.alert_level(v)}
                for v in p]
