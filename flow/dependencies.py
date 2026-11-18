"""Workflow rules: turns a visit's required steps into ordered stages and answers
"which services is this patient allowed to do next?".

A journey is a list of stages. Steps inside one stage may be done in any order
(only where config/workflow_rules.json allows it); stages are strictly sequential.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "workflow_rules.json"


@dataclass
class WorkflowRules:
    stations: list[str]
    aliases: dict[str, str]
    flexible_visit_types: set[str]
    flexible_phase_steps: set[str]
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def load(cls, path: Path | str = RULES_PATH) -> "WorkflowRules":
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        phase = next(p for p in cfg["phase_order"] if p["phase"] == "pre_consult_tests")
        vts = {vt for g in cfg["flexible_groups"] for vt in g["visit_types"]}
        return cls(cfg["stations"], cfg["step_aliases"], vts, set(phase["steps"]), cfg)

    def station_of(self, step: str) -> str:
        return self.aliases.get(step, step)

    def build_stages(self, steps: list[str], visit_type: str) -> list[list[str]]:
        """Group required steps into ordered stages.

        Steps between REGISTRATION and the first CONSULT form one order-flexible stage
        when the visit type is allowed; every other step is its own stage.
        """
        self.validate(steps)
        stages: list[list[str]] = []
        consult_i = steps.index("CONSULT")
        pre = steps[1:consult_i]
        stages.append([steps[0]])
        if pre and visit_type in self.flexible_visit_types and all(s in self.flexible_phase_steps for s in pre):
            stages.append(list(pre))
        else:
            stages.extend([[s] for s in pre])
        stages.extend([[s] for s in steps[consult_i:]])
        return stages

    def validate(self, steps: list[str]):
        if not steps or steps[0] != "REGISTRATION":
            raise ValueError("A journey must start with REGISTRATION (R01)")
        if "CONSULT" not in steps:
            raise ValueError("A journey must include CONSULT (R06)")
        for s in steps:
            if self.station_of(s) not in self.stations:
                raise ValueError(f"Unknown step {s}")
        if "REVIEW" in steps and "XRAY" not in steps[steps.index("CONSULT"):steps.index("REVIEW")]:
            raise ValueError("REVIEW requires a post-consult XRAY (R07)")
        if "PAYMENT" in steps:
            clinical_after = [s for s in steps[steps.index("PAYMENT"):] if s not in ("PAYMENT", "PHARMACY")]
            if clinical_after:
                raise ValueError("PAYMENT must follow all clinical steps (R11)")
        if "PHARMACY" in steps and ("PAYMENT" not in steps or steps.index("PHARMACY") < steps.index("PAYMENT")):
            raise ValueError("PHARMACY must follow PAYMENT (R12)")


def eligible_next(stages: list[list[str]], completed: list[str]) -> list[str]:
    """Steps the patient may do now: the unfinished steps of the first unfinished stage.

    `completed` is the ordered list of finished steps (a step may appear twice, e.g. XRAY).
    """
    remaining = list(completed)
    for stage in stages:
        left = []
        for s in stage:
            if s in remaining:
                remaining.remove(s)
            else:
                left.append(s)
        if left:
            return left
    return []


def remaining_stages(stages: list[list[str]], completed: list[str]) -> list[list[str]]:
    remaining, out = list(completed), []
    for stage in stages:
        left = []
        for s in stage:
            if s in remaining: remaining.remove(s)
            else: left.append(s)
        if left: out.append(left)
    return out
