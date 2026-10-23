import pytest

from app.tools import (Ctx, ToolError, book_appointment, cancel_appointment, check_availability,
                       get_vehicle_details, normalize_phone, request_callback, search_vehicles)
from tests.conftest import NOW

CTX = Ctx(now=NOW)


def ctx(call_id=None):
    return Ctx(now=NOW, tool_call_id=call_id)


# --- Bestand -----------------------------------------------------------------

def test_search_filters_and_sorts_by_price(conn):
    out = search_vehicles(conn, {"fuel": "elektro", "max_price": 25000}, CTX)
    assert out["treffer_gesamt"] == 2
    prices = [v["preis_euro"] for v in out["fahrzeuge"]]
    assert prices == sorted(prices)
    assert all(v["kraftstoff"] == "elektro" for v in out["fahrzeuge"])


def test_search_caps_at_three_and_hints(conn):
    out = search_vehicles(conn, {}, CTX)
    assert out["treffer_gesamt"] == 14
    assert len(out["fahrzeuge"]) == 3
    assert out["hinweis"]


def test_search_make_is_case_insensitive_and_partial(conn):
    out = search_vehicles(conn, {"make": "volkswagen", "body": "kombi"}, CTX)
    assert [v["vehicle_id"] for v in out["fahrzeuge"]] == ["V002"]


def test_search_rejects_unknown_fuel(conn):
    with pytest.raises(ToolError):
        search_vehicles(conn, {"fuel": "wasserstoff"}, CTX)


def test_vehicle_details(conn):
    out = get_vehicle_details(conn, {"vehicle_id": "v004"}, CTX)
    assert out["fahrzeug"].startswith("Tesla") and "Wärmepumpe" in out["ausstattung"]
    with pytest.raises(ToolError):
        get_vehicle_details(conn, {"vehicle_id": "X999"}, CTX)


# --- Termine -----------------------------------------------------------------

def test_phone_normalization():
    assert normalize_phone("0151 590 824 65") == "+4915159082465"
    assert normalize_phone("0049 30 1234567") == "+49301234567"
    assert normalize_phone("+49 (30) 123-4567") == "+49301234567"
    with pytest.raises(ToolError):
        normalize_phone("123")


def test_availability_then_booking_then_slot_is_used(conn):
    avail = check_availability(conn, {"service": "inspektion", "date": "2026-10-26"}, CTX)
    assert avail["frei"] and not avail["ausweichtag"]
    first = avail["termine"][0]["start"]
    assert first == "2026-10-26T08:00"
    args = {"service": "inspektion", "start": first, "name": "Erika Muster", "phone": "030 1234567"}
    one = book_appointment(conn, args, ctx("a"))
    two = book_appointment(conn, args, ctx("b"))
    assert one["gebucht"] and two["gebucht"] and one["buchungscode"] != two["buchungscode"]
    # zwei Hebebühnen belegt, der dritte Versuch scheitert
    with pytest.raises(ToolError, match="nicht mehr frei"):
        book_appointment(conn, args, ctx("c"))
    again = check_availability(conn, {"service": "inspektion", "date": "2026-10-26"}, CTX)
    assert again["termine"][0]["start"] != first


def test_same_tool_call_id_does_not_double_book(conn):
    args = {"service": "beratung", "start": "2026-10-26T10:00", "name": "A", "phone": "030 1234567"}
    one = book_appointment(conn, args, ctx("retry-1"))
    two = book_appointment(conn, args, ctx("retry-1"))
    assert one["buchungscode"] == two["buchungscode"]
    assert conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 1


def test_test_drive_needs_vehicle_and_blocks_that_vehicle(conn):
    with pytest.raises(ToolError, match="Fahrzeug"):
        check_availability(conn, {"service": "probefahrt"}, CTX)
    args = {"service": "probefahrt", "vehicle_id": "V004", "start": "2026-10-26T11:00",
            "name": "A", "phone": "030 1234567"}
    book_appointment(conn, args, ctx("x"))
    with pytest.raises(ToolError, match="nicht mehr frei"):
        book_appointment(conn, {**args, "start": "2026-10-26T11:30"}, ctx("y"))
    # anderes Fahrzeug zur gleichen Zeit geht
    book_appointment(conn, {**args, "vehicle_id": "V005"}, ctx("z"))


def test_booking_outside_hours_or_in_past_fails(conn):
    base = {"service": "oelwechsel", "name": "A", "phone": "030 1234567"}
    with pytest.raises(ToolError):
        book_appointment(conn, {**base, "start": "2026-10-31T10:00"}, ctx())  # Samstag, Werkstatt zu
    with pytest.raises(ToolError, match="Vergangenheit"):
        book_appointment(conn, {**base, "start": "2026-10-25T10:00"}, ctx())
    with pytest.raises(ToolError, match="Format"):
        book_appointment(conn, {**base, "start": "morgen früh"}, ctx())


def test_weekend_request_moves_to_next_open_day(conn):
    out = check_availability(conn, {"service": "reifenwechsel", "date": "2026-11-01"}, CTX)
    assert out["ausweichtag"] and out["tag"].startswith("Montag")


def test_cancel_needs_matching_phone(conn):
    booked = book_appointment(conn, {"service": "beratung", "start": "2026-10-26T12:00",
                                     "name": "A", "phone": "0151 11122233"}, ctx())
    code = booked["buchungscode_gesprochen"]  # "1 2 3, 4 5 6", wie gesprochen
    with pytest.raises(ToolError):
        cancel_appointment(conn, {"code": code, "phone": "0151 99999999"}, CTX)
    out = cancel_appointment(conn, {"code": code, "phone": "+49 151 11122233"}, CTX)
    assert out["storniert"]
    # Slot ist wieder frei
    avail = check_availability(conn, {"service": "beratung", "date": "2026-10-26"}, CTX)
    assert avail["frei"]


def test_callback_is_idempotent(conn):
    args = {"name": "Max", "phone": "030 1234567", "topic": "Finanzierung für den Kuga"}
    request_callback(conn, args, ctx("cb"))
    request_callback(conn, args, ctx("cb"))
    assert conn.execute("SELECT COUNT(*) FROM callbacks").fetchone()[0] == 1
    with pytest.raises(ToolError):
        request_callback(conn, {"name": "Max", "phone": "030 1234567"}, ctx())


# --- Härtung (Sicherheitsprüfung) --------------------------------------------

def test_booking_enforces_same_limits_as_availability(conn):
    base = {"service": "beratung", "name": "A", "phone": "030 1234567"}
    with pytest.raises(ToolError, match="frühestens"):
        book_appointment(conn, {**base, "start": "2026-10-26T07:30"}, ctx())  # nur 30 min Vorlauf
    with pytest.raises(ToolError, match="zwei Wochen"):
        book_appointment(conn, {**base, "start": "2026-11-17T10:00"}, ctx())
    with pytest.raises(ToolError, match="Uhrzeiten"):
        book_appointment(conn, {**base, "start": "2026-10-26T10:10"}, ctx())


def test_bookings_per_call_are_capped(conn):
    base = {"service": "beratung", "name": "A", "phone": "030 1234567"}
    call = lambda tc, cid="call-A": Ctx(now=NOW, tool_call_id=tc, call_id=cid)
    for i, t in enumerate(["10:00", "11:00", "12:00"]):
        book_appointment(conn, {**base, "start": f"2026-10-26T{t}"}, call(f"p{i}"))
    with pytest.raises(ToolError, match="mehrere Termine"):
        book_appointment(conn, {**base, "start": "2026-10-26T13:00"}, call("p4"))
    # ein anderer Anruf mit derselben Nummer ist nicht blockiert (keine Sperre gegen fremde Nummern)
    book_appointment(conn, {**base, "start": "2026-10-26T13:00"}, call("q", "call-B"))


def test_cancel_failures_lock_only_the_attacking_call(conn):
    booked = book_appointment(conn, {"service": "beratung", "start": "2026-10-26T12:00",
                                     "name": "A", "phone": "0151 11122233"}, ctx())
    attacker = Ctx(now=NOW, call_id="call-angreifer")
    for wrong in range(3):
        with pytest.raises(ToolError, match="keinen Termin"):
            cancel_appointment(conn, {"code": f"00000{wrong}", "phone": "0151 11122233"}, attacker)
    with pytest.raises(ToolError, match="Fehlversuche"):
        cancel_appointment(conn, {"code": booked["buchungscode"], "phone": "0151 11122233"}, attacker)
    # der echte Kunde in seinem eigenen Anruf kann trotzdem absagen
    owner = Ctx(now=NOW, call_id="call-kunde")
    assert cancel_appointment(conn, {"code": booked["buchungscode"], "phone": "0151 11122233"}, owner)["storniert"]


def test_many_calls_cannot_brute_force_but_owner_still_cancels(conn):
    booked = book_appointment(conn, {"service": "beratung", "start": "2026-10-26T12:00",
                                     "name": "A", "phone": "0151 11122233"}, ctx())
    # Angreifer verteilt Fehlversuche auf viele Anrufe (je neue call.id)
    for i in range(10):
        with pytest.raises(ToolError, match="keinen Termin"):
            cancel_appointment(conn, {"code": "000000", "phone": "0151 11122233"},
                               Ctx(now=NOW, call_id=f"angriff-{i}", caller_number="+4930999"))
    # danach auch mit dem richtigen Code nur noch von der hinterlegten Nummer aus
    with pytest.raises(ToolError, match="Mitarbeiter"):
        cancel_appointment(conn, {"code": booked["buchungscode"], "phone": "0151 11122233"},
                           Ctx(now=NOW, call_id="angriff-x", caller_number="+4930999"))
    owner = Ctx(now=NOW, call_id="kunde", caller_number="+4915111122233")
    assert cancel_appointment(conn, {"code": booked["buchungscode"], "phone": "0151 11122233"}, owner)["storniert"]
