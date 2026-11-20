"""Busy-day surge for the demo clock (+15 min / Run x10).

When the presenter moves the clinic clock on, the clinic should get busier the way it does in the data, not
by numbers we make up. For each 15-minute step:

* Each station's queue, recent wait and arrivals move half-way towards the 90th percentile of the clinic
  snapshots for that weekday and time of day ("a busy day in our data"). They only go up, never down.
* A few patients arrive, drawn from the appointment records for that weekday and hour: their visit type,
  referral, age, mobility, doctor and pre-consult tests are copied from real (synthetic) records; only the
  names are made up. They check in and join the queue for their recommended first step.

Model A and Model B then predict from these new inputs, so the predicted waits and risks rise because the
models see a busier clinic. Everything is labelled as a scenario; Reset demo puts the clinic back.
"""
from __future__ import annotations
import random
from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = ROOT / "data" / "original" / "ortho_station_snapshots.csv"
APPOINTMENTS = ROOT / "data" / "original" / "ortho_appointments.csv"
PRE_TESTS = {"XRAY", "PHLEBOTOMY", "ECG"}
NAMES = ["Mr Goh", "Mdm Wong", "Mr Rahman", "Ms Devi", "Mr Chua", "Mdm Siti", "Mr Ong", "Ms Priya", "Mr Ismail",
         "Mdm Chen", "Mr Lee", "Ms Nur", "Mr Pillai", "Mdm Koh", "Mr Teo", "Ms Aisha", "Mr Low", "Mdm Raj"]
MAX_SURGE_PATIENTS = 12


@lru_cache(maxsize=1)
def busy_levels() -> pd.DataFrame:
    """90th percentile per (weekday, station, 15-min slot) of the clinic snapshots."""
    d = pd.read_csv(SNAPSHOTS, usecols=["day_of_week", "snapshot_time", "station", "queue_length",
                                        "recent_mean_wait_30min", "arrivals_last_15min"])
    t = pd.to_datetime(d["snapshot_time"])
    d["slot"] = t.dt.hour * 60 + (t.dt.minute // 15) * 15
    g = d.groupby(["day_of_week", "station", "slot"]).agg(
        q=("queue_length", lambda x: x.quantile(0.9)), w=("recent_mean_wait_30min", lambda x: x.quantile(0.9)),
        a=("arrivals_last_15min", lambda x: x.quantile(0.9)))
    return g


@lru_cache(maxsize=1)
def appointments() -> pd.DataFrame:
    a = pd.read_csv(APPOINTMENTS, usecols=["day_of_week", "appointment_time", "visit_type", "referral_category",
                                           "clinician_priority", "mobility", "age", "doctor_id", "planned_steps",
                                           "no_show"])
    a = a[a["no_show"] == 0].copy()
    t = pd.to_datetime(a["appointment_time"])
    a["minute"] = t.dt.hour * 60 + t.dt.minute
    return a


def _level(dow: int, station: str, slot: int):
    g = busy_levels()
    for key in ((dow, station, slot), (dow, station, slot - 15), (dow, station, slot + 15)):
        if key in g.index:
            r = g.loc[key]
            return float(r["q"]), (None if pd.isna(r["w"]) else float(r["w"])), float(r["a"])
    return None


def step(svc, clinic_min: float, rng: random.Random | None = None, new_patients: int = 2) -> dict:
    """One 15-minute surge step at clinic time `clinic_min`. Returns what changed (for the event log)."""
    rng = rng or random.Random(int(clinic_min))
    dow = int(svc.clock.day_of_week)
    slot = int(clinic_min // 15 * 15)
    changed = []
    for s in svc.db.query("SELECT * FROM stations"):
        lv = _level(dow, s["station_id"], slot)
        if not lv or not s["available"]:
            continue
        q90, w90, a90 = lv
        q0 = s["background_queue"] or 0
        q1 = max(q0, round(q0 + 0.5 * (q90 - q0)))
        w0 = s["recent_wait_hint"] or 0
        w1 = max(w0, w0 + 0.5 * ((w90 or w0) - w0))
        a0 = s["arrivals_hint"] or 0
        a1 = max(a0, round(a0 + 0.5 * (a90 - a0)))
        if (q1, round(w1, 1), a1) != (q0, round(w0, 1), a0):
            svc.db.update("stations", "station_id", s["station_id"], background_queue=int(q1),
                          recent_wait_hint=round(w1, 1), arrivals_hint=int(a1))
            if q1 != q0:
                changed.append(f"{s['station_id']} {q0}->{q1}")
    added = []
    n_now = svc.db.one("SELECT COUNT(*) AS n FROM patients WHERE patient_id LIKE 'SURGE_%'")["n"]
    pool = appointments()
    pool = pool[(pool["day_of_week"] == dow) & (pool["minute"].between(slot - 30, slot + 30))]
    for _ in range(min(new_patients, MAX_SURGE_PATIENTS - n_now)):
        if pool.empty:
            break
        r = pool.sample(1, random_state=rng.randint(0, 10 ** 6)).iloc[0]
        steps = r["planned_steps"].split("|")
        pre = [x for x in steps[: steps.index("CONSULT")] if x in PRE_TESTS] if "CONSULT" in steps else []
        k = n_now + len(added) + 1
        pid = f"SURGE_{k:02d}"
        svc.check_in(dict(patient_id=pid, display_name=f"{NAMES[(k - 1) % len(NAMES)]} (fictional)",
                          visit_type=r["visit_type"], referral_category=r["referral_category"],
                          clinician_priority=r["clinician_priority"], mobility=r["mobility"], age=int(r["age"]),
                          doctor_id=r["doctor_id"] if r["doctor_id"] in svc.cfg.get("doctors", []) else None,
                          pre_consult_tests=pre))
        p = svc._patient(pid)
        nxt = (p.get("recommendation") or {}).get("recommended_station")
        if nxt and nxt in svc.eligible(p):
            svc.confirm_destination(pid, nxt, source="staff")
            svc.arrive_at_station(pid)                       # joins that queue
        added.append(pid)
    return {"stations": changed, "patients": added}
