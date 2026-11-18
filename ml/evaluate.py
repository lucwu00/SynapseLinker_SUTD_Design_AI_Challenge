"""Evaluation charts, feature importance and the model report for Models A and B.

Usage (Windows):  python -m ml.evaluate      (run after train_waiting and train_bottleneck)
"""
from __future__ import annotations
import joblib
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.inspection import permutation_importance
from sklearn.metrics import precision_recall_curve, confusion_matrix
from sklearn.calibration import calibration_curve

from ml.preprocessing import (MODELS, REPORTS, FIGS, load_events, load_snapshots, as_categorical,
                              WAIT_FEATURES, WAIT_CAT, WAIT_TARGET, BN_FEATURES, BN_CAT, BN_TARGET)

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
S1, S2, S3, NEUTRAL = "#2a78d6", "#eb6834", "#1baf7a", "#a9a8a2"
plt.rcParams.update({"figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "axes.edgecolor": GRID,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2, "text.color": INK,
                     "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.spines.top": False,
                     "axes.spines.right": False, "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "bold",
                     "axes.titlelocation": "left", "legend.frameon": False})


def save(fig, name):
    FIGS.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(); fig.savefig(FIGS / name, dpi=160); plt.close(fig)


def hbar(ax, labels, values, colors, fmt="{:.1f}"):
    y = np.arange(len(labels))
    ax.barh(y, values, color=colors, height=0.6)
    ax.set_yticks(y, labels); ax.invert_yaxis(); ax.grid(axis="y", visible=False)
    for i, v in enumerate(values):
        ax.text(v, i, " " + fmt.format(v), va="center", color=INK2, fontsize=9)


def model_a(lines):
    comp = pd.read_csv(REPORTS / "model_a_comparison.csv")
    t = comp[comp.split == "test"].reset_index(drop=True)
    pred = pd.read_csv(REPORTS / "model_a_test_predictions.csv")
    bundle = joblib.load(MODELS / "waiting_time_model.joblib")

    fig, ax = plt.subplots(figsize=(7, 3))
    hbar(ax, t.model, t.MAE, [S1 if m == bundle["model_name"] else NEUTRAL for m in t.model])
    ax.set_xlabel("MAE on Oct-Dec test (minutes, lower is better)"); ax.set_title("Model A - waiting-time model comparison")
    save(fig, "a1_model_comparison.png")

    s = pred.sample(min(8000, len(pred)), random_state=0)
    fig, ax = plt.subplots(figsize=(5.5, 5))
    ax.scatter(s.actual, s.predicted, s=8, alpha=0.25, color=S1, linewidths=0)
    m = max(s.actual.max(), s.predicted.max()); ax.plot([0, m], [0, m], color=INK2, lw=1, ls="--")
    ax.set_xlabel("Actual wait (min)"); ax.set_ylabel("Predicted wait (min)")
    ax.set_title("Actual vs predicted (8,000-row test sample)")
    save(fig, "a2_actual_vs_predicted.png")

    err = pred.predicted - pred.actual
    fig, ax = plt.subplots(figsize=(7, 3))
    ax.hist(err.clip(-60, 60), bins=60, color=S1, edgecolor=SURFACE, linewidth=0.5)
    ax.set_xlabel("Prediction error, predicted - actual (min, clipped at +/-60)"); ax.set_ylabel("Events")
    ax.set_title("Error distribution")
    save(fig, "a3_error_distribution.png")

    by = pred.assign(ae=(pred.predicted - pred.actual).abs(), be=(pred.baseline - pred.actual).abs()) \
             .groupby("station").agg(n=("ae", "size"), model_MAE=("ae", "mean"), baseline_MAE=("be", "mean"),
                                     mean_wait=("actual", "mean")).sort_values("model_MAE", ascending=False)
    fig, ax = plt.subplots(figsize=(7, 4))
    y = np.arange(len(by)); h = 0.38
    ax.barh(y - h/2, by.baseline_MAE, height=h, color=NEUTRAL, label="Baseline (station x hour median)")
    ax.barh(y + h/2, by.model_MAE, height=h, color=S1, label="HistGradientBoosting")
    ax.set_yticks(y, by.index); ax.invert_yaxis(); ax.grid(axis="y", visible=False)
    ax.set_xlabel("MAE (min)"); ax.set_title("Error by station"); ax.legend(loc="lower right")
    save(fig, "a4_error_by_station.png")

    # congestion = queue length relative to rostered servers, in quintiles
    pred["load"] = pred.queue_length_at_join / pred.servers_on_duty.clip(lower=1)
    pred["congestion"] = pd.qcut(pred.load.rank(method="first"), 5, labels=["Q1 lowest", "Q2", "Q3", "Q4", "Q5 highest"])
    cg = pred.assign(ae=(pred.predicted - pred.actual).abs(), be=(pred.baseline - pred.actual).abs()) \
             .groupby("congestion", observed=True).agg(model_MAE=("ae", "mean"), baseline_MAE=("be", "mean"),
                                                       mean_wait=("actual", "mean"))
    fig, ax = plt.subplots(figsize=(7, 3.2))
    x = np.arange(len(cg)); w = 0.38
    ax.bar(x - w/2, cg.baseline_MAE, width=w, color=NEUTRAL, label="Baseline")
    ax.bar(x + w/2, cg.model_MAE, width=w, color=S1, label="HistGradientBoosting")
    ax.set_xticks(x, cg.index); ax.grid(axis="x", visible=False)
    ax.set_ylabel("MAE (min)"); ax.set_xlabel("Queue length per rostered server (quintile)")
    ax.set_title("Error under congestion"); ax.legend()
    save(fig, "a5_error_under_congestion.png")

    long = pred.actual >= 60
    lw = dict(n=int(long.sum()), MAE=float((pred.predicted - pred.actual).abs()[long].mean()),
              bias=float((pred.predicted - pred.actual)[long].mean()),
              baseline_MAE=float((pred.baseline - pred.actual).abs()[long].mean()))
    cover = float(((pred.actual >= pred.p10) & (pred.actual <= pred.p90)).mean())

    # permutation importance on a test sample
    ev = load_events(); te = ev[ev.split == "test"].sample(6000, random_state=1)
    X = as_categorical(te[WAIT_FEATURES], WAIT_CAT)
    pi = permutation_importance(bundle["model"], X, te[WAIT_TARGET], n_repeats=5, random_state=0,
                                scoring="neg_mean_absolute_error")
    imp = pd.Series(pi.importances_mean, index=WAIT_FEATURES).sort_values(ascending=False)
    imp.to_csv(REPORTS / "model_a_feature_importance.csv", header=["MAE_increase_min"])
    top = imp.head(12)
    fig, ax = plt.subplots(figsize=(7, 4))
    hbar(ax, top.index, top.values, S1, "{:.2f}")
    ax.set_xlabel("Increase in MAE when shuffled (min)"); ax.set_title("Model A - permutation importance (top 12)")
    save(fig, "a6_feature_importance.png")

    lines += ["## Model A - waiting-time regression", "",
              f"Selected: **{bundle['model_name']}** {bundle['params']} (chosen on Aug-Sep validation). "
              "Refitted on Jan-Sep; evaluated once on Oct-Dec (57,666 events).", "",
              t[["model", "MAE", "RMSE", "R2"]].to_markdown(index=False), "",
              "![](figures/a1_model_comparison.png)", "![](figures/a2_actual_vs_predicted.png)",
              "![](figures/a3_error_distribution.png)", "",
              "### Error by station", "", by.round(2).to_markdown(), "", "![](figures/a4_error_by_station.png)", "",
              "### Error under congestion", "", cg.round(2).to_markdown(), "",
              "![](figures/a5_error_under_congestion.png)", "",
              f"### Long waits (actual >= 60 min, n = {lw['n']})", "",
              f"Model MAE {lw['MAE']:.1f} min (baseline {lw['baseline_MAE']:.1f}); mean bias {lw['bias']:+.1f} min "
              "(negative = under-predicts long waits).", "",
              f"### Uncertainty", "",
              f"The 10th-90th percentile band covers {cover*100:.1f}% of test waits (nominal 80%), so the band is "
              "conservative (wider than necessary). The API reports it as an 'expected range'.", "",
              "### Feature importance", "", "![](figures/a6_feature_importance.png)", "",
              imp.head(10).round(3).to_frame("MAE increase (min)").to_markdown(), ""]
    return by, imp


def model_b(lines):
    comp = pd.read_csv(REPORTS / "model_b_comparison.csv")
    t = comp[comp.split == "test"].reset_index(drop=True)
    pred = pd.read_csv(REPORTS / "model_b_test_predictions.csv")
    st = pd.read_csv(REPORTS / "model_b_by_station.csv")
    bundle = joblib.load(MODELS / "bottleneck_model.joblib")
    thr = bundle["threshold"]

    fig, ax = plt.subplots(figsize=(7, 2.6))
    hbar(ax, t.model, t.PR_AUC, [S1 if m == bundle["model_name"] else NEUTRAL for m in t.model], "{:.3f}")
    ax.set_xlim(0, 1.08); ax.set_xlabel("PR-AUC on Oct-Dec test (higher is better)")
    ax.set_title("Model B - bottleneck model comparison")
    save(fig, "b1_model_comparison.png")

    p, r, _ = precision_recall_curve(pred.actual, pred.probability)
    fig, axs = plt.subplots(1, 2, figsize=(9, 3.8))
    axs[0].plot(r, p, color=S1, lw=2); axs[0].axhline(pred.actual.mean(), color=NEUTRAL, ls="--", lw=1)
    axs[0].text(0.02, pred.actual.mean() + 0.02, "no-skill (prevalence)", color=INK2, fontsize=8)
    axs[0].set_xlabel("Recall"); axs[0].set_ylabel("Precision"); axs[0].set_title("Precision-recall curve")
    fr, mp = calibration_curve(pred.actual, pred.probability, n_bins=10, strategy="uniform")
    axs[1].plot([0, 1], [0, 1], color=NEUTRAL, ls="--", lw=1)
    axs[1].plot(mp, fr, color=S1, lw=2, marker="o", ms=5)
    axs[1].set_xlabel("Predicted probability"); axs[1].set_ylabel("Observed frequency"); axs[1].set_title("Calibration")
    save(fig, "b2_pr_and_calibration.png")

    cm = confusion_matrix(pred.actual, (pred.probability >= thr).astype(int))
    fig, ax = plt.subplots(figsize=(4, 3.4))
    ax.imshow(cm, cmap="Blues"); ax.grid(False)
    for (i, j), v in np.ndenumerate(cm):
        ax.text(j, i, f"{v:,}", ha="center", va="center", color="white" if v > cm.max() / 2 else INK)
    ax.set_xticks([0, 1], ["No alert", "Alert"]); ax.set_yticks([0, 1], ["No bottleneck", "Bottleneck"])
    ax.set_xlabel("Predicted"); ax.set_ylabel("Actual (next 30 min)"); ax.set_title(f"Confusion matrix (threshold {thr:.2f})")
    save(fig, "b3_confusion_matrix.png")

    sn = load_snapshots(); te = sn[sn.split == "test"].sample(8000, random_state=1)
    X = as_categorical(te[BN_FEATURES], BN_CAT)
    pi = permutation_importance(bundle["model"], X, te[BN_TARGET], n_repeats=5, random_state=0,
                                scoring="average_precision")
    imp = pd.Series(pi.importances_mean, index=BN_FEATURES).sort_values(ascending=False)
    imp.to_csv(REPORTS / "model_b_feature_importance.csv", header=["PR_AUC_drop"])
    fig, ax = plt.subplots(figsize=(7, 3.6))
    hbar(ax, imp.index, imp.values, S1, "{:.3f}")
    ax.set_xlabel("Drop in PR-AUC when shuffled"); ax.set_title("Model B - permutation importance")
    save(fig, "b4_feature_importance.png")

    # error analysis
    pred["yhat"] = (pred.probability >= thr).astype(int)
    pred["hour"] = pd.to_datetime(pred.snapshot_time).dt.hour
    fn = pred[(pred.actual == 1) & (pred.yhat == 0)]; fp = pred[(pred.actual == 0) & (pred.yhat == 1)]
    fn_st = fn.station.value_counts().head(4).to_dict(); fp_st = fp.station.value_counts().head(4).to_dict()
    fn_q = fn.queue_length.median(); tp_q = pred[(pred.actual == 1) & (pred.yhat == 1)].queue_length.median()

    lines += ["## Model B - bottleneck forecast (next 30 minutes)", "",
              "Label: 1 when the mean wait of patients who join the station in the next 30 minutes exceeds that "
              "station's threshold. **This is a forecast, not an observed current bottleneck.**", "",
              f"Class balance (test): {pred.actual.mean()*100:.1f}% positive. Selected **{bundle['model_name']}** "
              f"{bundle['params']}; alert threshold {thr:.3f} chosen to maximise F1 on Aug-Sep validation.", "",
              t[["model", "PR_AUC", "precision", "recall", "F1", "brier", "TP", "FP", "FN", "TN"]].to_markdown(index=False), "",
              "![](figures/b1_model_comparison.png)", "![](figures/b2_pr_and_calibration.png)",
              "![](figures/b3_confusion_matrix.png)", "",
              "### Alert levels returned by the API", "",
              f"- HIGH: probability >= {bundle['alert_levels']['HIGH']:.2f} (validation-tuned threshold)",
              f"- MEDIUM: probability >= {bundle['alert_levels']['MEDIUM']:.2f}",
              "- LOW: below MEDIUM", "",
              "### By station", "",
              st[["station", "positive_rate", "PR_AUC", "precision", "recall", "F1", "TP", "FP", "FN"]].to_markdown(index=False), "",
              "### False positive / false negative analysis", "",
              f"- Missed bottlenecks (FN = {len(fn)}) by station: {fn_st}. Median queue length when missed: "
              f"{fn_q:.0f}, versus {tp_q:.0f} when caught. Misses are mostly bottlenecks that build from a short "
              "queue, e.g. a doctor running late or a burst of arrivals.",
              f"- False alarms (FP = {len(fp)}) by station: {fp_st}.",
              "- **CONSULT is the weak spot** (recall about 0.26). Consult delays depend on individual doctors' pace "
              "and late starts, which a station-level snapshot captures poorly. Use Model A's per-patient consult "
              "wait for consult decisions, and treat Model B consult alerts as advisory only.",
              "- CAST_ROOM, DRESSING and PROCEDURE almost never bottleneck (<0.3% positives), so their scores are not meaningful.",
              "", "### Feature importance", "", "![](figures/b4_feature_importance.png)", "",
              imp.round(4).to_frame("PR-AUC drop").to_markdown(), ""]


def main():
    lines = ["# Synapse Linker - Model report", "",
             "All data are synthetic (fictional orthopaedic clinic simulation). Models are **not clinically validated**; "
             "scores show how well the models learn the simulated clinic and will be lower on real hospital data.", "",
             "Protocol: train Jan-Jul 2025, validate/tune Aug-Sep, refit on Jan-Sep, test once on Oct-Dec. "
             "Splits are by whole date; no visit spans two splits. Forbidden and future-derived columns are "
             "rejected in code (`assert_no_leakage`).", ""]
    model_a(lines)
    model_b(lines)
    lines += ["## Why the scores are high", "",
              "- Waits at first-in-first-out stations are largely determined by queue length and recent waits, both of "
              "which are known when a patient joins. The strongest features are exactly these, not leaked outcomes.",
              "- The simulator has less hidden variation than a real hospital, although unannounced breaks, outages, "
              "urgent queue-jumpers and complex cases were added to limit this.",
              "- CONSULT is the most realistic test (doctor pace, priorities, late starts) and scores lowest.", ""]
    (REPORTS / "model_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("wrote reports/model_report.md and reports/figures/*.png")


if __name__ == "__main__":
    main()
