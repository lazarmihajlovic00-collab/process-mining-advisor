import json

import pytest

from pmadvisor import llm
from pmadvisor.advisor import grounding, runner
from pmadvisor.scenario import build
from pmadvisor.service import ServiceError, Workspace
from pmadvisor.store import Store


@pytest.fixture()
def ws():
    w = Workspace(Store())
    w.load_scenario(build(21, ["manual_invoice_entry", "batched_payment_run"], vary=False))
    return w


def test_grounding_parses_serbian_numbers():
    nums = dict(grounding.numbers_in_text("Ušteda 1.251.000 RSD (1,3 miliona), 15,4% i 24.0 h; 12.10.2026 u 10:00, #3"))
    assert nums["1.251.000"] == 1_251_000 and nums["1,3 miliona"] == 1_300_000 and nums["15,4"] == 15.4
    assert "2026" not in " ".join(nums)


def test_grounding_flags_invented_numbers():
    g = grounding.check("Ušteda 1.251.000 RSD, oko 1,3 miliona, a ručno 15% faktura i 987 sati.",
                        [{"net": 1251000, "share": 15.4, "x": "7097 h"}])
    assert g["ungrounded"] == ["987"] and g["grounded"] == 3


def test_template_advisor_creates_pending_recommendations(ws):
    res = runner.advise(ws, "ana")
    recs = ws.recommendations()
    assert res.source == "template"
    assert {r["lever"] for r in recs} == {"sef_erp_integration", "daily_payment_run"}
    assert all(r["status"] == "PENDING" for r in recs)
    assert "Sažetak" in res.text


def test_only_a_named_person_can_decide(ws):
    runner.advise(ws, "ana")
    rec = ws.recommendations()[0]
    for bad in ("", "agent", "agent (za ana)", "gemini"):
        with pytest.raises(ServiceError):
            ws.decide(rec["id"], True, bad)
    done = ws.decide(rec["id"], True, "Ana Jovanović", "počinjemo u januaru")
    assert done["status"] == "ACCEPTED" and done["decided_by"] == "Ana Jovanović"
    with pytest.raises(ServiceError):
        ws.decide(rec["id"], False, "Ana Jovanović")


def test_audit_chain_detects_tampering(ws):
    runner.advise(ws, "ana")
    assert ws.store.verify_audit()["ok"]
    ws.store.db.execute("UPDATE audit SET payload='{}' WHERE id=2")
    assert not ws.store.verify_audit()["ok"]


def test_whatif_needs_a_simulated_log(tmp_path, ws):
    p = tmp_path / "log.csv"
    ws.run().an.df[["case_id", "activity", "start", "complete", "resource"]].to_csv(p, index=False)
    w2 = Workspace(Store())
    w2.load_file(p)
    with pytest.raises(ServiceError):
        w2.whatif(["daily_payment_run"])


class _Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code, self.text = body, status, json.dumps(body)

    def json(self):
        return self.body


class FakeGemini:
    """Scripted Gemini: overview -> estimate -> propose -> final text with one invented number."""

    def __init__(self):
        self.calls = 0
        self.requests = []

    def post(self, url, headers=None, json=None):
        self.requests.append(json)
        self.calls += 1
        script = [
            [{"functionCall": {"name": "pregled_procesa", "args": {}}}],
            [{"functionCall": {"name": "proceni_meru", "args": {"mera": "daily_payment_run"}}}],
            [{"functionCall": {"name": "predlozi_preporuku",
                               "args": {"mera": "daily_payment_run", "prioritet": 1.0, "obrazlozenje": "Popusti."}}}],
        ]
        if self.calls <= len(script):
            parts = script[self.calls - 1]
        else:
            # use a real number from the estimate, plus one invented
            est = json["contents"][4]["parts"][0]["functionResponse"]["response"]
            parts = [{"text": f"## Sažetak\nNeto {est['net_rsd_per_year']:,.0f} RSD godišnje, a 4321 izmišljeno.".replace(",", ".")}]
        return _Resp({"candidates": [{"content": {"role": "model", "parts": parts}}],
                      "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20}})


def test_gemini_loop_with_scripted_model(ws, monkeypatch):
    fake = FakeGemini()
    monkeypatch.setenv("LLM_DISABLED", "0")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(llm, "gemini_http", lambda: fake)
    res = runner.advise(ws, "ana")
    assert res.source == "llm" and res.error is None
    assert [c["name"] for c in res.tool_calls] == ["pregled_procesa", "proceni_meru", "predlozi_preporuku"]
    recs = ws.recommendations()
    assert len(recs) == 1 and recs[0]["status"] == "PENDING" and recs[0]["created_by"] == "agent (za ana)"
    assert recs[0]["priority"] == 1
    assert res.grounding["ungrounded"] == ["4321"]
    tools = fake.requests[0]["tools"][0]["functionDeclarations"]
    assert any(t["name"] == "predlozi_preporuku" and "enum" in t["parameters"]["properties"]["mera"] for t in tools)


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeAnthropic:
    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        usage = _Block(input_tokens=1000, output_tokens=100)
        if self.calls == 1:
            return _Block(stop_reason="tool_use", usage=usage, content=[
                _Block(type="tool_use", id="t1", name="nalazi", input={}),
                _Block(type="tool_use", id="t2", name="predlozi_preporuku",
                       input={"mera": "sef_erp_integration", "prioritet": 1, "obrazlozenje": "SEF."})])
        return _Block(stop_reason="end_turn", usage=usage, content=[_Block(type="text", text="## Sažetak\nGotovo.")])


def test_anthropic_loop_with_scripted_model(ws, monkeypatch):
    fake = FakeAnthropic()
    monkeypatch.setenv("LLM_DISABLED", "0")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(llm, "client", lambda: fake)
    res = runner.advise(ws, "ana")
    assert res.error is None and res.recommendation_ids
    assert res.cost_usd > 0
    assert ws.recommendations()[0]["lever"] == "sef_erp_integration"
