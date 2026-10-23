from assistant.config import assistant, tools
from app.tools import HANDLERS


def function_tools():
    return {t["function"]["name"]: t["function"] for t in tools() if t["type"] == "function"}


def test_every_handler_is_exposed_and_vice_versa():
    assert set(function_tools()) == set(HANDLERS)


class Recording(dict):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.read = set()

    def get(self, key, default=None):
        self.read.add(key)
        return super().get(key, default)


VALID_ARGS = {
    "search_vehicles": {"make": "Volkswagen", "model": "Golf", "fuel": "benzin", "body": "kompakt",
                        "max_price": 30000, "max_mileage": 90000, "min_year": 2015},
    "get_vehicle_details": {"vehicle_id": "V001"},
    "check_availability": {"service": "probefahrt", "date": "2026-10-26", "vehicle_id": "V001"},
    "book_appointment": {"service": "probefahrt", "start": "2026-10-26T10:00", "name": "A",
                         "phone": "030 1234567", "vehicle_id": "V001", "license_plate": "B-AB 123",
                         "notes": "x"},
    "cancel_appointment": {"code": "123456", "phone": "030 1234567"},
    "request_callback": {"name": "A", "phone": "030 1234567", "topic": "Finanzierung",
                         "preferred_time": "morgen", "vehicle_id": "V001"},
}


def test_tool_arguments_are_read_by_the_handler(conn):
    # Jedes Argument, das das Modell schicken darf, muss der Handler auch auslesen,
    # sonst verschwindet es still.
    from app.tools import Ctx, ToolError
    from tests.conftest import NOW
    for name, fn in function_tools().items():
        args = Recording(VALID_ARGS[name])
        assert set(args) == set(fn["parameters"]["properties"]), name
        try:
            HANDLERS[name](conn, args, Ctx(now=NOW, tool_call_id=f"t-{name}"))
        except ToolError:
            pass  # z. B. Storno ohne Termin; gelesen wurde trotzdem alles
        assert set(fn["parameters"]["properties"]) <= args.read, (name, set(fn["parameters"]["properties"]) - args.read)


def test_required_fields_are_declared_in_properties():
    for name, fn in function_tools().items():
        assert set(fn["parameters"]["required"]) <= set(fn["parameters"]["properties"]), name


def test_transfer_only_with_number():
    assert not any(t["type"] == "transferCall" for t in tools())
    t = [t for t in tools("+4930123456") if t["type"] == "transferCall"][0]
    assert t["destinations"][0]["number"] == "+4930123456"


def test_privacy_and_webhook_settings():
    cfg = assistant("https://example.org/vapi/webhook", "cred-1")
    assert cfg["artifactPlan"]["recordingEnabled"] is False
    assert cfg["server"] == {"url": "https://example.org/vapi/webhook", "credentialId": "cred-1",
                             "timeoutSeconds": 10}
    assert set(cfg["serverMessages"]) == {"tool-calls", "end-of-call-report"}
    assert "KI" in cfg["firstMessage"]
    assert cfg["transcriber"]["language"] == "de"


def test_agent_can_hang_up():
    assert any(t["type"] == "endCall" for t in tools())
