# Synapse Linker: Orthopaedic Physical AI patient-flow prototype

SUTD Design·AI Challenge. All patient data are **fictional/synthetic** and the models are **not clinically validated**. EDi-01 runs on the OjanBot ESP32 firmware in `firmware/` (simulated and compile-checked; confirm the pins on your robot) or, without the robot, as an on-screen simulation.

## What it does

1. **Model A** predicts how long a patient will wait at each station (HistGradientBoostingRegressor).
2. **Model B** forecasts whether a station will bottleneck in the next 30 minutes (HistGradientBoostingClassifier).
3. **Workflow rules** decide which services a patient is allowed to do next.
4. **The router** uses the ML predictions to pick the best order among the permitted services, and explains why.
5. **An autonomous loop** reruns all of this on every event and pushes updates live to the dashboard and robot screens.
6. **Two robots, one journey**: EDi-01 (the physical OjanBot) and EDi-02 (a virtual assistant) share a single SQLite journey store, so a patient can switch robots without losing context.

See `docs/ARCHITECTURE.md` for the system diagram.

## Singapore / CGH context

The product is designed around a Singapore public-hospital Orthopaedic Surgery specialist outpatient clinic (SOC), with Changi General Hospital as the reference context. It uses **no CGH logo or branding**, and it says on screen that it is a concept prototype, not affiliated with or endorsed by CGH. Add CGH branding only if CGH or the organisers give permission.

| Alignment | Source |
|---|---|
| Referral categories use CGH Orthopaedic Surgery's published subspecialty names (Hip & Knee, Integrated Spine, Shoulder & Elbow, Foot & Ankle, Trauma) | CGH Orthopaedic Surgery page |
| Patients are identified by **queue number**; "patients ahead of you" is shown on the robot screen; names are masked on shared screens | Health Buddy queue updates (queue number, patients ahead) |
| Robot screen in English, 华文, Bahasa Melayu and தமிழ், with read-aloud | Singapore's four official languages. The translations are drafts, so have native speakers check them |
| Visit flow: registration, tests, consultation, payment, medication collection | CGH "Your clinic visit" pages |
| Large high-legibility text, mobility-aware routing | An older orthopaedic patient group |

Not copied: real CGH clinic numbers, room names, policies or records.

## Table demo (60 × 60 cm double loop): rooms, obstacles, rerouting

See **`docs/TABLE_SETUP.md`** and `docs/table_layout_60cm.png`. In short:
- An outer tape rectangle 10 cm in from the table edges plus a middle corridor at y = 30 cm, giving two alternative loops joined by T-junctions at (10, 30) and (50, 30). Station bars: START (30, 10), Station 1 / X-ray Room 2 (50, 38), Station 2 / X-ray Room 1 (30, 50), Station 3 / Consultation Room 1 (10, 22), in cm from the bottom-left corner.
- Navigation is a graph planner that knows which way the robot faces: it plans shortest routes with turn costs, gives turn instructions at junctions, and replans when a corridor is reported blocked. The robot recognises station bars and T-junctions by sensor pattern, expected order and distance driven.
- An occupied room makes the system re-guide the patient (scene 8). An obstacle makes the robot stop, wait 5 s, turn round and take the alternative corridor (scene 9). If every way is blocked, staff are alerted.
- The track is defined in `config/table_map_60cm.json`; swap in an exact physical map later without touching the ML or routing. Firmware: `firmware/README.md`. Hosting: `docs/DEPLOY.md`.

## Windows setup (once)

Install **Python 3.11+**. Install **Node.js 18+** only if you want to edit the dashboard.

```
cd synapse-linker
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Train the models yourselves (about 3 minutes)

The trained models are included, but rerun this so the team trains them on its own machine. Results are identical (fixed seeds).

```
python -m ml.train_waiting       # Model A  -> ml/models/waiting_time_model.joblib
python -m ml.train_bottleneck    # Model B  -> ml/models/bottleneck_model.joblib
python -m ml.build_wait_profile  # router look-ahead -> ml/models/wait_profile.json
python -m ml.evaluate            # reports/model_report.md + charts
python -m flow.evaluate_replay   # before/after replay -> reports/replay_report.md
python -m pytest -q              # 60 tests (backend, rules, routing, navigation, table loop, API)
```

## Run the demo

```
start_backend.bat          # EDi-01 simulated on screen
start_backend_robot.bat    # demo day: EDi-01 is the real OjanBot (see docs/TABLE_SETUP.md section 4)
```

Then open:

| Screen | URL |
|---|---|
| Operations wall display (show to the audience) | http://localhost:8000 |
| Presenter console (demo scenes, simulation controls) | http://localhost:8000/#/presenter |
| EDi-01 robot phone | http://LAPTOP-IP:8000/#/robot/EDi-01 |
| EDi-02 virtual assistant | http://LAPTOP-IP:8000/#/robot/EDi-02 |
| API docs (Swagger) | http://localhost:8000/docs |

- **Phone access:** find LAPTOP-IP with `ipconfig`. The phone must be on the same Wi-Fi. Allow Python through Windows Firewall when asked.
- **Prebuilt dashboard:** `frontend/dist` is already built, so no Node.js is needed to run it.
- **Editing the dashboard:** run `start_dashboard_dev.bat` and open http://localhost:5173. When finished, run `cd frontend && npm run build` to update `frontend/dist`.

### Demo script (presenter console buttons 1-9)

Scenes 8 and 9 start from a clean clinic and run in real time. Watch the "Demo table track" panel. With the real robot, put it back on START facing right before each of them.

| Scene | What happens | What is ML vs rules vs simulated |
|---|---|---|
| 1 Patient checks in | ORTHO_001 checks in with EDi-01, and the robot is dispatched to consult automatically | ML wait; rules give eligibility; robot simulated |
| 2 Pre-op test order | ORTHO_002 may do X-ray, blood test and ECG in any order; the router compares all 6 orders | ML waits and risks; rule R05 |
| 3 Test-room congestion | A test room (ECG first choice) gets a burst of requests, Model B forecasts HIGH risk, and the recommended order changes by itself. Works at any real time: the scene picks the test and congestion level for which the trained models change the order | Load simulated; forecast and update are ML |
| 4 Blood-test room closes | The closed room is excluded and the patient is re-routed | Rules; room status simulated |
| 5 Robot drives | The navigation command is shown; the simulated robot arrives; the patient is QUEUED, not completed | Simulation adapter |
| 6 Robot handover | The clinician orders an X-ray; EDi-01 takes another patient; EDi-02 continues ORTHO_001's journey | Shared database |
| 7 Before/after result | Held-out replay results | Discrete-event simulation |
| 8 Room occupied, re-guide | Someone is in the nearest X-ray room (Station 1). The robot arrives, scans it as occupied, checks room availability and re-guides to X-ray Room 1 (Station 2) | Robot ultrasonic scan + rules |
| 9 Corridor blocked, reroute | A trolley blocks the lower-right corridor. The robot sees it, waits 5 s, turns round and takes the middle corridor (right turn, then left turn at the T-junctions) to Station 1. Block the middle and top corridors too to show "completely blocked → staff alerted" | Robot obstacle stop + direction-aware planner + rules |

Screens:
- **Wall display**: a dark command-centre layout with KPIs, a station table (predicted wait, wait trend, 30-minute risk), acknowledgeable alerts, a patient tracking board with an expandable routing decision and staff actions, the robots, a compact floor map and the event log.
- **Presenter console**: run it on your own laptop. It holds every demo control, so the wall display stays clean.

Other controls (presenter console):
- **Robot cards:** Obstacle, Clear, Emergency stop and Resume.
- **Station cards:** Close room and ±5 waiting.
- **Clock:** "+15 min" and "Run ×10". The models depend on time of day.
- **Reset demo:** resets everything in one click.

## Measured results (synthetic data)

| | Result |
|---|---|
| Model A, waiting time | Test MAE 4.3 min, against 13.0 min for a station × hour median baseline; R² 0.89 |
| Model B, bottleneck (30 min) | PR-AUC 0.945; precision 0.89; recall 0.84. Weak at CONSULT (recall 0.26, advisory only) |
| Replay: order-flexible visits | **4.6 min faster (3.0%)**, 95% CI 3.3–5.9 |
| Replay: pre-op visits | **6.0 min faster (3.9%)** |
| Replay: all visits | 0.09%, not distinguishable from zero |

Full details are in `reports/model_report.md` and `reports/replay_report.md`.

## Project layout

```
data/original/      the datasets (synthetic)        reports/   audit, model report, replay report, charts
ml/                 training + evaluation           flow/      rules, predictor, router, replay simulator
backend/            FastAPI app, service, SQLite    navigation/ interface, simulation + ESP32 adapters, planner
frontend/           React + Vite dashboard          config/    rules, assumptions, backend settings, example map
docs/               architecture, API, ESP32 contract, table setup, deploy   tests/  pytest suite
firmware/           OjanBot ESP32 sketches (demo + hardware test)          tools/  dataset simulator
render.yaml         Render blueprint for the hosted backend
```

## Hardware steps

1. Lay the tape on the 60 × 60 cm table (`docs/TABLE_SETUP.md`).
2. Confirm the pins and calibrate with `firmware/ojanbot_hw_test`, with a dummy weight equal to the phone.
3. Flash `firmware/ojanbot_synapse`, turn on the laptop hotspot and run `start_backend_robot.bat`.

## Limitations

- **Synthetic data:** all training and evaluation data come from a simulated clinic. Scores will be lower on real hospital data.
- **Hypothetical assumptions:** capacities, other-department demand, breaks, outages and travel times are made up.
- **Small clinic-wide effect:** reordering helps only the roughly 8% of visits that have a choice. Clinic-wide time does not change, because rerouting mostly moves waiting between patients.
- **Background load:** the dashboard's "Waiting" counts include simulated patients from other departments, so the models see realistic inputs during a small demo.
- **Robot:** the firmware has been checked in simulation only; physical navigation is claimed only after it runs on the table. Navigation is line following with mark counting, not free-space localisation.
- **Room occupancy:** "occupied" means someone is in the room, not whether the room is clinically ready. Staff still open and close rooms.

## Attribution

- Datasets are generated by `tools/generate_ortho_data.py`. They were designed after auditing the Mendeley HospitalOPD dataset (DOI 10.17632/2dfm39t973.1, CC BY 4.0), which was not used for training.
- The no-show assumption follows Chua & Chow, *Proceedings of Singapore Healthcare* (2019).
