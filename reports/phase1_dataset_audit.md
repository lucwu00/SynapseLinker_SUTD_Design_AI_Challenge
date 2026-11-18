# Synapse Linker — Phase 1: Dataset Audit

All four datasets are synthetic. They come from the team's discrete-event simulation of a fictional Orthopaedic Specialist Outpatient Clinic (seed 2026), and every row has `synthetic = 1`. The period covers 252 working days, 2 Jan – 31 Dec 2025, Mon–Fri only, excluding Singapore public holidays.

## 1. Audit table

| Dataset | Rows × cols | Grain | Role | Missing values (all structural, none to be zero-filled) |
|---|---|---|---|---|
| `ortho_service_events.csv` | 225,396 × 38 | One patient joining one station queue | Model A training | `recent_mean_wait_30min` 6.6% (no recent starts); consult-only fields 75.7% (blank for non-consult rows) |
| `ortho_station_snapshots.csv` | 100,800 × 17 | One station every 15 min, 08:00–17:45 | Model B training | `recent_mean_wait_30min` 19.5%; `booked_appointments_next_30min` 90% (CONSULT only) |
| `ortho_service_dependencies.csv` | 14 × 8 | One workflow rule | Routing rules (not training) | Blank cells mean "not applicable" |
| `ortho_appointments.csv` | 61,569 × 25 | One booked visit | Journey replay (not training) | Arrival and outcome fields 17% blank = no-shows |

There are no duplicate rows. Event volume is even across months (17.6k–20.6k).

## 2. Target definitions

- **Model A, `wait_min`:** minutes from joining a station queue to the start of service. It is zero-inflated: the share of zero waits ranges from 15% (pharmacy) to 93% (dressing).
- **Model B, `bottleneck_next_30min`:** set to 1 if the mean `wait_min` of patients who join the station in the window (t, t+30 min] is above that station's `bottleneck_threshold_min`. The thresholds are consult 45, X-ray 20, payment 20, ECG 20, pharmacy 30, phlebotomy 15, registration 10, cast/dressing/procedure 30. I rebuilt the label exactly from the events file for REGISTRATION (2,040 of 2,040 rows match). For shared stations the label also counts other-department patients, who are not in the CSVs.

## 3. Approved prediction-time features (Model A)

The prompt's list:
- `station`, `visit_type`, `referral_category`, `clinician_priority`, `mobility`, `age`
- `join_hour`, `day_of_week`
- `queue_length_at_join`, `servers_on_duty`, `servers_busy`, `recent_mean_wait_30min`, `arrivals_last_15min`
- `patients_ahead_for_doctor`, `doctor_running_delay_min`

**Proposed additions.** All are known when the patient joins the queue, and the backend can compute each from its live state:
- `step_index`, `is_review`, `minutes_since_clinic_arrival`, `station_open`
- `doctor_present`, `minutes_to_appointment`, `total_consult_queue`, `consult_completions_last_15min`

Consult-only fields are left blank for other stations. HistGradientBoosting handles blanks natively; for Ridge and RandomForest, blanks are imputed and a missing-indicator column is added.

## 4. Excluded columns

| Column(s) | Reason |
|---|---|
| `start_time`, `end_time`, `service_min`, `server_id` | Known only after service starts |
| `wait_min` | Target |
| `event_id`, `visit_id`, `patient_id`, `synthetic` | Identifiers or constant |
| `join_time`, `date` | Raw timestamps; already represented by hour and day |
| `month` | Test months never appear in training (forces extrapolation); no signal (single-feature R² 0.001) |
| `doctor_id` | Safe, but ties the model to one fictional roster; excluded for portability |
| Snapshots: `future_n_joiners_30min`, `future_mean_wait_30min` | Used to build the label |

**Leakage screen.** I trained a model on each feature alone and measured its R² on held-out rows. The strongest single features are `recent_mean_wait_30min` (0.58) and `queue_length_at_join` (0.56). These are genuine queueing signals. No feature reconstructs the target.

## 5. Chronological splits

| Period | Use |
|---|---|
| Jan – Jul | Train |
| Aug – Sep | Validation (tuning, threshold choice) |
| Oct – Dec (64 days) | Held-out test, used once; also the replay period |

- **Repeated patients:** 6,290 patients appear both before and after 1 Oct. No feature uses patient history, so this creates no leakage. No single visit spans the split.
- **Overlapping windows:** snapshots are 15 min apart and the label window is 30 min, so adjacent labels overlap within a day. Each simulated day starts with empty queues and no carried-over state. Splitting on whole dates therefore needs no extra purge gap. Validation folds will still be grouped by date.

## 6. Dataset relationships

```
appointments (visit_id, patient_id) --1:N--> service_events (visit_id)
service_events / snapshots  --join on (date, station)
dependencies --> rules applied to appointments.planned_steps
```

## 7. Routing dependency interpretation

The dependencies file is written for people to read. It uses probability ranges such as "0.20-0.60" and phrases such as "all pre-consult tests", so code cannot run it directly.

**Proposal:**
- The routing engine uses each visit's `planned_steps` as the services that visit requires.
- It applies the file's ordering rules, translated one-to-one into `config/workflow_rules.json`.

**What the rules allow today:**
- Only `pre_op_tests` (rule R05) is explicitly order-flexible. That covers **2,908 of 51,108 attended visits (5.7%)**.
- 879 follow-ups also need two tests before the consult (X-ray + blood test), but the rules say nothing about their order.
- Every other visit follows a fixed sequence, so the router has no choice to make.

Expected effect: a measurable improvement for the order-flexible subgroup, but only a small change in overall mean clinic time.

## 8. Historical replay feasibility

Feasible, with the replay run on the held-out Oct–Dec period (13,089 attended visits).

**Available in the four files:**
- Arrival times
- Required steps
- Clinician priority, doctor/session and mobility
- Each patient-step's observed service duration (100% present). Reusing these gives both scenarios identical service times, a technique known as common random numbers.

**Missing from the four files:**
- Other-department demand at the shared X-ray, blood test, ECG, payment and pharmacy stations
- Staff breaks and outages
- Doctor late-start times
- Station capacities

Capacities and other-department demand are in `ortho_stations.csv` and the generator script. Without them, shared stations would look almost empty and any comparison would be meaningless. These background events would be re-simulated with the same seed in both scenarios. They are context for the replay, not new training records.

## 9. Missing information / assumptions

1. Permission to use `ortho_stations.csv` and the generator's background assumptions in the replay.
2. Whether multiple pre-consult tests may be done in any order (affects 879 follow-ups).
3. Travel times between stations: no map yet. A fixed placeholder of 1.5–4 min × mobility factor is used until the classroom map exists.
4. ESP32 firmware protocol (HTTP polling, WebSocket or MQTT).
5. Dashboard stack and patient-phone interface.
6. Environment note: FastAPI and httpx cannot be installed in this build environment. All flow, ML and navigation logic will be plain Python, tested here with pytest. The FastAPI route tests run on your Windows machine after `pip install -r requirements.txt`.
