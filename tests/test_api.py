from fastapi.testclient import TestClient

from pmadvisor.api import create_app
from pmadvisor.scenario import build
from pmadvisor.service import Workspace
from pmadvisor.store import Store


def test_api_flow():
    ws = Workspace(Store())
    ws.load_scenario(build(21, ["manual_three_way_match"], vary=False))
    c = TestClient(create_app(ws))
    assert c.get("/api/status").json()["simulated"] is True
    assert c.get("/api/overview").json()["kpis"]["cases"] > 0
    assert any(f["id"] == "manual_three_way_match" for f in c.get("/api/findings").json())
    assert c.get("/api/dfg").json()["edges"]
    adv = c.post("/api/advise", json={}).json()
    assert adv["recommendation_ids"]
    rec_id = adv["recommendation_ids"][0]
    assert c.post(f"/api/recommendations/{rec_id}/decision", json={"accept": True}, headers={"X-User": "agent"}).status_code == 400
    r = c.post(f"/api/recommendations/{rec_id}/decision", json={"accept": True}, headers={"X-User": "Ana"})
    assert r.json()["status"] == "ACCEPTED"
    w = c.post("/api/whatif", json={"levers": ["auto_three_way_match"]}).json()
    assert w["simulation"]["delta"]["manual_hours_per_year"] < 0
    assert c.post("/api/whatif", json={"levers": ["nope"]}).status_code == 400
    assert c.get("/api/audit").json()["verify"]["ok"]
    assert c.get("/api/export/model.bpmn").text.startswith("<?xml")
