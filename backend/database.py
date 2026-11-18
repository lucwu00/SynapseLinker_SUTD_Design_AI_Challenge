"""SQLite store shared by the dashboard, EDi-01 (physical) and EDi-02 (virtual).

One database, one journey per patient: both robots read and write the same rows, which is what
makes the robot handover preserve context.
"""
from __future__ import annotations
import json
import sqlite3
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "runtime" / "synapse_v2.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
  patient_id TEXT PRIMARY KEY,
  queue_no TEXT,                         -- queue number shown to the patient (no names on robot screens)
  display_name TEXT,
  visit_type TEXT NOT NULL,
  referral_category TEXT NOT NULL,
  clinician_priority TEXT NOT NULL,
  mobility TEXT NOT NULL,
  age INTEGER NOT NULL,
  doctor_id TEXT,
  appointment_min REAL,
  checked_in_min REAL NOT NULL,
  required_steps TEXT NOT NULL,          -- JSON list, ordered as booked
  completed_steps TEXT NOT NULL,         -- JSON list, in completion order
  status TEXT NOT NULL,                  -- AWAITING_GUIDANCE | GUIDED | QUEUED | IN_SERVICE | COMPLETED | ESCALATED
  current_station TEXT,
  assigned_destination TEXT,             -- confirmed destination (locked)
  assigned_room TEXT,                    -- specific room on the classroom track, if any
  robot_id TEXT,
  escalated INTEGER NOT NULL DEFAULT 0,
  escalation_reason TEXT,
  recommendation TEXT,                   -- JSON: latest routing output (ML + rules)
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stations (
  station_id TEXT PRIMARY KEY,
  available INTEGER NOT NULL,            -- room/staff ready (staff toggle or sensor) - NOT clinical readiness
  servers_on_duty INTEGER NOT NULL,
  background_queue INTEGER NOT NULL,     -- people from other departments waiting (shared stations)
  recent_wait_hint REAL,                 -- recent observed wait reported by the station system
  arrivals_hint INTEGER NOT NULL,        -- recent arrivals reported by the station system
  doctor_delay_min REAL,                 -- CONSULT only: how late the doctor is running
  occupancy_sensor INTEGER,              -- IoT occupancy (informational only)
  prediction TEXT,                       -- JSON: latest Model A / Model B outputs
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS rooms (
  room_id TEXT PRIMARY KEY,
  station_id TEXT NOT NULL,
  label TEXT NOT NULL,
  classroom_label TEXT,
  node TEXT NOT NULL,
  staffed INTEGER NOT NULL,              -- open and staffed (staff toggle)
  occupied INTEGER NOT NULL,             -- what the system KNOWS (robot scan, IoT sensor, staff, service start)
  occupancy_source TEXT,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS queue_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  patient_id TEXT NOT NULL,
  station_id TEXT NOT NULL,
  step TEXT NOT NULL,
  room_id TEXT,
  doctor_id TEXT,
  joined_min REAL NOT NULL,
  started_min REAL,
  ended_min REAL,
  predicted_wait_min REAL
);
CREATE TABLE IF NOT EXISTS robots (
  robot_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  adapter TEXT NOT NULL,
  patient_id TEXT,
  status TEXT,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS nav_tasks (
  task_id TEXT PRIMARY KEY,
  robot_id TEXT NOT NULL,
  patient_id TEXT NOT NULL,
  destination_station TEXT NOT NULL,
  room_id TEXT,
  state TEXT NOT NULL,
  command TEXT NOT NULL,
  reason TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  clinic_time TEXT,
  severity TEXT NOT NULL,                -- critical | warning | info
  type TEXT NOT NULL,
  title TEXT NOT NULL,
  message TEXT,
  patient_id TEXT,
  station_id TEXT,
  robot_id TEXT,
  acknowledged_by TEXT,
  acknowledged_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  clinic_time TEXT,
  type TEXT NOT NULL,
  source TEXT NOT NULL,                  -- rules | ml | staff | robot | system | demo
  patient_id TEXT,
  station_id TEXT,
  robot_id TEXT,
  message TEXT,
  payload TEXT
);
"""

JSON_COLS = {"required_steps", "completed_steps", "recommendation", "prediction", "status_json", "command", "payload"}


class Database:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else DEFAULT_DB
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def reset(self):
        with self.lock:
            for t in ("patients", "stations", "rooms", "queue_entries", "robots", "nav_tasks", "events", "alerts"):
                self.conn.execute(f"DELETE FROM {t}")
            self.conn.commit()

    @staticmethod
    def _decode(row) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        for k in JSON_COLS & d.keys():
            if d[k] is not None:
                d[k] = json.loads(d[k])
        return d

    def query(self, sql, args=()) -> list[dict]:
        with self.lock:
            return [self._decode(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()) -> dict | None:
        with self.lock:
            return self._decode(self.conn.execute(sql, args).fetchone())

    def execute(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def upsert(self, table: str, key: str, row: dict):
        row = {k: (json.dumps(v) if k in JSON_COLS and v is not None else v) for k, v in row.items()}
        cols = ", ".join(row)
        ph = ", ".join("?" for _ in row)
        upd = ", ".join(f"{c}=excluded.{c}" for c in row if c != key)
        with self.lock:
            self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({ph}) ON CONFLICT({key}) DO UPDATE SET {upd}",
                              tuple(row.values()))
            self.conn.commit()

    def update(self, table: str, key: str, key_val, **fields):
        fields = {k: (json.dumps(v) if k in JSON_COLS and v is not None else v) for k, v in fields.items()}
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.lock:
            self.conn.execute(f"UPDATE {table} SET {sets} WHERE {key}=?", (*fields.values(), key_val))
            self.conn.commit()

    def log_event(self, type_, source, message="", patient_id=None, station_id=None, robot_id=None, payload=None,
                  clinic_time=None) -> dict:
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO events (ts, clinic_time, type, source, patient_id, station_id, robot_id, message, payload) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (time.time(), clinic_time, type_, source, patient_id, station_id, robot_id, message,
                 json.dumps(payload) if payload is not None else None))
            self.conn.commit()
            return dict(id=cur.lastrowid, type=type_, source=source, message=message, patient_id=patient_id,
                        station_id=station_id, robot_id=robot_id, clinic_time=clinic_time)
