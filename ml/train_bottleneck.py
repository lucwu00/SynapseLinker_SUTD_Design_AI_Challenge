"""Model B - bottleneck forecast (target: bottleneck_next_30min).

Label: 1 if the mean wait of patients who join the station in (t, t+30 min] exceeds the
station threshold. It is a FORECAST of the next 30 minutes, not an observed current bottleneck.

Protocol: tune on validation (Aug-Sep), choose the alert threshold on validation, refit on
Jan-Sep, evaluate once on Oct-Dec. Each simulated day starts with empty queues, so splitting
on whole dates leaves no overlapping label window across the boundary.

Usage (Windows):  python -m ml.train_bottleneck
"""
from __future__ import annotations
import json, os, time
import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, precision_recall_curve, precision_score, recall_score,
                             f1_score, confusion_matrix, brier_score_loss)
from sklearn.pipeline import Pipeline

from ml.preprocessing import (BN_CAT, BN_NUM, BN_FEATURES, BN_FORBIDDEN, BN_TARGET, MODELS, REPORTS,
                              load_snapshots, as_categorical, assert_no_leakage, linear_preprocessor,
                              tree_preprocessor)

# Deploy mode (Render build): train only the HistGradientBoosting configurations - same selection on
# validation, but no RandomForest/Ridge comparison and no report files, to fit a small cloud instance.
DEPLOY = os.environ.get("SYNAPSE_DEPLOY_TRAIN") == "1"
MODEL_VERSION = "bottleneck-1.0.0"


def candidates():
    yield "Dummy (prior)", {}, lambda: DummyClassifier(strategy="prior")
    for C in [0.1, 1.0]:
        yield "LogisticRegression", {"C": C}, lambda C=C: Pipeline([
            ("pre", linear_preprocessor(BN_CAT, BN_NUM)),
            ("m", LogisticRegression(C=C, max_iter=2000, class_weight="balanced"))])
    for leaf in [5, 20]:
        yield "RandomForest", {"min_samples_leaf": leaf}, lambda leaf=leaf: Pipeline([
            ("pre", tree_preprocessor(BN_CAT, BN_NUM)),
            ("m", RandomForestClassifier(n_estimators=200, min_samples_leaf=leaf, n_jobs=-1, random_state=0))])
    for lr, leaves in [(0.05, 31), (0.1, 15)]:
        yield "HistGradientBoosting", {"learning_rate": lr, "max_leaf_nodes": leaves}, \
            lambda lr=lr, leaves=leaves: HistGradientBoostingClassifier(
                learning_rate=lr, max_leaf_nodes=leaves, max_iter=500, early_stopping=True,
                n_iter_no_change=30, categorical_features="from_dtype", random_state=0)


def best_f1_threshold(y, p):
    pr, rc, th = precision_recall_curve(y, p)
    f1 = 2 * pr * rc / np.clip(pr + rc, 1e-9, None)
    i = int(np.nanargmax(f1[:-1]))
    return float(th[i])


def cls_metrics(y, p, thr):
    yhat = (p >= thr).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, yhat, labels=[0, 1]).ravel()
    return dict(PR_AUC=round(float(average_precision_score(y, p)), 4) if y.sum() else None,
                precision=round(float(precision_score(y, yhat, zero_division=0)), 4),
                recall=round(float(recall_score(y, yhat, zero_division=0)), 4),
                F1=round(float(f1_score(y, yhat, zero_division=0)), 4),
                brier=round(float(brier_score_loss(y, p)), 4), threshold=round(thr, 4),
                TP=int(tp), FP=int(fp), FN=int(fn), TN=int(tn))


def main():
    t0 = time.time()
    assert_no_leakage(BN_FEATURES, BN_FORBIDDEN)
    df = load_snapshots()
    X = as_categorical(df[BN_FEATURES], BN_CAT)
    y = df[BN_TARGET].values
    tr, va, te = [(df.split == s).values for s in ("train", "val", "test")]
    print("class balance:", {s: round(float(y[m].mean()), 4) for s, m in zip(("train", "val", "test"), (tr, va, te))})

    rows, best = [], {}
    for name, params, make in candidates():
        if DEPLOY and name != "HistGradientBoosting":
            continue
        m = make().fit(X[tr], y[tr])
        p = m.predict_proba(X[va])[:, 1]
        thr = 0.5 if name.startswith("Dummy") else best_f1_threshold(y[va], p)
        r = cls_metrics(y[va], p, thr)
        rows.append(dict(model=name, params=json.dumps(params), split="val", **r)); print(rows[-1])
        if name not in best or (r["PR_AUC"] or 0) > (best[name][1]["PR_AUC"] or 0):
            best[name] = (params, r, make)
    chosen = max(best, key=lambda k: best[k][1]["PR_AUC"] or 0)
    print("selected on validation:", chosen, best[chosen][0])

    dev = tr | va
    test_p = {}
    for name, (params, rv, make) in best.items():
        m = make().fit(X[dev], y[dev])
        p = m.predict_proba(X[te])[:, 1]; test_p[name] = p
        rows.append(dict(model=name, params=json.dumps(params), split="test", **cls_metrics(y[te], p, rv["threshold"])))
        if name == chosen: final = m
    res = pd.DataFrame(rows)
    if not DEPLOY:
        res.to_csv(REPORTS / "model_b_comparison.csv", index=False)
    print(res[res.split == "test"].drop(columns=["params"]).to_string(index=False))

    thr = best[chosen][1]["threshold"]
    alert = dict(HIGH=thr, MEDIUM=round(thr / 2, 4))   # LOW below MEDIUM
    per_station = []
    p = test_p[chosen]
    for st, g in df[te].assign(p=p, y=y[te]).groupby("station"):
        r = cls_metrics(g.y.values, g.p.values, thr); r["station"] = st; r["positive_rate"] = round(float(g.y.mean()), 4)
        per_station.append(r)
    if not DEPLOY:
        pd.DataFrame(per_station).to_csv(REPORTS / "model_b_by_station.csv", index=False)
        out = df.loc[te, ["date", "snapshot_time", "station", "queue_length", "servers_busy", "servers_on_duty"]].copy()
        out["actual"], out["probability"] = y[te], p
        out.to_csv(REPORTS / "model_b_test_predictions.csv", index=False)

    MODELS.mkdir(parents=True, exist_ok=True)
    joblib.dump(dict(model=final, model_name=chosen, params=best[chosen][0], features=BN_FEATURES,
                     categorical=BN_CAT, numeric=BN_NUM, threshold=thr, alert_levels=alert, version=MODEL_VERSION,
                     trained_on="ortho_station_snapshots.csv 2025-01-02..2025-09-30 (synthetic)",
                     test_metrics=cls_metrics(y[te], p, thr),
                     note="Probability that the NEXT 30 minutes will exceed the station wait threshold."),
                MODELS / "bottleneck_model.joblib", compress=3)
    print(f"saved ml/models/bottleneck_model.joblib  ({time.time()-t0:.0f}s total)")


if __name__ == "__main__":
    main()
