"""HTTP-level tests with FastAPI's TestClient. Needs `pip install -r requirements.txt` (fastapi, httpx).
Skipped automatically where FastAPI is not installed."""
import os
import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
os.environ.setdefault("SYNAPSE_DB", ":memory:")
from fastapi.testclient import TestClient  # noqa: E402
from backend.main import app  # noqa: E402

client = TestClient(app)


def test_health_and_dashboard():
    assert client.get("/health").json()["ok"]
    client.post("/demo/reset")
    s = client.get("/dashboard/state").json()
    assert len(s["stations"]) == 10


def test_check_in_validation_and_flow():
    client.post("/demo/reset")
    assert client.post("/patients/check-in", json={"patient_id": "bad id!", "visit_type": "x", "age": 999}).status_code == 422
    r = client.post("/patients/check-in", json={"patient_id": "A1", "visit_type": "pre_op", "referral_category": "Knee/Hip",
                                               "age": 66, "pre_consult_tests": ["XRAY", "PHLEBOTOMY", "ECG"]})
    assert r.status_code == 200
    assert client.post("/patients/A1/destination", json={"step": "CONSULT"}).status_code == 422
    w = client.post("/predict/waiting-time", json={"patient_id": "A1", "station": "XRAY"}).json()
    assert w["predicted_wait_min"] >= 0 and w["model_version"]
    b = client.post("/predict/bottleneck", json={"station": "PHARMACY"}).json()
    assert 0 <= b["bottleneck_probability_next_30min"] <= 1
    assert client.post("/flow/recommend", json={"patient_id": "A1"}).json()["status"] == "ROUTE"


def test_scenes_and_results():
    client.post("/demo/reset")
    for n in range(1, 8):
        assert client.post(f"/demo/scene/{n}").status_code == 200
    assert client.get("/simulation/results").status_code == 200
    assert client.get("/robots/EDi-02/session").json()["patient_id"] == "ORTHO_001"
