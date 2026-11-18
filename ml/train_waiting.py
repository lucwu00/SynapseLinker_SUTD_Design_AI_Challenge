"""Model A - waiting-time regression (target: wait_min).

Chronological protocol:
  1. Fit candidates on train (Jan-Jul), tune on validation (Aug-Sep).
  2. Refit the chosen configuration on train+val (Jan-Sep).
  3. Evaluate ONCE on test (Oct-Dec).
Also fits 10%/90% quantile models for an uncertainty band.

Usage (Windows):  python -m ml.train_waiting
"""
from __future__ import annotations
import json, os, time
import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline

from ml.preprocessing import (WAIT_CAT, WAIT_NUM, WAIT_FEATURES, WAIT_FORBIDDEN, WAIT_TARGET, MODELS, REPORTS,
                              load_events, as_categorical, assert_no_leakage, linear_preprocessor, tree_preprocessor)

# Deploy mode (Render build): train only the HistGradientBoosting configurations - same selection on
# validation, but no RandomForest/Ridge comparison and no report files, to fit a small cloud instance.
DEPLOY = os.environ.get("SYNAPSE_DEPLOY_TRAIN") == "1"
MODEL_VERSION = "waiting-1.0.0"
RF_TRAIN_ROWS = 60000   # RandomForest is subsampled to keep training laptop-friendly


def metrics(y, p):
    return dict(MAE=round(float(mean_absolute_error(y, p)), 3),
                RMSE=round(float(np.sqrt(mean_squared_error(y, p))), 3),
                R2=round(float(r2_score(y, p)), 4))


class StationHourMedian:
    """Simple operational baseline: historical median wait for this station and hour."""
    def fit(self, X, y):
        d = X[["station", "join_hour"]].astype(str).assign(y=np.asarray(y))
        self.table = d.groupby(["station", "join_hour"]).y.median().to_dict()
        self.station = d.groupby("station").y.median().to_dict()
        self.overall = float(np.median(y))
        return self

    def predict(self, X):
        s, h = X["station"].astype(str).values, X["join_hour"].astype(str).values
        return np.array([self.table.get((a, b), self.station.get(a, self.overall)) for a, b in zip(s, h)])


def candidates():
    yield "Dummy (mean)", {}, lambda: DummyRegressor(strategy="mean")
    yield "Baseline: station x hour median", {}, StationHourMedian
    for a in [1.0, 10.0]:
        yield "Ridge", {"alpha": a}, lambda a=a: Pipeline([("pre", linear_preprocessor(WAIT_CAT, WAIT_NUM)),
                                                          ("m", Ridge(alpha=a))])
    for leaf in [5, 20]:
        yield "RandomForest", {"min_samples_leaf": leaf}, lambda leaf=leaf: Pipeline([
            ("pre", tree_preprocessor(WAIT_CAT, WAIT_NUM)),
            ("m", RandomForestRegressor(n_estimators=120, min_samples_leaf=leaf, max_features=0.5,
                                        n_jobs=-1, random_state=0))])
    for lr, leaves in [(0.05, 31), (0.1, 63)]:
        yield "HistGradientBoosting", {"learning_rate": lr, "max_leaf_nodes": leaves}, \
            lambda lr=lr, leaves=leaves: HistGradientBoostingRegressor(
                learning_rate=lr, max_leaf_nodes=leaves, max_iter=600, early_stopping=True,
                validation_fraction=0.1, n_iter_no_change=30, categorical_features="from_dtype", random_state=0)


def main():
    t0 = time.time()
    assert_no_leakage(WAIT_FEATURES, WAIT_FORBIDDEN)
    df = load_events(WAIT_FEATURES + [WAIT_TARGET] if DEPLOY else None)
    X = as_categorical(df[WAIT_FEATURES], WAIT_CAT)
    y = df[WAIT_TARGET].values
    tr, va, te = [(df.split == s).values for s in ("train", "val", "test")]
    print(f"rows train={tr.sum()} val={va.sum()} test={te.sum()}")

    rng = np.random.default_rng(0)
    rows = []
    best = {}
    for name, params, make in candidates():
        if DEPLOY and name != "HistGradientBoosting":
            continue
        m = make()
        fit_idx = tr
        if name == "RandomForest":
            fit_idx = np.zeros(len(df), bool); fit_idx[rng.choice(np.where(tr)[0], RF_TRAIN_ROWS, replace=False)] = True
        s = time.time(); m.fit(X[fit_idx], y[fit_idx]); fit_s = time.time() - s
        r = metrics(y[va], m.predict(X[va]))
        rows.append(dict(model=name, params=json.dumps(params), split="val", fit_seconds=round(fit_s, 1), **r))
        print(rows[-1])
        if name not in best or r["MAE"] < best[name][1]["MAE"]:
            best[name] = (params, r, make)

    chosen = min(best, key=lambda k: best[k][1]["MAE"])
    print("selected on validation:", chosen, best[chosen][0])

    # Refit every model family's best config on train+val, evaluate on test once.
    dev = tr | va
    test_preds = {}
    for name, (params, _, make) in best.items():
        m = make()
        fit_idx = dev
        if name == "RandomForest":
            fit_idx = np.zeros(len(df), bool); fit_idx[rng.choice(np.where(dev)[0], RF_TRAIN_ROWS, replace=False)] = True
        m.fit(X[fit_idx], y[fit_idx])
        p = m.predict(X[te]); test_preds[name] = p
        rows.append(dict(model=name, params=json.dumps(params), split="test", fit_seconds=None, **metrics(y[te], p)))
        if name == chosen:
            final = m
    res = pd.DataFrame(rows)
    if not DEPLOY:
        res.to_csv(REPORTS / "model_a_comparison.csv", index=False)
    print(res[res.split == "test"].to_string(index=False))

    # Uncertainty band: quantile models on the chosen feature set
    qp = best["HistGradientBoosting"][0]
    q = {}
    for alpha in (0.1, 0.9):
        q[alpha] = HistGradientBoostingRegressor(loss="quantile", quantile=alpha, learning_rate=qp["learning_rate"],
                                                 max_leaf_nodes=qp["max_leaf_nodes"], max_iter=400,
                                                 categorical_features="from_dtype", random_state=0).fit(X[dev], y[dev])
    lo, hi = q[0.1].predict(X[te]), q[0.9].predict(X[te])
    coverage = float(np.mean((y[te] >= lo) & (y[te] <= hi)))
    print(f"80% interval coverage on test: {coverage:.3f}")

    if not DEPLOY:
      pred = pd.DataFrame({"event_id": df.event_id[te].values, "date": df.date[te].values,
                         "station": df.station[te].values, "actual": y[te],
                         "predicted": np.clip(test_preds[chosen], 0, None), "p10": np.clip(lo, 0, None),
                         "p90": np.clip(hi, 0, None), "baseline": test_preds["Baseline: station x hour median"],
                         "queue_length_at_join": df.queue_length_at_join[te].values,
                         "servers_on_duty": df.servers_on_duty[te].values})
      pred.to_csv(REPORTS / "model_a_test_predictions.csv", index=False)

    MODELS.mkdir(parents=True, exist_ok=True)
    joblib.dump(dict(model=final, q10=q[0.1], q90=q[0.9], model_name=chosen, params=best[chosen][0],
                     features=WAIT_FEATURES, categorical=WAIT_CAT, numeric=WAIT_NUM, version=MODEL_VERSION,
                     trained_on="ortho_service_events.csv 2025-01-02..2025-09-30 (synthetic)",
                     test_metrics=metrics(y[te], test_preds[chosen]), interval_coverage_80=round(coverage, 3)),
                MODELS / "waiting_time_model.joblib", compress=3)
    print(f"saved ml/models/waiting_time_model.joblib  ({time.time()-t0:.0f}s total)")


if __name__ == "__main__":
    main()
