# Synapse Linker API

The interactive docs (Swagger) are at `http://localhost:8000/docs` while the backend runs. Errors return `{"error": "..."}`, or `{"detail": [...]}` for validation errors, with these status codes:
- 404: unknown resource
- 409: conflicting state (for example a closed room or an escalated patient)
- 422: invalid input or a broken workflow rule

## Patients and journeys

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/patients/check-in` | `patient_id, visit_type, referral_category, age, [clinician_priority, mobility, doctor_id, appointment_time "HH:MM", pre_consult_tests[], post_consult_steps[], pharmacy, robot_id]` | Registration counts as done. Triggers the inference loop |
| GET | `/patients/{id}/journey` | | Required, completed and eligible steps, current queue, history with predicted vs actual waits |
| POST | `/patients/{id}/destination` | `step, [robot_id]` | Confirms a destination. Re-checks rules and availability, then creates a robot task |
| POST | `/patients/{id}/arrived` | | Joins the queue (the robot does this automatically on arrival) |
| POST | `/patients/{id}/service-started` | | Staff |
| POST | `/patients/{id}/service-completed` | `[step, added_steps[], recorded_by]` | Staff. A clinician can add X-ray, review, cast, dressing, procedure or pharmacy at consult/review |
| POST | `/patients/{id}/escalate` | `reason, active` | Suspends automatic routing |

## Predictions and flow

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/predict/waiting-time` | `{patient_id, station}` (live) or `{features}` | `predicted_wait_min, expected_range_min, model_version, prediction_timestamp, missing_optional_inputs, limitations` |
| POST | `/predict/bottleneck` | `{station}` (live) or `{features}` | `bottleneck_probability_next_30min, alert_level (LOW/MEDIUM/HIGH), horizon, note` |
| POST | `/flow/recommend` | `{patient_id}` | `status, recommended_station, recommended_sequence, candidates[], rule_checks[], explanation, decision_source, model_versions` |

Live mode builds the features from the current hospital state. The routing engine calls the same code for each eligible service.

## Stations

| Method | Path | Body |
|---|---|---|
| GET | `/stations/status` | |
| POST | `/stations/{id}/status` | any of `available, servers_on_duty, background_queue, recent_wait_hint, arrivals_hint, doctor_delay_min, occupancy_sensor` |

## Robots and navigation

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/navigation/tasks` | `robot_id, patient_id, step` | Repeating the same request returns the same task |
| GET | `/navigation/tasks/{task_id}` | | |
| POST | `/robots/{id}/assign` | `patient_id, [force]` | Handover: the robot takes over the existing journey |
| GET | `/robots/{id}/session` | | What the robot screen shows |
| POST | `/robots/{id}/command` | `action: stop / resume / cancel / obstacle / clear_obstacle / report_arrival` | Emergency stop never resumes by itself |
| GET | `/robot/{id}/next-command` | | **ESP32 polls this** (see ESP32_CONTRACT.md) |
| POST | `/robot/status` | `robot_id, task_id, event, marker` | **ESP32 reports this** |

## Classroom track and rooms

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/navigation/map` | | Track, corridors (known-blocked / simulated obstacle), rooms, robot position and route |
| POST | `/navigation/map/reload` | | Reload `config/classroom_map.json` (no robot may be moving) |
| POST | `/navigation/obstacles` | `from_node, to_node, present` | **Simulation only**: place or remove a physical obstacle on a corridor |
| POST | `/navigation/corridors/clear` | `from_node, to_node` | Staff confirm a corridor is clear again |
| POST | `/rooms/{room_id}/status` | any of `occupied, staffed, person_present (simulation), source` | IoT room sensor, staff, or simulated person |

## Dashboard, simulation, demo

| Method | Path | Notes |
|---|---|---|
| GET | `/dashboard/state` | Everything the dashboard shows |
| GET | `/events/stream` | Server-sent events; any message means "refetch" |
| POST | `/simulation/replay` | `{days}`: background quick replay (booked order vs Synapse) |
| GET | `/simulation/results` | Held-out Oct–Dec report + latest quick replay |
| POST | `/demo/reset`, `/demo/scene/{1-9}`, `/demo/clock`, `/demo/auto-dispatch` | Demo controls |
