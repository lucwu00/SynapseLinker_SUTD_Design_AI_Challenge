"""Rule-constrained, ML-assisted routing.

Decision order (rules first, ML second):
  1. RULES  - escalated patients are never auto-routed (S02).
  2. RULES  - eligible steps = unfinished steps of the first unfinished stage (dependencies).
  3. OPS    - remove stations that are unavailable / closed (room or staff status).
  4. ML     - Model A predicts the wait at each eligible station; Model B predicts the
              probability that the station bottlenecks in the next 30 minutes.
  5. SEARCH - when several orders are permitted, every permitted order of the stage is scored
              with a lightweight look-ahead (travel + predicted wait + expected service, with
              waits at later stops pushed toward the bottleneck level in proportion to risk).
  6. STABILITY - keep the previous recommendation unless the new one saves at least
              max(3 min, 10%), and never reroute a patient whose destination is locked.

The engine never changes clinician priority, never skips a prerequisite and never diagnoses.
"""
from __future__ import annotations
import itertools
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from flow.dependencies import WorkflowRules, eligible_next, remaining_stages

ROOT = Path(__file__).resolve().parents[1]
ASSUMPTIONS = json.loads((ROOT / "config" / "operational_assumptions.json").read_text(encoding="utf-8"))


def load_station_config() -> dict:
    df = pd.read_csv(ROOT / "data" / "original" / "ortho_stations.csv")
    out = {}
    for r in df.itertuples(index=False):
        out[r.station] = dict(
            base_servers=None if r.base_servers == "per roster" else int(r.base_servers),
            service_mean=None if r.station == "CONSULT" else float(r.service_mean_min),
            service_sd=None if r.station == "CONSULT" else float(r.service_sd_min),
            opens=None if r.station == "CONSULT" else r.opens,
            shared=bool(r.shared_with_other_departments),
            external_rates=[float(x) for x in str(r.external_arrivals_per_hour_08_to_17).split("|")]
            if isinstance(r.external_arrivals_per_hour_08_to_17, str) else [],
            threshold=float(r.bottleneck_threshold_min))
    return out


STATIONS = load_station_config()


@dataclass
class StationView:
    """Live operational state of one station (what the hospital system knows now)."""
    station: str
    available: bool = True                 # room/staff ready (set by staff or sensors)
    is_open: bool = True
    queue_length: int = 0
    servers_on_duty: int = 1
    servers_busy: int = 0
    recent_mean_wait_30min: float | None = None
    arrivals_last_15min: int = 0
    booked_appointments_next_30min: float | None = None


@dataclass
class PatientContext:
    patient_id: str
    visit_type: str
    referral_category: str
    clinician_priority: str
    mobility: str
    age: int
    required_steps: list[str]
    completed_steps: list[str] = field(default_factory=list)
    minutes_since_clinic_arrival: float = 0.0
    escalated: bool = False
    locked_destination: str | None = None     # set once the patient is guided/queued
    previous_recommendation: str | None = None
    booked_order: list[str] | None = None     # tie-break: prefer the booked order


@dataclass
class ClinicContext:
    now_min: float                            # minutes since midnight
    day_of_week: int
    consult_completions_last_15min: int = 0
    total_consult_queue: int = 0
    doctor: dict | None = None                # consult-only features for this patient's doctor


def load_wait_profile() -> dict:
    d = json.loads((ROOT / "ml" / "models" / "wait_profile.json").read_text(encoding="utf-8"))
    d["profile"] = {st: {int(k): v for k, v in prof.items()} for st, prof in d["profile"].items()}
    return d


def expected_service(step: str, visit_type: str, mobility: str) -> float:
    if step == "REVIEW":
        return ASSUMPTIONS["consult_expected_min"]["review"]
    if step == "CONSULT":
        return ASSUMPTIONS["consult_expected_min"][visit_type]
    s = STATIONS[step]["service_mean"]
    if step == "XRAY" and mobility != "independent":
        s += ASSUMPTIONS["xray_extra_min_if_mobility_aid"]
    return s


def expected_travel(mobility: str) -> float:
    t = ASSUMPTIONS["travel_min"]
    return t["expected"] * t["mobility_factor"][mobility]


class RoutingEngine:
    def __init__(self, predictor, rules: WorkflowRules | None = None, travel_fn=None,
                 hysteresis_min: float | None = None, hysteresis_fraction: float | None = None):
        self.predictor = predictor
        self.rules = rules or WorkflowRules.load()
        self.travel_fn = travel_fn or (lambda patient, frm, to: expected_travel(patient.mobility))
        r = ASSUMPTIONS["routing"]
        self.h_min = r["hysteresis_min"] if hysteresis_min is None else hysteresis_min
        self.h_frac = r["hysteresis_fraction"] if hysteresis_fraction is None else hysteresis_fraction
        self.horizon = r["lookahead_horizon_min"]
        self.profile = load_wait_profile()

    # ------------------------------------------------------------------ features
    def wait_row(self, p: PatientContext, step: str, sv: StationView, c: ClinicContext) -> dict:
        station = self.rules.station_of(step)
        row = dict(station=station, visit_type=p.visit_type, referral_category=p.referral_category,
                   clinician_priority=p.clinician_priority, mobility=p.mobility, age=p.age,
                   join_hour=int(c.now_min // 60), day_of_week=c.day_of_week,
                   step_index=len(p.completed_steps), is_review=int(step == "REVIEW"),
                   minutes_since_clinic_arrival=p.minutes_since_clinic_arrival, station_open=int(sv.is_open),
                   queue_length_at_join=sv.queue_length, servers_on_duty=sv.servers_on_duty,
                   servers_busy=sv.servers_busy, recent_mean_wait_30min=sv.recent_mean_wait_30min,
                   arrivals_last_15min=sv.arrivals_last_15min,
                   consult_completions_last_15min=c.consult_completions_last_15min,
                   total_consult_queue=c.total_consult_queue)
        if station == "CONSULT" and c.doctor:
            row.update({k: c.doctor.get(k) for k in ("patients_ahead_for_doctor", "doctor_present",
                                                       "doctor_running_delay_min", "minutes_to_appointment")})
        return row

    @staticmethod
    def bn_row(sv: StationView, c: ClinicContext, completions: int) -> dict:
        return dict(station=sv.station, time_of_day_min=c.now_min, day_of_week=c.day_of_week,
                    queue_length=sv.queue_length, servers_on_duty=sv.servers_on_duty, servers_busy=sv.servers_busy,
                    recent_mean_wait_30min=sv.recent_mean_wait_30min, arrivals_last_15min=sv.arrivals_last_15min,
                    consult_completions_last_15min=completions,
                    booked_appointments_next_30min=sv.booked_appointments_next_30min)

    # ------------------------------------------------------------------ look-ahead
    def profile_at(self, station: str, tod_min: float) -> float | None:
        prof = self.profile["profile"].get(station)
        if not prof:
            return None
        b = int(tod_min // self.profile["bin_min"] * self.profile["bin_min"])
        b = min(max(b, min(prof)), max(prof))
        return prof.get(b, prof[min(prof, key=lambda k: abs(k - b))])

    def future_wait(self, station: str, now_wait: float, prob: float, threshold: float, now_min: float,
                    offset_min: float) -> float:
        """Wait expected if the patient joins `offset_min` minutes from now (lightweight look-ahead).

        1. Learned time-of-day profile + persistence: today's deviation from the usual level
           (Model A prediction minus profile) fades with the station's learned time constant tau.
        2. Model B adjustment (next 30 min only): with probability p the coming 30-min mean wait
           exceeds the threshold, so the projection is pulled toward p*max(x, thr) + (1-p)*min(x, thr).
        """
        d = max(offset_min, 0.0)
        p0, p1 = self.profile_at(station, now_min), self.profile_at(station, now_min + d)
        if p0 is None or p1 is None:
            proj = now_wait
        else:
            proj = max(p1 + (now_wait - p0) * math.exp(-d / self.profile["tau_min"].get(station, 60.0)), 0.0)
        if d <= self.horizon:
            a = d / self.horizon
            target = prob * max(proj, threshold) + (1 - prob) * min(proj, threshold)
            proj = (1 - a) * proj + a * target if d > 0 else now_wait
        return proj

    def sequence_cost(self, p, seq, preds, here, now_min) -> float:
        t, loc = 0.0, here
        for step in seq:
            t += self.travel_fn(p, loc, step)
            pw = preds[step]
            t += self.future_wait(self.rules.station_of(step), pw["wait"], pw["prob"], pw["threshold"],
                                  now_min, t) + pw["service"]
            loc = step
        return t

    # ------------------------------------------------------------------ main entry
    def recommend(self, p: PatientContext, stations: dict[str, StationView], c: ClinicContext,
                  explain: bool = True, bn_stations: dict[str, StationView] | None = None) -> dict:
        """`stations` are the views used for Model A (for CONSULT: the patient's own doctor).
        `bn_stations` (optional) are station-level views for Model B (for CONSULT: all doctors)."""
        bn_stations = bn_stations or stations
        rule_checks = []
        if p.escalated:
            return dict(status="ESCALATED", recommended_station=None, candidates=[], rule_checks=[
                "S02: patient flagged for staff attention - automatic routing suspended"],
                explanation="Escalated to staff. No automatic routing.", decision_source="rules")

        stages = self.rules.build_stages(p.required_steps, p.visit_type)
        eligible = eligible_next(stages, p.completed_steps)
        if not eligible:
            return dict(status="COMPLETE", recommended_station=None, candidates=[], rule_checks=[
                "All required services completed"], explanation="Journey complete.", decision_source="rules")
        later = [s for st in remaining_stages(stages, p.completed_steps)[1:] for s in st]
        rule_checks.append(f"Eligible now (prerequisites met): {eligible}")
        if later:
            rule_checks.append(f"Not yet eligible (unmet prerequisites): {later}")

        locked = False
        if p.locked_destination and p.locked_destination in eligible:
            sv = stations.get(self.rules.station_of(p.locked_destination))
            if sv and sv.available:
                rule_checks.append("Destination already confirmed; not rerouting")
                eligible, locked = [p.locked_destination], True

        avail = [s for s in eligible if stations.get(self.rules.station_of(s)) is not None
                 and stations[self.rules.station_of(s)].available]
        unavailable = sorted(set(eligible) - set(avail))
        if unavailable:
            rule_checks.append(f"Excluded - room/staff unavailable: {unavailable}")
        if not avail:
            return dict(status="WAIT_UNAVAILABLE", recommended_station=None, candidates=[], rule_checks=rule_checks,
                        decision_source="rules",
                        explanation=f"No eligible service is available right now ({unavailable}). Please wait; "
                                    "staff have been notified.")

        # ML predictions for every available eligible step
        wrows = [self.wait_row(p, s, stations[self.rules.station_of(s)], c) for s in avail]
        wp = self.predictor.predict_wait(wrows, with_range=explain)
        brows = [self.bn_row(bn_stations[self.rules.station_of(s)], c, c.consult_completions_last_15min) for s in avail]
        bp = self.predictor.predict_bottleneck(brows)
        preds = {}
        for s, w, b in zip(avail, wp, bp):
            st = self.rules.station_of(s)
            preds[s] = dict(wait=w["predicted_wait_min"], range=w.get("expected_range_min"),
                            prob=b["bottleneck_probability_next_30min"], alert=b["alert_level"],
                            threshold=STATIONS[st]["threshold"], service=expected_service(s, p.visit_type, p.mobility))

        here = p.completed_steps[-1] if p.completed_steps else None
        order_pref = {s: i for i, s in enumerate(p.booked_order or p.required_steps)}
        if len(avail) == 1:
            best_seq, seq_scores = [avail[0]], {(avail[0],): self.sequence_cost(p, avail, preds, here, c.now_min)}
        else:
            seq_scores = {perm: self.sequence_cost(p, perm, preds, here, c.now_min) for perm in itertools.permutations(avail)}
            best_seq = list(min(seq_scores, key=lambda k: (round(seq_scores[k], 6),
                                                          [order_pref.get(s, 99) for s in k])))
        best = best_seq[0]
        best_cost = seq_scores[tuple(best_seq)]

        changed = True
        prev = p.previous_recommendation
        if prev and prev in avail and prev != best and len(avail) > 1:
            prev_cost = min(v for k, v in seq_scores.items() if k[0] == prev)
            if prev_cost - best_cost <= max(self.h_min, self.h_frac * prev_cost):
                best, changed = prev, False
                best_seq = list(min((k for k in seq_scores if k[0] == prev), key=seq_scores.get))
                rule_checks.append("Stability: kept previous recommendation (saving below hysteresis)")
        elif prev == best:
            changed = False

        candidates = []
        for s in avail:
            d = preds[s]
            first_cost = min(v for k, v in seq_scores.items() if k[0] == s)
            candidates.append(dict(step=s, station=self.rules.station_of(s), predicted_wait_min=d["wait"],
                                   expected_range_min=d["range"], bottleneck_probability_next_30min=d["prob"],
                                   alert_level=d["alert"], expected_service_min=round(d["service"], 1),
                                   est_stage_completion_min=round(first_cost, 1)))
        candidates.sort(key=lambda x: x["est_stage_completion_min"])

        if locked:
            changed = False
        out = dict(status="ROUTE", recommended_station=best, recommended_sequence=best_seq, changed=changed,
                   candidates=candidates, rule_checks=rule_checks, decision_source="locked" if locked else "ml+rules",
                   model_versions=self.predictor.versions)
        if explain:
            out["explanation"] = self._explain(best, best_seq, candidates, len(avail))
        return out

    @staticmethod
    def _explain(best, seq, cands, n) -> str:
        c = next(x for x in cands if x["step"] == best)
        if n == 1:
            return (f"Next required service is {best}. Predicted wait {c['predicted_wait_min']:.0f} min "
                    f"(range {c['expected_range_min'][0]:.0f}-{c['expected_range_min'][1]:.0f}); "
                    f"bottleneck risk {c['alert_level']}.")
        alt = [x for x in cands if x["step"] != best]
        parts = ", ".join(f"{x['step']} {x['predicted_wait_min']:.0f} min wait / risk {x['alert_level']}" for x in alt)
        return (f"Several orders are clinically permitted. Recommended order {' -> '.join(seq)}: {best} has a "
                f"predicted wait of {c['predicted_wait_min']:.0f} min (risk {c['alert_level']}), and finishing this "
                f"group is estimated at {c['est_stage_completion_min']:.0f} min. Alternatives: {parts}. "
                "Waiting times are ML predictions; the allowed services come from the workflow rules.")
