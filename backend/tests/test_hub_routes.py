from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import fastapi_app

client = TestClient(fastapi_app)


def test_projects_lists_volhelix_first():
    data = client.get("/api/hub/projects").json()
    assert data["projects"][0]["id"] == "volhelix"


def test_overview_marks_unreachable_sibling_offline():
    reg = [{"id": "x", "name": "X", "health_url": "http://127.0.0.1:1/none"}]
    with patch("backend.api.hub_routes.load_registry", return_value=reg):
        data = client.get("/api/hub/overview").json()
    by_id = {p["id"]: p for p in data["projects"]}
    assert by_id["volhelix"]["status"] == "online"
    assert by_id["x"]["status"] == "offline"
    assert data["summary"] == {"total": 2, "online": 1}


def test_overview_includes_risk_limits():
    data = client.get("/api/hub/overview").json()
    risk = data["projects"][0]["risk"]
    assert risk["max_daily_drawdown_pct"] > 0
    assert risk["daily_drawdown_pct"] >= 0
