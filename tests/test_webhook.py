import json

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import NOW

TOKEN = "t" * 32


def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "w.db"), token=TOKEN, clock=lambda: NOW)
    return TestClient(app)


def tool_call(call_id, name, args, as_string=False):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args) if as_string else args}}


def post(c, message, token=TOKEN):
    return c.post("/vapi/webhook", json={"message": message},
                  headers={"Authorization": f"Bearer {token}"} if token else {})


def test_rejects_missing_or_wrong_token(tmp_path):
    c = client(tmp_path)
    assert post(c, {"type": "tool-calls"}, token=None).status_code == 401
    assert post(c, {"type": "tool-calls"}, token="falsch").status_code == 401


def test_refuses_to_start_without_strong_token(tmp_path):
    import pytest
    with pytest.raises(RuntimeError):
        create_app(db_path=str(tmp_path / "x.db"), token="kurz")


def test_tool_calls_return_one_result_per_call(tmp_path):
    c = client(tmp_path)
    msg = {"type": "tool-calls", "toolCallList": [
        tool_call("1", "search_vehicles", {"fuel": "hybrid"}),
        tool_call("2", "check_availability", {"service": "hu_au"}, as_string=True),
        tool_call("3", "gibt_es_nicht", {}),
        tool_call("4", "search_vehicles", {"fuel": "wasserstoff"}),
    ]}
    r = post(c, msg)
    assert r.status_code == 200
    res = {x["toolCallId"]: x for x in r.json()["results"]}
    assert set(res) == {"1", "2", "3", "4"}
    assert json.loads(res["1"]["result"])["treffer_gesamt"] == 2
    assert json.loads(res["2"]["result"])["frei"] is True
    assert "error" in res["3"] and "result" not in res["3"]
    assert "error" in res["4"]


def test_invalid_json_arguments_become_error(tmp_path):
    c = client(tmp_path)
    msg = {"type": "tool-calls", "toolCallList": [
        {"id": "1", "function": {"name": "search_vehicles", "arguments": "{kaputt"}}]}
    assert "error" in post(c, msg).json()["results"][0]


def test_end_of_call_report_is_stored_without_transcript(tmp_path):
    c = client(tmp_path)
    msg = {"type": "end-of-call-report", "endedReason": "customer-ended-call",
           "durationSeconds": 93.5, "cost": 0.21, "call": {"id": "call-1"},
           "analysis": {"summary": "Probefahrt Tesla vereinbart."},
           "artifact": {"transcript": "AI: Hallo ... User: ..."}}
    assert post(c, msg).status_code == 200
    from app import db
    conn = db.connect(str(tmp_path / "w.db"))
    row = conn.execute("SELECT * FROM call_reports").fetchone()
    assert row["summary"] == "Probefahrt Tesla vereinbart." and row["transcript"] is None


def test_other_message_types_are_acknowledged(tmp_path):
    assert post(client(tmp_path), {"type": "status-update"}).json() == {}


def test_call_id_from_vapi_scopes_the_cancel_lock(tmp_path):
    c = client(tmp_path)

    def cancel(call_id, code):
        msg = {"type": "tool-calls", "call": {"id": call_id}, "toolCallList": [
            tool_call("x", "cancel_appointment", {"code": code, "phone": "030 1234567"})]}
        return post(c, msg).json()["results"][0]["error"]

    for _ in range(3):
        assert "keinen Termin" in cancel("call-1", "111111")
    assert "Fehlversuche" in cancel("call-1", "111111")
    # ein neuer Anruf startet ohne Sperre
    assert "keinen Termin" in cancel("call-2", "111111")


def test_caller_number_is_taken_from_vapi_call(tmp_path):
    c = client(tmp_path)

    def cancel(call_id, caller):
        msg = {"type": "tool-calls", "call": {"id": call_id, "customer": {"number": caller}},
               "toolCallList": [tool_call("x", "cancel_appointment", {"code": "1", "phone": "030 1234567"})]}
        return post(c, msg).json()["results"][0]["error"]

    for i in range(10):
        cancel(f"a{i}", "+4930999")
    assert "Mitarbeiter" in cancel("a-fremd", "+4930999")
    # von der hinterlegten Nummer aus wird weiter geprüft statt abgewiesen
    assert "keinen Termin" in cancel("a-echt", "030 1234567")


def test_rejection_reason_is_logged_without_token(tmp_path, caplog):
    c = client(tmp_path)
    with caplog.at_level("WARNING"):
        post(c, {"type": "tool-calls"}, token="x" * 40)
    assert "falscher Token" in caplog.text and "x" * 40 not in caplog.text


def test_demo_page_only_locally(tmp_path, monkeypatch):
    monkeypatch.setenv("VAPI_PUBLIC_KEY", "pub-123")
    monkeypatch.setenv("VAPI_ASSISTANT_ID", "asst-456")
    from fastapi.testclient import TestClient
    app = create_app(db_path=str(tmp_path / "d.db"), token=TOKEN, clock=lambda: NOW)
    local = TestClient(app, base_url="http://localhost:8787", client=("127.0.0.1", 50000))
    r = local.get("/demo")
    assert r.status_code == 200 and "pub-123" in r.text and "asst-456" in r.text
    # über den Tunnel: fremder Host bzw. Cloudflare-Header
    assert TestClient(app, base_url="https://abc.trycloudflare.com",
                      client=("127.0.0.1", 50000)).get("/demo").status_code == 404
    assert local.get("/demo", headers={"cf-connecting-ip": "1.2.3.4"}).status_code == 404
    # der Private Key landet nie auf der Seite
    monkeypatch.setenv("VAPI_API_KEY", "privat-geheim")
    assert "privat-geheim" not in local.get("/demo").text
