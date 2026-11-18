"""Learn the look-ahead component used by the router (development period Jan-Sep only).

1. Time-of-day wait profile: mean wait_min of patients joining each station in each 15-min bin.
2. Persistence: how quickly a station's deviation from its usual level fades, as an exponential
   time constant tau (minutes), fitted from the autocorrelation of snapshot wait deviations.

Projected wait if a patient joins `d` minutes from now:
    profile(t + d) + (predicted_now - profile(t)) * exp(-d / tau)

Usage:  python -m ml.build_wait_profile
"""
from __future__ import annotations
import json
import numpy as np
import pandas as pd

from ml.preprocessing import DATA, MODELS, VAL_END

BIN = 15


def main():
    ev = pd.read_csv(DATA / "ortho_service_events.csv", usecols=["date", "station", "join_time", "wait_min"])
    ev = ev[ev.date < VAL_END]
    t = pd.to_datetime(ev.join_time)
    ev["bin"] = (t.dt.hour * 60 + t.dt.minute) // BIN * BIN
    prof = ev[(ev.bin >= 7 * 60) & (ev.bin < 19 * 60)].groupby(["station", "bin"]).wait_min.mean()

    sn = pd.read_csv(DATA / "ortho_station_snapshots.csv")
    sn = sn[sn.date < VAL_END].copy()
    ts = pd.to_datetime(sn.snapshot_time)
    sn["tod"] = ts.dt.hour * 60 + ts.dt.minute
    sn["dev"] = sn.recent_mean_wait_30min - sn.groupby(["station", "tod"]).recent_mean_wait_30min.transform("mean")
    sn = sn.sort_values(["station", "date", "tod"])
    taus = {}
    for st, g in sn.groupby("station"):
        lags, logs = [], []
        for k in (1, 2, 3, 4):
            y = g.groupby("date").dev.shift(-k)
            m = g.dev.notna() & y.notna()
            if m.sum() > 50:
                r = np.corrcoef(g.dev[m], y[m])[0, 1]
                if r > 0.01:
                    lags.append(k * BIN); logs.append(np.log(r))
        slope = np.sum(np.array(lags) * np.array(logs)) / np.sum(np.array(lags) ** 2) if lags else -1 / 30
        taus[st] = float(np.clip(-1 / slope, 10, 600))

    out = {"bin_min": BIN, "trained_on": "2025-01-02..2025-09-30", "tau_min": {k: round(v, 1) for k, v in taus.items()},
           "profile": {st: {int(b): round(float(w), 2) for b, w in g.droplevel(0).items()} for st, g in prof.groupby(level=0)}}
    MODELS.mkdir(parents=True, exist_ok=True)
    (MODELS / "wait_profile.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("tau (min):", out["tau_min"])


if __name__ == "__main__":
    main()
