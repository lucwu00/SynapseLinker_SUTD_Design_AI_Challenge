"""Synapse Linker FastAPI app.

Run (Windows):  uvicorn backend.main:app --host 0.0.0.0 --port 8000
Interactive API docs: http://localhost:8000/docs
"""
from __future__ import annotations
import json
import mimetypes
import os

# Windows sometimes maps .js to "text/plain" in the registry, which makes browsers refuse to run
# the dashboard (blank page). Force the correct types before serving static files.
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
import queue
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend import schemas as S
from backend.service import SynapseService, ServiceError
from backend import demo
from flow.predictor import PredictionInputError

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title="Synapse Linker API", version="1.0.0",
              description="Orthopaedic patient-flow coordination (fictional data, not clinically validated).")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

svc = SynapseService(db_path=os.environ.get("SYNAPSE_DB"), start_loop=True)
replay_state = {"status": "idle", "result": None, "error": None}


@app.middleware("http")
async def revalidate_dashboard_files(request: Request, call_next):
    """Make browsers re-check the dashboard files on every load, so an updated build is never stale."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/assets/") or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(ServiceError)
def _service_error(_: Request, e: ServiceError):
    return JSONResponse(status_code=e.status, content={"error": str(e)})


@app.exception_handler(PredictionInputError)
def _pred_error(_: Request, e: PredictionInputError):
    return JSONResponse(status_code=422, content={"error": str(e)})


def body(m) -> dict:
    return m.model_dump(exclude_none=True)


# ------------------------------------------------------------------ health
@app.get("/health")
def health():
    return {"ok": True, "models": svc.predictor.versions, "clock": svc.clock.to_dict()}


# ------------------------------------------------------------------ patients / journey
@app.post("/patients/check-in")
def check_in(req: S.CheckInRequest):
    return svc.check_in(body(req))


@app.get("/patients/{patient_id}/journey")
def journey(patient_id: str):
    return svc.journey(patient_id)


@app.post("/patients/{patient_id}/destination")
def confirm_destination(patient_id: str, req: S.DestinationRequest):
    return svc.confirm_destination(patient_id, req.step, req.robot_id, source="patient")


@app.post("/patients/{patient_id}/arrived")
def arrived(patient_id: str):
    return svc.arrive_at_station(patient_id, source="staff")


@app.post("/patients/{patient_id}/service-started")
def service_started(patient_id: str):
    return svc.start_service(patient_id)


@app.post("/patients/{patient_id}/service-completed")
def service_completed(patient_id: str, req: S.ServiceCompletedRequest):
    return svc.complete_service(patient_id, req.step, req.added_steps, req.recorded_by)


@app.post("/patients/{patient_id}/orders")
def change_orders(patient_id: str, req: S.OrdersRequest):
    return svc.change_orders(patient_id, req.add, req.remove, req.recorded_by)


@app.post("/patients/{patient_id}/escalate")
def escalate(patient_id: str, req: S.EscalateRequest):
    return svc.escalate(patient_id, req.reason, req.active)


# ------------------------------------------------------------------ predictions & flow
@app.post("/predict/waiting-time")
def predict_waiting_time(req: S.WaitPredictionRequest):
    feats = req.features.model_dump() if req.features else None
    return svc.predict_waiting_time(req.patient_id, req.station, feats)


@app.post("/predict/bottleneck")
def predict_bottleneck(req: S.BottleneckRequest):
    feats = req.features.model_dump() if req.features else None
    return svc.predict_bottleneck_risk(req.station, feats)


@app.post("/flow/recommend")
def flow_recommend(req: S.RecommendRequest):
    return svc.recommend(req.patient_id)


# ------------------------------------------------------------------ stations
@app.get("/stations/status")
def stations_status():
    return svc.dashboard_state()["stations"]


@app.post("/stations/{station_id}/status")
def station_update(station_id: str, req: S.StationStatusUpdate):
    return svc.update_station(station_id, **body(req))


# ------------------------------------------------------------------ navigation & robots
@app.post("/navigation/tasks")
def nav_task(req: S.NavTaskRequest):
    p = svc.journey(req.patient_id)["patient"]
    if p["status"] == "AWAITING_GUIDANCE":
        return svc.confirm_destination(req.patient_id, req.step, req.robot_id, source="staff")
    return {"navigation_task": svc.create_nav_task(req.robot_id, req.patient_id, req.step)}


@app.get("/navigation/tasks/{task_id}")
def nav_task_get(task_id: str):
    t = svc.db.one("SELECT * FROM nav_tasks WHERE task_id=?", (task_id,))
    if not t:
        raise HTTPException(404, f"Unknown task {task_id}")
    return t


@app.get("/robot/{robot_id}/next-command")          # polled by the ESP32
def robot_next_command(robot_id: str):
    return svc.esp32_poll(robot_id)


@app.post("/robot/status")                            # reported by the ESP32
def robot_status(req: S.RobotStatusReport):
    return svc.esp32_status(body(req))


@app.post("/robots/{robot_id}/assign")
def robot_assign(robot_id: str, req: S.AssignRobotRequest):
    return svc.assign_robot(robot_id, req.patient_id, req.force)


@app.get("/robots/{robot_id}/session")
def robot_session(robot_id: str):
    return svc.robot_session(robot_id)


@app.post("/robots/{robot_id}/command")
def robot_command(robot_id: str, req: S.RobotCommandRequest):
    return svc.robot_command(robot_id, req.action, req.task_id)


# ------------------------------------------------------------------ classroom floor (track, rooms, obstacles)
@app.get("/navigation/map")
def floor_map():
    return svc.floor_map()


@app.post("/navigation/map/reload")
def reload_map():
    return svc.reload_map()


@app.post("/navigation/obstacles")
def obstacle(req: S.ObstacleRequest):
    return svc.set_obstacle(req.from_node, req.to_node, req.present, exclusive=req.exclusive)


@app.post("/navigation/corridors/clear")
def corridor_clear(req: S.CorridorRequest):
    return svc.clear_corridor(req.from_node, req.to_node)


@app.post("/rooms/{room_id}/status")
def room_status(room_id: str, req: S.RoomStatusUpdate):
    return svc.update_room(room_id, req.occupied, req.staffed, req.person_present, req.source)


@app.post("/alerts/{alert_id}/ack")
def ack_alert(alert_id: int, req: S.AckRequest):
    return svc.ack_alert(alert_id, req.by)


# ------------------------------------------------------------------ dashboard & live updates
@app.get("/dashboard/state")
def dashboard_state():
    return svc.dashboard_state()


@app.get("/events/stream")
def events_stream():
    def gen():
        q = svc.bus.subscribe()
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    msg = q.get(timeout=15)
                    yield f"data: {json.dumps(msg)}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            svc.bus.unsubscribe(q)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------------ simulation (before/after)
@app.post("/simulation/replay")
def simulation_replay(req: S.ReplayRequest):
    if os.environ.get("SYNAPSE_DISABLE_LIVE_REPLAY") == "1":
        raise HTTPException(503, "Live replay is turned off on the hosted demo (memory limit). "
                                 "The full held-out results are shown above; run the replay on a laptop.")
    if replay_state["status"] == "running":
        raise HTTPException(409, "A replay is already running")

    def run(days):
        from flow.replay_simulator import run as rp, summarise
        try:
            res, _, _ = rp(("fixed", "synapse"), days=days, verbose=False)
            replay_state.update(status="done", result=summarise(res).round(2).to_dict("records"), error=None)
        except Exception as e:                       # report, never crash the API
            replay_state.update(status="error", error=str(e))
    replay_state.update(status="running", result=None, started=time.time(), days=req.days)
    threading.Thread(target=run, args=(req.days,), daemon=True).start()
    return {"status": "running", "days": req.days}


@app.get("/simulation/results")
def simulation_results():
    p = ROOT / "reports" / "replay_results.json"
    full = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    from flow.evaluate_replay import clean_json
    return clean_json({"held_out_report": full, "latest_quick_replay": replay_state})


# ------------------------------------------------------------------ demo controls
@app.post("/demo/reset")
def demo_reset():
    svc.reset()
    return {"ok": True}


@app.post("/demo/scene/{n}")
def demo_scene(n: int):
    if n not in demo.SCENE_NUMBERS:
        raise HTTPException(404, f"Scenes are {min(demo.SCENE_NUMBERS)}-{max(demo.SCENE_NUMBERS)}")
    return demo.scene(svc, n)


@app.post("/demo/clock")
def demo_clock(req: S.ClockRequest):
    return svc.set_clock(req.clinic_time, req.speed, req.advance_min, req.day_of_week)


@app.post("/demo/auto-dispatch")
def demo_auto_dispatch(req: S.ToggleRequest):
    svc.auto_dispatch = req.enabled
    return {"auto_dispatch": svc.auto_dispatch}


# ------------------------------------------------------------------ built dashboard (after `npm run build`)
DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="frontend")
