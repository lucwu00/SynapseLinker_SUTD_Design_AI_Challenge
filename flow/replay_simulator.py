"""Before/after replay: queue-based discrete-event re-run of historical (synthetic) clinic days.

Why not just reorder the historical timestamps? Those timestamps are outcomes of the original
process: changing one patient's order changes everyone else's queues. So each day is
re-simulated with queues, servers and prerequisites, and only the routing policy differs.

Identical across policies (common random numbers):
  * orthopaedic patients: actual arrival times, required steps, priority, doctor/session (ortho_appointments.csv)
  * each patient-step's service duration: the observed value from ortho_service_events.csv
  * doctor start time per session: first observed consult start
  * rostered servers per station per day: observed servers_on_duty
  * other-department demand, staff breaks, outages, pharmacy preparation, travel times:
    re-simulated from ortho_stations.csv + config/operational_assumptions.json with fixed seeds
Routing decisions never see future service durations: the router only uses live queue state,
ML predictions and expected (mean) service times.

Policies:
  fixed           - booked order (planned_steps), the pre-Synapse process
  shortest_queue  - non-ML heuristic: fewest people per server
  synapse         - Synapse Linker routing engine (Model A + Model B + rules + look-ahead)

Usage:  python -m flow.replay_simulator                  (Oct-Dec test period, all policies)
        python -m flow.replay_simulator --days 5         (quick run)
"""
from __future__ import annotations
import argparse, heapq, itertools, json, math, time
from collections import deque, defaultdict
from datetime import date as Date
from pathlib import Path

import numpy as np
import pandas as pd

from flow.dependencies import WorkflowRules, eligible_next
from flow.routing_engine import (RoutingEngine, StationView, PatientContext, ClinicContext, STATIONS, ASSUMPTIONS)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "original"
REPORTS = ROOT / "reports"
A = ASSUMPTIONS
STATION_IDX = {s: i for i, s in enumerate(STATIONS)}
SNAP_TIMES = [8 * 60 + 15 * i for i in range(40)]


def hm(s: str) -> float:
    h, m = s.split(":")[:2]
    return int(h) * 60 + int(m)


def clock(ts: str) -> float:
    t = ts[11:]
    return int(t[0:2]) * 60 + int(t[3:5]) + int(t[6:8]) / 60


def lognorm(rng, mean, sd):
    s2 = math.log(1 + (sd / mean) ** 2)
    return float(rng.lognormal(math.log(mean) - s2 / 2, math.sqrt(s2)))


# ============================================================================ inputs
def load_inputs(start="2025-10-01", end="2025-12-31"):
    ap = pd.read_csv(DATA / "ortho_appointments.csv")
    ap = ap[(ap.date >= start) & (ap.date <= end) & (ap.no_show == 0)].copy()
    ev = pd.read_csv(DATA / "ortho_service_events.csv",
                     usecols=["visit_id", "date", "station", "step_name", "step_index", "service_min", "start_time",
                              "servers_on_duty", "wait_min", "doctor_id"])
    ev = ev[(ev.date >= start) & (ev.date <= end)]
    ev = ev.merge(ap[["visit_id", "session"]], on="visit_id", how="left")
    durations = defaultdict(lambda: defaultdict(list))
    for r in ev.sort_values(["visit_id", "step_index"]).itertuples(index=False):
        durations[r.visit_id][r.step_name].append(float(r.service_min))
    cons = ev[ev.station == "CONSULT"].copy()
    cons["start"] = cons.start_time.map(clock)
    doc_open = cons.groupby(["date", "doctor_id", "session"]).start.min().to_dict()
    servers = ev.groupby(["date", "station"]).servers_on_duty.agg(lambda s: int(s.mode().iloc[0])).to_dict()
    return ap, ev, durations, doc_open, servers


# ============================================================================ engine
class Station:
    def __init__(self, name, servers, open_at):
        self.name, self.rostered, self.on_duty, self.free, self.open_at = name, servers, servers, 0, open_at
        self.pending_off = 0
        self.queue, self.recent_starts, self.recent_joins = [], deque(), deque()
        self.all_waits, self.busy_min, self.queue_samples = [], 0.0, []
        self.last_appt_delay = math.nan

    def busy(self):
        return max(self.on_duty - self.free, 0)

    def recent_wait(self, now, window=30):
        while self.recent_starts and self.recent_starts[0][0] < now - 60:
            self.recent_starts.popleft()
        w = [x for t, x in self.recent_starts if t >= now - window]
        return float(np.mean(w)) if w else None

    def arrivals(self, now, window=15):
        while self.recent_joins and self.recent_joins[0] < now - 60:
            self.recent_joins.popleft()
        return sum(1 for t in self.recent_joins if t >= now - window)

    def go_off(self):
        if self.on_duty <= 1: return
        self.on_duty -= 1
        if self.free > 0: self.free -= 1
        else: self.pending_off += 1

    def go_on(self):
        self.on_duty += 1
        if self.pending_off > 0: self.pending_off -= 1
        else: self.free += 1

    def release(self):
        if self.pending_off > 0: self.pending_off -= 1
        else: self.free += 1


class Token:
    __slots__ = ("visit", "stages", "completed", "step", "join", "external", "dur", "hop", "arrival",
                 "waits", "changes", "decisions", "flexible", "consult_end", "prev_rec", "wby")

    def __init__(self, external=False):
        self.external, self.completed, self.waits, self.changes, self.decisions, self.hop = external, [], 0.0, 0, 0, 0
        self.consult_end, self.prev_rec, self.wby = None, None, defaultdict(float)


def day_seed(day: str, *parts) -> np.random.Generator:
    return np.random.default_rng([A["seed"], Date.fromisoformat(day).toordinal(), *parts])


def simulate_day(day, visits, durations, doc_open, servers, policy, engine=None, rules=None):
    dow = Date.fromisoformat(day).weekday()
    st = {}
    for name, cfg in STATIONS.items():
        if name == "CONSULT": continue
        st[name] = Station(name, servers.get((day, name), cfg["base_servers"]), hm(cfg["opens"]))
    docs = {}
    for (d, doc, sess), t in doc_open.items():
        if d == day:
            docs[(doc, sess)] = Station("CONSULT", 1, t)

    ev, seq = [], itertools.count()
    push = lambda t, k, *a: heapq.heappush(ev, (t, next(seq), k, a))

    # ---- background conditions (identical for every policy)
    dfac = A["day_demand_factor"].get(str(dow), 1.0)
    for name, s in st.items():
        rng = day_seed(day, STATION_IDX[name], 1)
        for h, rate in enumerate(STATIONS[name]["external_rates"]):
            for t in np.sort(rng.uniform((8 + h) * 60, (9 + h) * 60, rng.poisson(rate * dfac))):
                tok = Token(external=True)
                tok.step = name
                tok.dur = lognorm(rng, STATIONS[name]["service_mean"], STATIONS[name]["service_sd"])
                cc = A["complex_case"].get(name)
                if cc and rng.random() < cc[0]: tok.dur += lognorm(rng, cc[1], cc[2])
                tok.flexible = 0 if rng.random() < A["external_urgent_probability"].get(name, 0) else 1  # priority key
                push(float(t), "arrive_ext", tok, s)
        orng = day_seed(day, STATION_IDX[name], 2)
        if name in A["lunch_breaks"]["stations"]:
            for t0 in A["lunch_breaks"]["starts"]:
                push(hm(t0), "off", s); push(hm(t0) + A["lunch_breaks"]["minutes"], "on", s)
        if STATIONS[name]["shared"] or name == "REGISTRATION":
            for _ in range(orng.poisson(A["outages_per_station_per_day"])):
                t0 = orng.uniform(8.5 * 60, 16 * 60)
                push(t0, "off", s); push(t0 + orng.uniform(*A["outage_minutes"]), "on", s)
    for s in list(st.values()) + list(docs.values()):
        push(s.open_at, "open", s)
    for t in SNAP_TIMES:
        push(t, "snap")

    # ---- orthopaedic patients
    tokens = []
    for v in visits:
        tok = Token()
        tok.visit = v
        steps = v["planned_steps"].split("|")
        tok.stages = rules.build_stages(steps, v["visit_type"])
        tok.flexible = int(any(len(sg) > 1 for sg in tok.stages))
        tok.dur = {k: list(x) for k, x in durations[v["visit_id"]].items()}
        tok.arrival = clock(v["arrival_time"])
        tokens.append(tok)
        push(tok.arrival, "arrive", tok)
    consult_done = deque()

    def station_of(tok):
        if tok.step in ("CONSULT", "REVIEW"):
            return docs[(tok.visit["doctor_id"], tok.visit["session"])]
        return st[tok.step]

    def try_start(s, now):
        if now < s.open_at: return
        while s.free > 0 and s.queue:
            _, _, tok = heapq.heappop(s.queue)
            s.free -= 1
            w = now - tok.join
            s.recent_starts.append((now, w)); s.all_waits.append((tok.join, w))
            if tok.external:
                d = tok.dur
            else:
                tok.waits += w
                tok.wby[s.name] += w
                lst = tok.dur.get(tok.step)
                d = lst.pop(0) if lst else STATIONS.get(s.name, {}).get("service_mean") or 10.0
                if tok.step == "CONSULT":
                    s.last_appt_delay = now - clock(tok.visit["appointment_time"])
            s.busy_min += d
            push(now + d, "done", tok, s)

    def join(tok, now):
        s = station_of(tok)
        tok.join = now
        if s.name == "CONSULT":
            appt = clock(tok.visit["appointment_time"])
            eff = now - 10 if tok.step == "REVIEW" else max(appt, now - 30)
            key = (0 if tok.visit["clinician_priority"] == "expedited" else 1, eff)
        else:
            key = (1, now)
        s.recent_joins.append(now)
        if s.name == "PHARMACY" and tok.consult_end is not None:
            prng = np.random.default_rng([A["seed"], int(tok.visit["visit_id"][1:]), 7])
            ready = tok.consult_end + lognorm(prng, *A["pharmacy_prep_min"])
            if ready > now:
                push(ready, "ready", tok, s, key); return
        heapq.heappush(s.queue, (key, next(seq), tok)); try_start(s, now)

    def views(now):
        out = {n: StationView(n, True, now >= s.open_at, len(s.queue), s.rostered, s.busy() if now >= s.open_at else 0,
                              s.recent_wait(now), s.arrivals(now)) for n, s in st.items()}
        return out

    def choose(tok, eligible, now):
        booked = tok.visit["planned_steps"].split("|")
        default = min(eligible, key=lambda x: booked.index(x))
        if len(eligible) == 1 or policy == "fixed":
            return default
        tok.decisions += 1
        if policy == "shortest_queue":
            vs = views(now)
            pick = min(eligible, key=lambda x: (len(st[x].queue) / max(st[x].rostered, 1), booked.index(x)))
        else:
            v = tok.visit
            pc = PatientContext(v["patient_id"], v["visit_type"], v["referral_category"], v["clinician_priority"],
                                v["mobility"], int(v["age"]), booked, list(tok.completed),
                                now - tok.arrival, booked_order=booked,
                                previous_recommendation=default)   # booked order is the incumbent
            cc = ClinicContext(now, dow, sum(1 for t in consult_done if t >= now - 15),
                               sum(len(d.queue) for d in docs.values()))
            rec = engine.recommend(pc, views(now), cc, explain=False)
            pick = rec["recommended_station"] or default
        if pick != default: tok.changes += 1
        return pick

    def advance(tok, now):
        el = eligible_next(tok.stages, tok.completed)
        if not el:
            tok.visit["_exit"] = now; return
        tok.step = choose(tok, el, now)
        rng = np.random.default_rng([A["seed"], int(tok.visit["visit_id"][1:]), 3, tok.hop])
        tok.hop += 1
        travel = rng.uniform(*A["travel_min"]["range"]) * A["travel_min"]["mobility_factor"][tok.visit["mobility"]]
        push(now + travel, "travel_done", tok)

    while ev:
        now, _, kind, a = heapq.heappop(ev)
        if kind == "arrive":
            tok = a[0]; tok.step = "REGISTRATION"; join(tok, now)
        elif kind == "arrive_ext":
            tok, s = a; tok.join = now; s.recent_joins.append(now)
            heapq.heappush(s.queue, ((tok.flexible, now), next(seq), tok)); try_start(s, now)
        elif kind == "open":
            s = a[0]; s.free = s.on_duty - s.pending_off; s.pending_off = 0; try_start(s, now)
        elif kind == "off":
            a[0].go_off()
        elif kind == "on":
            a[0].go_on(); try_start(a[0], now)
        elif kind == "ready":
            tok, s, key = a; heapq.heappush(s.queue, (key, next(seq), tok)); try_start(s, now)
        elif kind == "travel_done":
            join(a[0], now)
        elif kind == "done":
            tok, s = a
            s.release()
            if not tok.external:
                tok.completed.append(tok.step)
                if s.name == "CONSULT":
                    consult_done.append(now); tok.consult_end = now
                advance(tok, now)
            try_start(s, now)
        elif kind == "snap":
            for n, s in st.items():
                s.queue_samples.append(len(s.queue))

    # ---- collect
    vis = [dict(visit_id=t.visit["visit_id"], date=day, visit_type=t.visit["visit_type"], flexible=t.flexible,
                total_time=t.visit.get("_exit", math.nan) - t.arrival, total_wait=t.waits, route_changes=t.changes,
                decisions=t.decisions, consult_wait=t.wby["CONSULT"],
                test_wait=t.wby["XRAY"] + t.wby["PHLEBOTOMY"] + t.wby["ECG"],
                post_wait=t.wby["PAYMENT"] + t.wby["PHARMACY"]) for t in tokens]
    stat = []
    consult_waits = [w for d in docs.values() for w in d.all_waits]
    groups = {**{n: s for n, s in st.items()}}
    for n, s in groups.items():
        waits = s.all_waits
        bn = 0
        for t in SNAP_TIMES:
            w = [x for j, x in waits if t < j <= t + 30]
            bn += int(bool(w) and np.mean(w) > STATIONS[n]["threshold"])
        last = max([j + x for j, x in waits], default=s.open_at)
        cap = s.rostered * max(min(last, 18 * 60) - s.open_at, 1)
        stat.append(dict(date=day, station=n, mean_wait_all=np.mean([x for _, x in waits]) if waits else 0.0,
                         mean_queue=float(np.mean(s.queue_samples)), bottleneck_snapshots=bn,
                         snapshots=len(SNAP_TIMES), utilisation=min(s.busy_min / cap, 1.5)))
    bn = sum(int(bool(w) and np.mean(w) > STATIONS["CONSULT"]["threshold"])
             for t in SNAP_TIMES for w in [[x for j, x in consult_waits if t < j <= t + 30]])
    stat.append(dict(date=day, station="CONSULT", mean_wait_all=np.mean([x for _, x in consult_waits]) if consult_waits else 0,
                     mean_queue=math.nan, bottleneck_snapshots=bn, snapshots=len(SNAP_TIMES), utilisation=math.nan))
    for t in tokens:
        t.visit.pop("_exit", None)
    return vis, stat


# ============================================================================ runner
def run(policies=("fixed", "shortest_queue", "synapse"), days=None, start="2025-10-01", end="2025-12-31",
        verbose=True, engine_kwargs=None):
    rules = WorkflowRules.load()
    ap, ev, durations, doc_open, servers = load_inputs(start, end)
    all_days = sorted(ap.date.unique())
    if days: all_days = all_days[:days]
    engine = None
    if "synapse" in policies:
        from flow.predictor import Predictor
        engine = RoutingEngine(Predictor(), rules, **(engine_kwargs or {}))
    by_day = {d: g.to_dict("records") for d, g in ap.groupby("date")}
    results = {}
    for pol in policies:
        t0, V, S = time.time(), [], []
        for d in all_days:
            v, s = simulate_day(d, by_day[d], durations, doc_open, servers, pol, engine, rules)
            V += v; S += s
        results[pol] = (pd.DataFrame(V), pd.DataFrame(S))
        if verbose:
            print(f"{pol:15s} {len(all_days)} days, {len(V)} visits, {time.time()-t0:.0f}s, "
                  f"mean total time {pd.DataFrame(V).total_time.mean():.1f} min")
    return results, ap[ap.date.isin(all_days)], ev[ev.date.isin(all_days)]


def summarise(results):
    rows = []
    for pol, (v, s) in results.items():
        for grp, mask in [("all visits", v.flexible >= 0), ("order-flexible visits", v.flexible == 1)]:
            g = v[mask]
            rows.append(dict(policy=pol, group=grp, visits=len(g), mean_total_min=g.total_time.mean(),
                             median_total_min=g.total_time.median(), p90_total_min=g.total_time.quantile(.9),
                             mean_wait_min=g.total_wait.mean(), route_changes=int(g.route_changes.sum()),
                             incomplete=int(g.total_time.isna().sum())))
    return pd.DataFrame(rows)


def station_summary(results):
    rows = []
    for pol, (_, s) in results.items():
        g = s.groupby("station").agg(mean_wait=("mean_wait_all", "mean"), mean_queue=("mean_queue", "mean"),
                                     bottleneck_rate=("bottleneck_snapshots", "sum"), snaps=("snapshots", "sum"),
                                     utilisation=("utilisation", "mean"))
        g["bottleneck_rate"] = g.bottleneck_rate / g.snaps
        g["policy"] = pol
        rows.append(g.drop(columns="snaps").reset_index())
    return pd.concat(rows)


if __name__ == "__main__":
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--days", type=int, default=None)
    ap_.add_argument("--policies", default="fixed,shortest_queue,synapse")
    a = ap_.parse_args()
    res, _, _ = run(tuple(a.policies.split(",")), a.days)
    print(summarise(res).round(2).to_string(index=False))
