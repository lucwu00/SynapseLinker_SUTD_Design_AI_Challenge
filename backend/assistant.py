"""EDi's conversational answers through the Claude API.

The robot screen first runs its own fixed rules (voice.js): urgent symptoms call staff, medical questions are
sent to the doctor, and actions (help, guide, change language) are handled there. Only the remaining questions
come here. Claude answers from the patient's LIVE journey (built here from the service, not from the browser)
and the clinic information the screen sends. If the API is not configured or fails, the screen keeps its own
rule-based answer, so the demo never depends on the network.

Configure with an environment variable (never commit the key):
    Windows CMD:   set ANTHROPIC_API_KEY=sk-ant-...
    PowerShell:    $env:ANTHROPIC_API_KEY="sk-ant-..."
    macOS/Linux:   export ANTHROPIC_API_KEY=sk-ant-...
or put the key alone in config/anthropic_api_key.txt (git-ignored).
Optional: ANTHROPIC_MODEL (default claude-haiku-5-5).
"""
from __future__ import annotations
import json
import os
from pathlib import Path

import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
KEY_FILE = ROOT / "config" / "anthropic_api_key.txt"
API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-5-5"
LANG_NAME = {"en": "English", "zh": "Simplified Chinese", "ms": "Malay", "ta": "Tamil"}

SYSTEM = """You are EDi, a guide robot in the orthopaedic specialist outpatient clinic of a Singapore public hospital.
This is a concept prototype with fictional patients. Your words are shown on a screen and read aloud.

Rules:
- Reply in {lang}. One to three short, plain sentences. No lists, no markdown, no emojis.
- Use only the facts in PATIENT JOURNEY and CLINIC INFORMATION below. Never invent rooms, times, prices,
  doctors' names or policies. If the answer is not there, say the staff at the counter can help.
- Waiting times and finish times are estimates that can change; say so when you give one.
- Never give medical advice: no diagnoses, test results, medicines, doses, or whether something is serious.
  For those, tell the patient to ask their doctor or a nurse, and that they can press "I need help".
- If the patient sounds unwell or in distress, tell them to press "I need help" and stay where they are.
- Refer to the patient by queue number only, never by name. Be warm and brief.
- You do not decide the order of steps; the clinic's rules and staff do. You can explain what the journey shows.
"""


def _post(url: str, headers: dict, body: dict, timeout: float) -> tuple[int, str]:
    """Plain HTTPS POST (standard library only, so nothing extra to install)."""
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def api_key() -> str | None:
    k = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not k and KEY_FILE.exists():
        k = KEY_FILE.read_text(encoding="utf-8").strip()
    return k or None


def status() -> dict:
    return {"enabled": api_key() is not None, "model": os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)}


def journey_facts(svc, robot_id: str) -> dict:
    """The facts Claude may use, taken from the live service (not from the browser)."""
    s = svc.robot_session(robot_id)
    robot = s.get("robot") or {}
    out = {"robot": robot_id, "robot_type": "virtual assistant on a screen (cannot walk with the patient)"
           if robot.get("kind") == "virtual" or robot.get("adapter") == "none"
           else "physical guide robot (can lead the patient there)", "clinic_time": s.get("clinic_time")}
    j = s.get("journey")
    if not j:
        out["patient"] = "No patient is linked to this robot yet. The patient can type their queue number to start."
        return out
    p = j["patient"]
    names = {
        "REGISTRATION": "Registration", "XRAY": "X-ray", "PHLEBOTOMY": "Blood test", "ECG": "ECG",
        "CONSULT": "Consultation with the doctor", "REVIEW": "Doctor review (results)", "CAST_ROOM": "Cast room",
        "DRESSING": "Dressing room", "PROCEDURE": "Procedure room", "PAYMENT": "Payment", "PHARMACY": "Pharmacy"}
    nm = lambda x: names.get(x, x)
    rec = p.get("recommendation") or {}
    est = j.get("estimate") or {}
    room = s.get("direction_room") or {}
    out["patient"] = {
        "queue_number": p.get("queue_no"),
        "status": {"AWAITING_GUIDANCE": "between steps, next step being decided", "GUIDED": "on the way to the next step",
                   "QUEUED": "waiting at the station", "IN_SERVICE": "being seen now", "COMPLETED": "visit complete",
                   "ESCALATED": "a staff member is coming"}.get(p["status"], p["status"]),
        "steps_done": [nm(x) for x in p["completed_steps"]],
        "all_steps_in_order": [nm(x) for x in p["required_steps"]],
        "steps_allowed_now": [nm(x) for x in j.get("eligible_now", [])],
        "current_or_next_step": nm(p.get("assigned_destination") or rec.get("recommended_station") or ""),
        "room": room.get("label") and f"{room.get('label')} ({room.get('classroom_label')})",
        "patients_ahead": j.get("patients_ahead"),
        "predicted_wait_next_step_min": next((round(c["predicted_wait_min"]) for c in rec.get("candidates", [])
                                              if c.get("step") == (p.get("assigned_destination") or rec.get("recommended_station"))), None),
        "why_this_step": rec.get("explanation") if isinstance(rec.get("explanation"), str) else None,
        "estimate_rest_of_visit": est and {
            "minutes_left_if_queues_stay_as_now": est.get("remaining_min"),
            "could_be_up_to_min": est.get("upper_min"), "finish_around": est.get("finish_at"),
            "steps": [{"step": nm(x["step"]), "wait_min": x["wait_min"], "service_min": x["service_min"]} for x in est.get("steps", [])],
            "doctor_may_add_steps": bool(est.get("before_consult"))},
    }
    return out


def ask(svc, robot_id: str, question: str, lang: str, clinic_info: list[str], history: list[dict]) -> dict:
    key = api_key()
    if not key:
        return {"ok": False, "reason": "not_configured"}
    facts = journey_facts(svc, robot_id)
    system = SYSTEM.format(lang=LANG_NAME.get(lang, "English")) + \
        "\nPATIENT JOURNEY (live):\n" + json.dumps(facts, ensure_ascii=False, indent=1) + \
        "\n\nCLINIC INFORMATION (approved answers for common questions):\n" + "\n".join(f"- {x}" for x in clinic_info[:40])
    msgs = []
    for t in history[-6:]:
        role = "user" if t.get("who") == "you" else "assistant"
        if t.get("text") and (not msgs or msgs[-1]["role"] != role):
            msgs.append({"role": role, "content": str(t["text"])[:500]})
    if msgs and msgs[0]["role"] == "assistant":
        msgs = msgs[1:]
    if msgs and msgs[-1]["role"] == "user":
        msgs = msgs[:-1]
    msgs.append({"role": "user", "content": question[:500]})
    try:
        code, text = _post(API_URL, {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                           {"model": status()["model"], "max_tokens": 300, "system": system, "messages": msgs}, 12.0)
        if code != 200:
            return {"ok": False, "reason": f"api_{code}", "detail": text[:300]}
        reply = "".join(b.get("text", "") for b in json.loads(text).get("content", []) if b.get("type") == "text").strip()
        return {"ok": bool(reply), "reply": reply, "model": status()["model"]}
    except (OSError, ValueError) as e:                     # network down, timeout, bad JSON
        return {"ok": False, "reason": "network", "detail": str(e)[:200]}
