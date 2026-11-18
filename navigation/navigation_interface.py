"""Navigation command contract. Every adapter (simulation now, ESP32 later) implements this.

Safety rules shared by all adapters:
  * reject navigation when route data is invalid or the destination is restricted;
  * stop when an obstacle is reported (BLOCKED) - no uncontrolled avoidance;
  * EMERGENCY_STOP never clears itself; only an explicit resume_navigation() does;
  * a repeated task_id is ignored (idempotent), so retries cannot create duplicate moves.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Callable

from navigation.navigation_models import NavTask, RobotStatus, Route


class NavigationError(Exception):
    pass


class NavigationAdapter(ABC):
    def __init__(self, robot_id: str, on_event: Callable[[str, dict], None] | None = None):
        self.robot_id = robot_id
        self.on_event = on_event or (lambda kind, data: None)
        self.tasks: dict[str, NavTask] = {}

    @abstractmethod
    def navigate(self, patient_id: str, destination_station: str, task_id: str, route: Route | None = None) -> NavTask: ...

    @abstractmethod
    def get_robot_status(self) -> RobotStatus: ...

    @abstractmethod
    def cancel_navigation(self, task_id: str | None = None) -> RobotStatus: ...

    @abstractmethod
    def stop_robot(self) -> RobotStatus: ...          # manual emergency stop

    @abstractmethod
    def resume_navigation(self) -> RobotStatus: ...   # explicit operator action only

    @abstractmethod
    def report_arrival(self, task_id: str) -> RobotStatus: ...

    def tick(self, dt_s: float):
        """Advance time (simulation) or check timeouts (hardware). Called by the backend loop."""
