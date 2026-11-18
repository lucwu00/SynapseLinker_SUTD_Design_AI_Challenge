"""
CareFlow AI - Phase 2b: synthetic Orthopaedic Specialist Outpatient Clinic (SOC) data.

A discrete-event simulation of ONE hypothetical Singapore public-hospital Orthopaedic SOC.
Waiting times are NOT drawn from a formula: they emerge from appointment bookings,
patient punctuality, no-shows, doctor late starts, service durations, staffing, and
shared hospital services (X-ray, phlebotomy, pharmacy) that also serve other departments.

ALL RECORDS ARE FICTIONAL. Parameters are explicit, hypothetical assumptions (see CONFIG
and reports/synthetic_data_report.md). No real patient, CGH/SingHealth/NUHS/NHG record or
policy is reproduced. Must be recalibrated with real hospital data before any real use.

Pure Python (heapq) event engine, so no SimPy install is needed. Fixed seed => reproducible.

Usage (Windows):  python scripts\\generate_ortho_data.py
Outputs: data/synthetic/ortho_*.csv
"""
from __future__ import annotations
import csv, heapq, math, itertools
from collections import deque
from datetime import date, timedelta
from pathlib import Path
import numpy as np

SEED = 2026
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "synthetic"
rng = np.random.default_rng(SEED)

# ----------------------------------------------------------------------------- CONFIG
START, END = date(2025, 1, 2), date(2025, 12, 31)
SG_PUBLIC_HOLIDAYS_2025 = {date(2025, 1, 1), date(2025, 1, 29), date(2025, 1, 30), date(2025, 3, 31),
                           date(2025, 4, 18), date(2025, 5, 1), date(2025, 5, 12), date(2025, 10, 20),
                           date(2025, 12, 25)}

H = lambda h, m=0: h * 60 + m  # clock time -> minutes from midnight

# name: servers, mean/sd service minutes, open time, shared-with-other-departments, bottleneck threshold (min)
STATIONS = {
    "REGISTRATION": dict(servers=3, mean=3.0, sd=1.5, open=H(7, 45), shared=False, threshold=10),
    "XRAY":         dict(servers=4, mean=7.0, sd=3.0, open=H(8, 0), shared=True, threshold=20),
    "PHLEBOTOMY":   dict(servers=4, mean=5.0, sd=2.5, open=H(8, 0), shared=True, threshold=15),
    "ECG":          dict(servers=1, mean=10.0, sd=3.0, open=H(8, 0), shared=True, threshold=20),
    "CONSULT":      dict(servers=None, mean=None, sd=None, open=None, shared=False, threshold=45),
    "CAST_ROOM":    dict(servers=2, mean=18.0, sd=9.0, open=H(8, 30), shared=False, threshold=30),
    "DRESSING":     dict(servers=2, mean=12.0, sd=6.0, open=H(8, 30), shared=False, threshold=30),
    "PROCEDURE":    dict(servers=1, mean=15.0, sd=5.0, open=H(8, 30), shared=False, threshold=30),
    "PAYMENT":      dict(servers=4, mean=4.0, sd=2.0, open=H(8, 0), shared=True, threshold=20),
    "PHARMACY":     dict(servers=7, mean=6.0, sd=3.0, open=H(8, 0), shared=True, threshold=30),
}
# External (non-orthopaedic) arrivals per hour at shared stations, by clock hour 8..17
EXTERNAL_RATE = {
    "XRAY":       [8, 12, 14, 12, 8, 10, 12, 10, 8, 3],
    "PHLEBOTOMY": [50, 55, 45, 30, 18, 22, 24, 16, 8, 2],
    "ECG":        [3, 4, 4, 3, 2, 3, 3, 2, 1, 0],
    "PAYMENT":    [10, 20, 26, 26, 18, 18, 24, 24, 16, 6],
    "PHARMACY":   [20, 38, 50, 55, 50, 42, 46, 50, 36, 14],
}
SESSIONS = {"AM": (H(8, 30), 240), "PM": (H(14, 0), 180)}
N_DOCTORS = 12
DOCTORS_PER_SESSION = {"AM": 7, "PM": 5}
DOCTOR_ABSENT_P = 0.06
LATE_START_P, LATE_START_RANGE = 0.30, (10, 45)       # ward rounds / OT overrun
SLOT_MIN = {"new": 20, "follow_up": 10, "pre_op": 15}
OVERBOOK = 1.30                                       # compensates for no-shows
VISIT_MIX = {"new": 0.25, "follow_up": 0.70, "pre_op": 0.05}
NO_SHOW_P = {"new": 0.25, "follow_up": 0.15, "pre_op": 0.05}
CATEGORY_MIX = {
    "new":       {"Knee/Hip": .30, "Spine/Back": .20, "Shoulder/Elbow": .12, "Hand/Wrist": .10, "Foot/Ankle": .10, "Fracture": .18},
    "follow_up": {"Fracture": .25, "Knee/Hip": .25, "Post-op": .20, "Spine/Back": .12, "Shoulder/Elbow": .08, "Hand/Wrist": .05, "Foot/Ankle": .05},
    "pre_op":    {"Knee/Hip": .60, "Spine/Back": .15, "Shoulder/Elbow": .15, "Fracture": .10},
}
AGE_BY_CATEGORY = {"Knee/Hip": (66, 10), "Spine/Back": (58, 14), "Shoulder/Elbow": (55, 13), "Hand/Wrist": (48, 15),
                   "Foot/Ankle": (50, 16), "Fracture": (55, 22), "Post-op": (63, 13)}
MOBILITY_P = {  # (walking_aid, wheelchair)
    "Fracture": (.30, .15), "Post-op": (.35, .12), "Knee/Hip": (.25, .06), "Spine/Back": (.15, .05),
    "Shoulder/Elbow": (.03, .01), "Hand/Wrist": (.02, .01), "Foot/Ankle": (.30, .05)}
TRAVEL_FACTOR = {"independent": 1.0, "walking_aid": 1.6, "wheelchair": 1.4}
P_PRE_XRAY = {"new": {"Knee/Hip": .35, "Spine/Back": .20, "Shoulder/Elbow": .30, "Hand/Wrist": .25, "Foot/Ankle": .25, "Fracture": .60},
              "follow_up": {"Fracture": .85, "Post-op": .65, "Knee/Hip": .35, "Spine/Back": .10, "Shoulder/Elbow": .25, "Hand/Wrist": .40, "Foot/Ankle": .40}}
P_POST_XRAY_NEW = {"Fracture": .80, "default": .45}   # doctor orders X-ray + same-day review (new patients)
P_CAST = {("new", "Fracture"): .50, ("follow_up", "Fracture"): .35, ("follow_up", "Hand/Wrist"): .10, ("new", "Hand/Wrist"): .10}
P_DRESSING = {("follow_up", "Post-op"): .55}
P_PROCEDURE = {"Knee/Hip": .12, "Shoulder/Elbow": .15, "Spine/Back": .03}
P_PHARMACY = {"new": .55, "follow_up": .40, "pre_op": .10}
CONSULT_TIME = {"new": (22, 9), "follow_up": (11, 6), "pre_op": (13, 5), "review": (6, 3)}
URGENT_EXTERNAL_P = {"XRAY": .12, "PHLEBOTOMY": .10, "ECG": .20}   # ED/inpatient requests served first
COMPLEX_CASE = {"PAYMENT": (.10, 10, 6), "PHARMACY": (.15, 8, 5), "XRAY": (.05, 8, 4), "REGISTRATION": (.05, 6, 3)}  # (p, extra mean, sd)
OUTAGE_PER_DAY = 0.8                                  # unannounced server outages per shared station per day
PHARMACY_PREP = (15, 10)                              # minutes to prepare a prescription after consult
SNAPSHOT_TIMES = [H(8) + 15 * i for i in range(40)]   # 08:00 .. 17:45


# ----------------------------------------------------------------------------- helpers
def lognorm(mean, sd):
    s2 = math.log(1 + (sd / mean) ** 2)
    return float(rng.lognormal(math.log(mean) - s2 / 2, math.sqrt(s2)))

def choice(d):
    k = list(d); return k[rng.choice(len(k), p=np.array(list(d.values())) / sum(d.values()))]

def hhmm(day, m):
    return f"{day.isoformat()} {int(m // 60):02d}:{int(m % 60):02d}:{int(round((m % 1) * 60)) % 60:02d}"

def r1(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, 1)


class Station:
    def __init__(self, name, servers, open_at, doctor=None):
        self.name, self.on_duty, self.free = name, servers, 0
        self.open_at, self.doctor = open_at, doctor
        self.free_ids = list(range(1, servers + 1))
        self.rostered = servers            # what the hospital system knows (feature)
        self.pending_off, self.offline = 0, []   # unannounced breaks/outages (NOT visible as features)
        self.queue = []                    # heap of (key, seq, token)
        self.recent_starts = deque()       # (start_time, wait)
        self.recent_joins = deque()        # join times
        self.last_started_appt_delay = math.nan
        self.all_waits = []                # (join_time, wait) incl. external patients; used for labels only

    def busy(self):
        return max(self.on_duty - self.free, 0)

    def go_offline(self):              # a server stops (break, fault, staff called away)
        if self.on_duty <= 1: return False
        self.on_duty -= 1
        if self.free > 0 and self.free_ids:
            self.free -= 1; self.offline.append(self.free_ids.pop())
        else:
            self.pending_off += 1
        return True

    def go_online(self):
        self.on_duty += 1
        if self.pending_off > 0: self.pending_off -= 1
        else:
            self.free += 1; self.free_ids.append(self.offline.pop() if self.offline else self.rostered + 1)

    def release(self, sid):
        if self.pending_off > 0:
            self.pending_off -= 1; self.offline.append(sid)
        else:
            self.free += 1; self.free_ids.append(sid); self.free_ids.sort()

    def recent_wait(self, now, window=30):
        while self.recent_starts and self.recent_starts[0][0] < now - 60:
            self.recent_starts.popleft()
        w = [x for t, x in self.recent_starts if t >= now - window]
        return float(np.mean(w)) if w else math.nan

    def arrivals(self, now, window=15):
        while self.recent_joins and self.recent_joins[0] < now - 60:
            self.recent_joins.popleft()
        return sum(1 for t in self.recent_joins if t >= now - window)


class Token:
    __slots__ = ("id", "visit", "steps", "idx", "join", "key", "external", "wait_rec", "feat")
    def __init__(self, tid, visit, steps, external=False):
        self.id, self.visit, self.steps, self.idx = tid, visit, steps, 0
        self.external, self.join, self.key, self.feat = external, None, None, None


# ----------------------------------------------------------------------------- patients
patients = {}            # patient_id -> dict
pool = {c: [] for c in AGE_BY_CATEGORY}
pid_counter = itertools.count(1)

def new_patient(cat, first_seen):
    pid = f"P{next(pid_counter):06d}"
    m, s = AGE_BY_CATEGORY[cat]
    age = int(np.clip(rng.normal(m, s), 18, 95))
    sex = "F" if rng.random() < (0.58 if cat == "Knee/Hip" else 0.5) else "M"
    patients[pid] = dict(patient_id=pid, sex=sex, birth_year=START.year - age, primary_referral_category=cat,
                         first_seen_date=first_seen, synthetic=1)
    pool[cat].append(pid)
    return pid

for cat in AGE_BY_CATEGORY:                       # existing follow-up patients before 2025
    for _ in range(2500):
        new_patient(cat, "pre-2025")


def plan_steps(vt, cat):
    """Rule-based pathway (hypothetical). Returns ordered steps and a flexible-order group label."""
    pre, flex = [], ""
    if vt == "pre_op":
        pre = ["XRAY", "PHLEBOTOMY", "ECG"]; rng.shuffle(pre); flex = "pre_op_tests"
    elif rng.random() < P_PRE_XRAY[vt].get(cat, .3):
        pre = ["XRAY"]
    if vt == "follow_up" and rng.random() < .05:
        pre.append("PHLEBOTOMY")
    steps = ["REGISTRATION"] + pre + ["CONSULT"]
    if vt == "new" and "XRAY" not in pre and rng.random() < P_POST_XRAY_NEW.get(cat, P_POST_XRAY_NEW["default"]):
        steps += ["XRAY", "REVIEW"]
    if rng.random() < P_CAST.get((vt, cat), 0): steps.append("CAST_ROOM")
    if rng.random() < P_DRESSING.get((vt, cat), 0): steps.append("DRESSING")
    if vt != "pre_op" and rng.random() < P_PROCEDURE.get(cat, 0): steps.append("PROCEDURE")
    steps.append("PAYMENT")
    if rng.random() < P_PHARMACY[vt]: steps.append("PHARMACY")
    return steps, flex


# ----------------------------------------------------------------------------- simulate one day
visit_rows, event_rows, snapshot_rows = [], [], []
vid_counter, eid_counter, tok_counter = itertools.count(1), itertools.count(1), itertools.count(1)
doctor_speed = {f"DR{i:02d}": float(np.clip(rng.normal(1, .12), .75, 1.3)) for i in range(1, N_DOCTORS + 1)}
doctor_ids = list(doctor_speed)


def simulate_day(day: date):
    dow = day.weekday()
    demand = float(rng.normal(1.0, 0.05)) * {0: 1.05, 4: 0.97}.get(dow, 1.0)
    stations = {}
    for n, c in STATIONS.items():
        if n == "CONSULT": continue
        s = c["servers"]
        if n == "XRAY" and rng.random() < .2: s -= 1            # equipment downtime / staffing
        if n == "PHARMACY" and rng.random() < .25: s -= 1
        if n in ("CAST_ROOM", "DRESSING") and rng.random() < .1: s -= 1
        stations[n] = Station(n, s, c["open"])

    # roster: deterministic weekday pattern, random absences
    roster = {}
    for sess, k in DOCTORS_PER_SESSION.items():
        offset = (dow * 5 + (0 if sess == "AM" else 7)) % N_DOCTORS
        docs = [doctor_ids[(offset + i) % N_DOCTORS] for i in range(k)]
        roster[sess] = [d for d in docs if rng.random() > DOCTOR_ABSENT_P]
    doc_st = {}
    for sess, docs in roster.items():
        st, _ = SESSIONS[sess]
        for d in docs:
            late = rng.uniform(*LATE_START_RANGE) if rng.random() < LATE_START_P else rng.uniform(0, 10)
            s = Station("CONSULT", 1, st + late, doctor=d)
            s.session, s.session_start, s.late = sess, st, late
            doc_st[(d, sess)] = s

    ev, seq = [], itertools.count()
    push = lambda t, kind, *a: heapq.heappush(ev, (t, next(seq), kind, a))

    # bookings
    day_visits = []
    for (d, sess), s in doc_st.items():
        st, length = SESSIONS[sess]
        cap = length * OVERBOOK * demand
        t, booked = 0.0, []
        while True:
            vt = choice(VISIT_MIX); slot = SLOT_MIN[vt]
            if t + slot > cap: break
            booked.append((vt, st + min(t, length - 10) * 1.0)); t += slot
        for vt, appt in booked:
            appt = st + round((appt - st) / 5) * 5
            cat = choice(CATEGORY_MIX[vt])
            if vt == "new" or not pool[cat]:
                pid = new_patient(cat, day.isoformat())
            else:
                pid = pool[cat][rng.integers(len(pool[cat]))]
            age = START.year - patients[pid]["birth_year"] + (day.year - START.year)
            aid, wc = MOBILITY_P[cat]
            if age >= 75: aid, wc = aid + .10, wc + .05
            u = rng.random(); mob = "wheelchair" if u < wc else ("walking_aid" if u < wc + aid else "independent")
            prio = "expedited" if rng.random() < (.20 if (vt == "new" and cat == "Fracture") else .05) else "routine"
            no_show = rng.random() < NO_SHOW_P[vt]
            steps, flex = plan_steps(vt, cat)
            off = float(np.clip(rng.normal(-25 if age >= 65 else -15, 20), -120, 60))
            v = dict(visit_id=f"V{next(vid_counter):07d}", patient_id=pid, date=day.isoformat(), day_of_week=dow,
                     session=sess, doctor_id=d, appointment_min=appt, slot_minutes=SLOT_MIN[vt], visit_type=vt,
                     referral_category=cat, clinician_priority=prio, mobility=mob, age=age, sex=patients[pid]["sex"],
                     no_show=int(no_show), planned_steps="|".join(steps), flexible_order_group=flex,
                     arrival_offset_min=None if no_show else round(off, 1), arrival_min=None, exit_min=None,
                     consult_start_min=None, synthetic=1)
            day_visits.append(v)
            if not no_show:
                tok = Token(next(tok_counter), v, steps)
                push(max(appt + off, H(7, 30)), "arrive", tok)
    # externals at shared stations
    for n, rates in EXTERNAL_RATE.items():
        for h, r in enumerate(rates):
            r *= demand
            for t in np.sort(rng.uniform(H(8 + h), H(9 + h), rng.poisson(r))):
                push(float(t), "arrive", Token(next(tok_counter), None, [n], external=True))
    for s in list(stations.values()) + list(doc_st.values()):
        push(s.open_at, "open", s)
    for t in SNAPSHOT_TIMES:
        push(t, "snap")
    # staggered lunch breaks (12:00-14:00, one server at a time) and random unannounced outages
    for n in ("XRAY", "PHLEBOTOMY", "PAYMENT", "PHARMACY", "REGISTRATION"):
        s = stations[n]
        for t0 in (H(12), H(13)):
            push(t0, "off", s); push(t0 + 60, "on", s)
        for _ in range(rng.poisson(OUTAGE_PER_DAY)):
            t0 = rng.uniform(H(8, 30), H(16)); push(t0, "off", s); push(t0 + rng.uniform(15, 60), "on", s)

    consult_done_log = deque()

    def station_for(tok):
        n = tok.steps[tok.idx]
        if n in ("CONSULT", "REVIEW"):
            return doc_st[(tok.visit["doctor_id"], tok.visit["session"])]
        return stations[n]

    def try_start(s, now):
        while s.free > 0 and s.queue:
            _, _, tok = heapq.heappop(s.queue)
            s.free -= 1
            sid = s.free_ids.pop(0)
            wait = now - tok.join
            s.recent_starts.append((now, wait))
            s.all_waits.append((tok.join, wait))
            step = tok.steps[tok.idx]
            if tok.external:
                dur = lognorm(STATIONS[s.name]["mean"], STATIONS[s.name]["sd"])
                if s.name in COMPLEX_CASE and rng.random() < COMPLEX_CASE[s.name][0]: dur += lognorm(*COMPLEX_CASE[s.name][1:])
            else:
                v = tok.visit
                if s.name == "CONSULT":
                    key = "review" if step == "REVIEW" else v["visit_type"]
                    m, sd = CONSULT_TIME[key]
                    dur = lognorm(m, sd) * doctor_speed[s.doctor] * (1.1 if v["age"] >= 70 else 1) + (2 if v["mobility"] == "wheelchair" else 0)
                    if step == "CONSULT":
                        v["consult_start_min"] = now
                        s.last_started_appt_delay = now - v["appointment_min"]
                else:
                    c = STATIONS[s.name]
                    dur = lognorm(c["mean"], c["sd"])
                    if s.name in COMPLEX_CASE and rng.random() < COMPLEX_CASE[s.name][0]: dur += lognorm(*COMPLEX_CASE[s.name][1:])
                    if s.name == "XRAY" and v["mobility"] != "independent": dur += 4
                    if s.name in ("CAST_ROOM", "DRESSING") and v["age"] >= 75: dur *= 1.15
                tok.feat.update(start_min=now, wait_min=wait, service_min=dur, server_id=sid)
            push(now + dur, "done", tok, s, sid)

    def join(tok, now):
        s = station_for(tok)
        tok.join = now
        if tok.external:
            urgent = s.name in URGENT_EXTERNAL_P and rng.random() < URGENT_EXTERNAL_P[s.name]
            key = (0 if urgent else 1, now)
        elif s.name == "CONSULT":
            v = tok.visit; step = tok.steps[tok.idx]
            eff = now - 10 if step == "REVIEW" else max(v["appointment_min"], now - 30)
            key = (0 if v["clinician_priority"] == "expedited" else 1, eff)
        else:
            key = (1, now)
        tok.key = key
        if not tok.external:
            v = tok.visit
            all_consult_q = sum(len(x.queue) for x in doc_st.values())
            f = dict(event_id=f"E{next(eid_counter):08d}", visit_id=v["visit_id"], patient_id=v["patient_id"],
                     date=v["date"], day_of_week=v["day_of_week"], month=day.month, station=s.name,
                     step_name=tok.steps[tok.idx], step_index=tok.idx, is_review=int(tok.steps[tok.idx] == "REVIEW"),
                     visit_type=v["visit_type"], referral_category=v["referral_category"],
                     clinician_priority=v["clinician_priority"], mobility=v["mobility"], age=v["age"],
                     arrival_offset_min=v["arrival_offset_min"], join_min=now,
                     join_hour=int(now // 60), minutes_since_clinic_arrival=now - v["arrival_min"],
                     queue_length_at_join=len(s.queue), servers_on_duty=s.rostered,
                     servers_busy=s.busy() if now >= s.open_at else 0, station_open=int(now >= s.open_at),
                     recent_mean_wait_30min=s.recent_wait(now), arrivals_last_15min=s.arrivals(now),
                     consult_completions_last_15min=sum(1 for t in consult_done_log if t >= now - 15),
                     doctor_id=v["doctor_id"] if s.name == "CONSULT" else "",
                     patients_ahead_for_doctor=sum(1 for k, _, _ in s.queue if k < key) if s.name == "CONSULT" else None,
                     doctor_present=int(now >= s.open_at) if s.name == "CONSULT" else None,
                     doctor_running_delay_min=s.last_started_appt_delay if s.name == "CONSULT" else None,
                     minutes_to_appointment=v["appointment_min"] - now if s.name == "CONSULT" else None,
                     total_consult_queue=all_consult_q)
            tok.feat = f
        s.recent_joins.append(now)
        if s.name == "PHARMACY" and not tok.external:
            # prescription is prepared after the consult; patient cannot collect before it is ready
            ready = tok.visit["consult_end_min"] + lognorm(*PHARMACY_PREP)
            if ready > now:
                push(ready, "pharm_ready", tok, s); return
        heapq.heappush(s.queue, (key, next(seq), tok))
        try_start(s, now)

    while ev:
        now, _, kind, a = heapq.heappop(ev)
        if kind == "arrive":
            tok = a[0]
            if not tok.external: tok.visit["arrival_min"] = now
            join(tok, now)
        elif kind == "open":
            s = a[0]; s.free = s.on_duty; s.pending_off = 0; try_start(s, now)
        elif kind == "off":
            a[0].go_offline()
        elif kind == "on":
            a[0].go_online(); try_start(a[0], now)
        elif kind == "pharm_ready":
            tok, s = a
            heapq.heappush(s.queue, (tok.key, next(seq), tok)); try_start(s, now)
        elif kind == "done":
            tok, s, sid = a
            s.release(sid)
            if not tok.external:
                f = tok.feat; f["end_min"] = now
                event_rows.append(f)
                if s.name == "CONSULT":
                    consult_done_log.append(now); tok.visit["consult_end_min"] = now
                tok.idx += 1
                if tok.idx < len(tok.steps):
                    tr = rng.uniform(1.5, 4.0) * TRAVEL_FACTOR[tok.visit["mobility"]]
                    push(now + tr, "travel_done", tok)
                else:
                    tok.visit["exit_min"] = now
            try_start(s, now)
        elif kind == "travel_done":
            join(a[0], now)
        elif kind == "snap":
            groups = dict(stations); groups["CONSULT"] = None
            for n, s in groups.items():
                if n == "CONSULT":
                    docs = list(doc_st.values())
                    present = [x for x in docs if now >= x.open_at]
                    q = sum(len(x.queue) for x in docs); busy = sum(1 for x in present if x.free == 0)
                    rw = [x.recent_wait(now) for x in docs]; rw = [x for x in rw if not math.isnan(x)]
                    rec = dict(queue_length=q, servers_on_duty=len(present), servers_busy=busy,
                               recent_mean_wait_30min=float(np.mean(rw)) if rw else math.nan,
                               arrivals_last_15min=sum(x.arrivals(now) for x in docs),
                               booked_appointments_next_30min=sum(1 for v in day_visits if now <= v["appointment_min"] < now + 30))
                else:
                    rec = dict(queue_length=len(s.queue), servers_on_duty=s.rostered if now >= s.open_at else 0,
                               servers_busy=s.busy() if now >= s.open_at else 0,
                               recent_mean_wait_30min=s.recent_wait(now), arrivals_last_15min=s.arrivals(now),
                               booked_appointments_next_30min=None)
                rec.update(date=day.isoformat(), day_of_week=dow, month=day.month, time_min=now, station=n,
                           consult_completions_last_15min=sum(1 for t in consult_done_log if t >= now - 15))
                snapshot_rows.append(rec)

    waits = {n: [] for n in STATIONS}
    for x in list(stations.values()) + list(doc_st.values()):
        waits[x.name].extend(x.all_waits)
    return day_visits, waits


def label_snapshots(day_snaps, day_waits):
    """Prospective label: mean wait of everyone who joins the station in (t, t+30] exceeds the threshold.
    Uses future information BY DESIGN - it is the label, never a feature."""
    for r in day_snaps:
        w = [x for t, x in day_waits.get(r["station"], []) if r["time_min"] < t <= r["time_min"] + 30]
        thr = STATIONS[r["station"]]["threshold"]
        r["future_n_joiners_30min"] = len(w)
        r["future_mean_wait_30min"] = float(np.mean(w)) if w else math.nan
        r["bottleneck_threshold_min"] = thr
        r["bottleneck_next_30min"] = int(bool(w) and float(np.mean(w)) > thr)


# ----------------------------------------------------------------------------- output
def write_csv(path, rows, cols):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (r1(v) if isinstance(v, float) else v) for k, v in r.items() if k in cols})


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    day, n_days = START, 0
    while day <= END:
        if day.weekday() < 5 and day not in SG_PUBLIC_HOLIDAYS_2025:
            k = len(snapshot_rows)
            vis, waits = simulate_day(day)
            label_snapshots(snapshot_rows[k:], waits)
            visit_rows.extend(vis); n_days += 1
        day += timedelta(days=1)

    # clock-time columns
    for v in visit_rows:
        d = date.fromisoformat(v["date"])
        v["appointment_time"] = hhmm(d, v["appointment_min"])
        v["arrival_time"] = hhmm(d, v["arrival_min"]) if v["arrival_min"] is not None else ""
        v["consult_start_time"] = hhmm(d, v["consult_start_min"]) if v["consult_start_min"] is not None else ""
        v["clinic_exit_time"] = hhmm(d, v["exit_min"]) if v["exit_min"] is not None else ""
        v["consult_wait_from_appointment_min"] = (v["consult_start_min"] - v["appointment_min"]) if v["consult_start_min"] is not None else None
        v["total_time_in_clinic_min"] = (v["exit_min"] - v["arrival_min"]) if v["exit_min"] is not None else None
        v["n_steps"] = len(v["planned_steps"].split("|"))
    for e in event_rows:
        d = date.fromisoformat(e["date"])
        e["join_time"], e["start_time"], e["end_time"] = (hhmm(d, e[k]) for k in ("join_min", "start_min", "end_min"))
        e["synthetic"] = 1
    for s in snapshot_rows:
        s["snapshot_time"] = hhmm(date.fromisoformat(s["date"]), s["time_min"]); s["synthetic"] = 1

    write_csv(OUT / "ortho_patients.csv", list(patients.values()),
              ["patient_id", "sex", "birth_year", "primary_referral_category", "first_seen_date", "synthetic"])
    write_csv(OUT / "ortho_appointments.csv", visit_rows,
              ["visit_id", "patient_id", "date", "day_of_week", "session", "doctor_id", "appointment_time", "slot_minutes",
               "visit_type", "referral_category", "clinician_priority", "mobility", "age", "sex", "no_show",
               "planned_steps", "n_steps", "flexible_order_group", "arrival_time", "arrival_offset_min",
               "consult_start_time", "consult_wait_from_appointment_min", "clinic_exit_time", "total_time_in_clinic_min", "synthetic"])
    write_csv(OUT / "ortho_service_events.csv", event_rows, EVENT_COLS)
    write_csv(OUT / "ortho_station_snapshots.csv", snapshot_rows, SNAP_COLS)
    write_reference_tables()
    print(f"days={n_days} patients={len(patients)} appointments={len(visit_rows)} "
          f"events={len(event_rows)} snapshots={len(snapshot_rows)}")


EVENT_COLS = ["event_id", "visit_id", "patient_id", "date", "day_of_week", "month", "station", "step_name", "step_index",
              "is_review", "visit_type", "referral_category", "clinician_priority", "mobility", "age", "arrival_offset_min",
              "join_time", "join_hour", "minutes_since_clinic_arrival", "station_open", "queue_length_at_join",
              "servers_on_duty", "servers_busy", "recent_mean_wait_30min", "arrivals_last_15min",
              "consult_completions_last_15min", "total_consult_queue", "doctor_id", "patients_ahead_for_doctor",
              "doctor_present", "doctor_running_delay_min", "minutes_to_appointment",
              "wait_min", "start_time", "end_time", "service_min", "server_id", "synthetic"]
SNAP_COLS = ["date", "day_of_week", "month", "snapshot_time", "station", "queue_length", "servers_on_duty", "servers_busy",
             "recent_mean_wait_30min", "arrivals_last_15min", "consult_completions_last_15min",
             "booked_appointments_next_30min", "future_n_joiners_30min", "future_mean_wait_30min",
             "bottleneck_threshold_min", "bottleneck_next_30min", "synthetic"]


def write_reference_tables():
    deps = [  # pathway_rule_id, visit_type, referral_category, step, must_follow, probability, flexible_group, note
        ("R01", "all", "all", "REGISTRATION", "", 1.0, "", "Every visit starts at registration"),
        ("R02", "new", "per category", "XRAY", "REGISTRATION", "0.20-0.60", "", "X-ray requested on referral, done before consult"),
        ("R03", "follow_up", "per category", "XRAY", "REGISTRATION", "0.10-0.85", "", "X-ray ordered at previous visit; consult needs the image"),
        ("R04", "follow_up", "all", "PHLEBOTOMY", "REGISTRATION", 0.05, "", "Monitoring blood test before consult"),
        ("R05", "pre_op", "all", "XRAY;PHLEBOTOMY;ECG", "REGISTRATION", 1.0, "pre_op_tests", "All three before consult, ANY order (routing may choose)"),
        ("R06", "all", "all", "CONSULT", "all pre-consult tests", 1.0, "", "Consult requires pre-consult results"),
        ("R07", "new", "per category", "XRAY -> REVIEW", "CONSULT", "0.45 (Fracture 0.80)", "", "Doctor-ordered X-ray then same-day review with the same doctor"),
        ("R08", "new/follow_up", "Fracture, Hand/Wrist", "CAST_ROOM", "CONSULT (and REVIEW if any)", "0.10-0.50", "", "Cast application/change/removal"),
        ("R09", "follow_up", "Post-op", "DRESSING", "CONSULT", 0.55, "", "Wound dressing / suture removal"),
        ("R10", "new/follow_up", "Knee/Hip, Shoulder/Elbow, Spine/Back", "PROCEDURE", "CONSULT", "0.03-0.15", "", "Injection in procedure room"),
        ("R11", "all", "all", "PAYMENT", "all clinical steps", 1.0, "", "Payment and next-appointment booking"),
        ("R12", "all", "all", "PHARMACY", "PAYMENT", "0.10-0.55", "", "Medication collection (hospital-wide shared pharmacy)"),
        ("S01", "all", "all", "CONSULT queue order", "", "", "", "Clinician-assigned 'expedited' patients first, then appointment time"),
        ("S02", "all", "all", "ESCALATION", "", "", "", "Any patient flagged unwell is escalated to staff; never auto-routed"),
    ]
    with open(OUT / "ortho_service_dependencies.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rule_id", "visit_type", "referral_category", "step", "must_follow", "probability", "flexible_order_group", "description"])
        w.writerows(deps)
    with open(OUT / "ortho_stations.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["station", "base_servers", "service_mean_min", "service_sd_min", "opens", "shared_with_other_departments",
                    "external_arrivals_per_hour_08_to_17", "bottleneck_threshold_min"])
        for n, c in STATIONS.items():
            op = f"{c['open']//60:02d}:{c['open']%60:02d}" if c["open"] else "per doctor session"
            w.writerow([n, c["servers"] or "per roster", c["mean"] or "by visit type", c["sd"] or "", op, c["shared"],
                        "|".join(map(str, EXTERNAL_RATE.get(n, []))), c["threshold"]])


if __name__ == "__main__":
    main()
