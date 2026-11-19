"""Request schemas (validated by FastAPI/pydantic). Responses are plain JSON documented in docs/API.md."""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field

VisitType = Literal["new", "follow_up", "pre_op"]
Referral = Literal["Knee/Hip", "Spine/Back", "Shoulder/Elbow", "Hand/Wrist", "Foot/Ankle", "Fracture", "Post-op"]
Priority = Literal["routine", "expedited"]
Mobility = Literal["independent", "walking_aid", "wheelchair"]
Station = Literal["REGISTRATION", "XRAY", "PHLEBOTOMY", "ECG", "CONSULT", "CAST_ROOM", "DRESSING", "PROCEDURE",
                  "PAYMENT", "PHARMACY"]
Step = Literal["XRAY", "PHLEBOTOMY", "ECG", "CONSULT", "REVIEW", "CAST_ROOM", "DRESSING", "PROCEDURE", "PAYMENT",
               "PHARMACY"]
PreTest = Literal["XRAY", "PHLEBOTOMY", "ECG"]
PostStep = Literal["XRAY", "REVIEW", "CAST_ROOM", "DRESSING", "PROCEDURE", "PHARMACY"]
HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class CheckInRequest(BaseModel):
    # letters, numbers, _ and -, with single spaces between words; blank = the next ORTHO_nnn
    patient_id: Optional[str] = Field(None, max_length=32, pattern=r"^[A-Za-z0-9_-]+( [A-Za-z0-9_-]+)*$", examples=["ORTHO_001", "Mr Z"])
    display_name: Optional[str] = Field(None, max_length=40)
    visit_type: VisitType
    referral_category: Referral
    clinician_priority: Priority = "routine"          # set by clinician/triage rules, never by AI
    mobility: Mobility = "independent"
    age: int = Field(ge=0, le=120)
    doctor_id: Optional[str] = None
    appointment_time: Optional[str] = Field(None, pattern=HHMM)
    pre_consult_tests: list[PreTest] = []
    post_consult_steps: list[PostStep] = []
    pharmacy: bool = False
    robot_id: Optional[str] = None


class DestinationRequest(BaseModel):
    step: Step
    robot_id: Optional[str] = None


class ServiceCompletedRequest(BaseModel):
    step: Optional[Step] = None
    added_steps: list[PostStep] = []                  # clinician orders recorded at CONSULT/REVIEW
    recorded_by: str = Field("staff", max_length=40)


class OrdersRequest(BaseModel):
    add: list[Step] = []
    remove: list[Step] = []
    order: Optional[list[Step]] = None      # the steps still to do, in the order staff want (drag and drop)
    unlock: bool = False                    # let the system choose the order again
    recorded_by: str = Field("Clinician", max_length=40)


class PriorityRequest(BaseModel):
    """Set by a nurse or clinician, never by the AI."""
    priority: str = Field(pattern="^(routine|expedited)$")
    by: str = Field("Nurse", max_length=40)


class QueueOrderRequest(BaseModel):
    """Who goes first at a station. Empty list: follow the suggested order again."""
    order: list[str] = []
    by: str = Field("Nurse", max_length=40)


class EscalateRequest(BaseModel):
    reason: str = Field(max_length=200)
    active: bool = True


class WaitFeatures(BaseModel):
    station: Station
    visit_type: VisitType
    referral_category: Referral
    clinician_priority: Priority
    mobility: Mobility
    join_hour: int = Field(ge=0, le=23)
    day_of_week: int = Field(ge=0, le=6)
    queue_length_at_join: int = Field(ge=0)
    servers_on_duty: int = Field(ge=0)
    servers_busy: int = Field(ge=0)
    age: Optional[int] = Field(None, ge=0, le=120)
    step_index: Optional[int] = None
    is_review: Optional[int] = None
    minutes_since_clinic_arrival: Optional[float] = None
    station_open: Optional[int] = None
    recent_mean_wait_30min: Optional[float] = None
    arrivals_last_15min: Optional[int] = None
    consult_completions_last_15min: Optional[int] = None
    total_consult_queue: Optional[int] = None
    patients_ahead_for_doctor: Optional[int] = None
    doctor_present: Optional[int] = None
    doctor_running_delay_min: Optional[float] = None
    minutes_to_appointment: Optional[float] = None


class WaitPredictionRequest(BaseModel):
    """Either live mode (patient_id + station: features built from current hospital state)
    or explicit mode (features)."""
    patient_id: Optional[str] = None
    station: Optional[Step] = None
    features: Optional[WaitFeatures] = None


class BottleneckFeatures(BaseModel):
    station: Station
    time_of_day_min: float = Field(ge=0, lt=1440)
    day_of_week: int = Field(ge=0, le=6)
    queue_length: int = Field(ge=0)
    servers_on_duty: int = Field(ge=0)
    servers_busy: int = Field(ge=0)
    recent_mean_wait_30min: Optional[float] = None
    arrivals_last_15min: Optional[int] = None
    consult_completions_last_15min: Optional[int] = None
    booked_appointments_next_30min: Optional[float] = None


class BottleneckRequest(BaseModel):
    station: Optional[Station] = None
    features: Optional[BottleneckFeatures] = None


class RecommendRequest(BaseModel):
    patient_id: str


class StationStatusUpdate(BaseModel):
    available: Optional[bool] = None
    servers_on_duty: Optional[int] = Field(None, ge=0, le=20)
    background_queue: Optional[int] = Field(None, ge=0, le=200)
    recent_wait_hint: Optional[float] = Field(None, ge=0, le=600)
    arrivals_hint: Optional[int] = Field(None, ge=0, le=200)
    doctor_delay_min: Optional[float] = Field(None, ge=0, le=240)
    occupancy_sensor: Optional[int] = Field(None, ge=0, le=50)


class NavTaskRequest(BaseModel):
    robot_id: str
    patient_id: str
    step: Step


class RobotStatusReport(BaseModel):
    """Sent by the ESP32 firmware."""
    robot_id: str
    task_id: Optional[str] = None
    event: Literal["ACK", "ARRIVED", "BLOCKED", "OBSTACLE_CLEARED", "PATH_BLOCKED", "ESTOP_PRESSED", "ERROR", "HEARTBEAT",
                   "RADIO_OFF"]
    marker: Optional[str] = None
    room_scan: Optional[Literal["OCCUPIED", "FREE"]] = None
    marks_passed: Optional[int] = Field(None, ge=0, le=64)   # PATH_BLOCKED: tape marks crossed on this route
    turned_around: Optional[bool] = None                     # PATH_BLOCKED: robot has turned round on the line
    on_mark: Optional[bool] = None                           # PATH_BLOCKED: it never left the mark it started on
    message: Optional[str] = Field(None, max_length=200)


class RobotCommandRequest(BaseModel):
    action: Literal["stop", "resume", "cancel", "obstacle", "clear_obstacle", "report_arrival"]
    task_id: Optional[str] = None


class AssignRobotRequest(BaseModel):
    patient_id: str
    force: bool = False


class ClockRequest(BaseModel):
    clinic_time: Optional[str] = Field(None, pattern=HHMM)
    speed: Optional[float] = Field(None, gt=0, le=120)
    advance_min: Optional[float] = Field(None, ge=0, le=600)
    day_of_week: Optional[int] = Field(None, ge=0, le=6)


class ReplayRequest(BaseModel):
    days: int = Field(5, ge=1, le=64)


class ToggleRequest(BaseModel):
    enabled: bool


class ObstacleRequest(BaseModel):
    """Simulation only: place/remove an obstacle on a tape segment."""
    from_node: str
    to_node: str
    present: bool = True
    exclusive: bool = False      # move the obstacle here: remove any others first


class CorridorRequest(BaseModel):
    from_node: str
    to_node: str


class RoomStatusUpdate(BaseModel):
    occupied: Optional[bool] = None          # IoT sensor / staff report (what the system knows)
    staffed: Optional[bool] = None
    person_present: Optional[bool] = None    # simulation only: someone is physically in the room, unreported
    source: str = Field("staff", max_length=40)


class AckRequest(BaseModel):
    by: str = Field("Staff", max_length=40)


class AssistantRequest(BaseModel):
    """A patient's question on a robot screen, for the Claude-backed assistant."""
    robot_id: str
    question: str = Field(..., max_length=500)
    lang: str = "en"
    clinic_info: list[str] = []
    history: list[dict] = []
