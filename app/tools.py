"""Tool-Handler, die der Telefonassistent über Vapi aufruft.

Jeder Handler bekommt die Verbindung, die Argumente des Modells und den
Kontext (aktuelle Zeit, toolCallId) und gibt ein dict zurück, das als JSON an
das Modell geht. Fachliche Fehler werfen ToolError mit einem Satz, den das
Modell dem Anrufer sinngemäß weitergeben kann.
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from app.domain import (LEAD_TIME, MAX_DAYS_AHEAD, SERVICES, SLOT_STEP, Booking, find_slots,
                        is_slot_free, spread)

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
FUELS = {"benzin", "diesel", "elektro", "hybrid"}
BODIES = {"kleinwagen", "kompakt", "kombi", "limousine", "suv"}
# Grenzen hängen an der Anruf-ID von Vapi, nicht an der Telefonnummer: die nennt
# der Anrufer selbst, eine Sperre darauf könnte er gegen fremde Kunden richten.
MAX_BOOKINGS_PER_CALL = 3
CANCEL_MAX_FAILURES_PER_CALL = 3


class ToolError(Exception):
    """Fachlicher Fehler, der als `error` an Vapi zurückgeht."""


@dataclass
class Ctx:
    now: datetime
    tool_call_id: str | None = None
    call_id: str = "-"  # Vapi call.id; "-" nur außerhalb echter Anrufe (Tests, lokale Proben)


def say_time(t: datetime) -> str:
    return f"{WEEKDAYS[t.weekday()]}, {t.day}.{t.month}., {t.hour}:{t.minute:02d} Uhr"


def normalize_phone(raw: str) -> str:
    s = re.sub(r"[^\d+]", "", raw or "")
    if s.startswith("00"):
        s = "+" + s[2:]
    elif s.startswith("0"):
        s = "+49" + s[1:]
    digits = re.sub(r"\D", "", s)
    if not 6 <= len(digits) <= 15:
        raise ToolError("Die Telefonnummer scheint unvollständig zu sein. Bitte noch einmal nachfragen.")
    return s if s.startswith("+") else "+" + digits


def _text(args: dict, key: str, required: bool = True, max_len: int = 200) -> str | None:
    val = args.get(key)
    if val is None or str(val).strip() == "":
        if required:
            raise ToolError(f"Es fehlt die Angabe '{key}'.")
        return None
    return str(val).strip()[:max_len]


def _int(args: dict, key: str) -> int | None:
    val = args.get(key)
    if val in (None, ""):
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        raise ToolError(f"'{key}' muss eine Zahl sein.") from None


def _vehicle_summary(row: sqlite3.Row) -> dict:
    return {
        "vehicle_id": row["id"],
        "fahrzeug": f"{row['make']} {row['model']}",
        "baujahr": row["year"],
        "kilometer": row["mileage_km"],
        "preis_euro": row["price_eur"],
        "kraftstoff": row["fuel"],
        "getriebe": row["transmission"],
    }


# --- Bestand -----------------------------------------------------------------

def search_vehicles(conn: sqlite3.Connection, args: dict, ctx: Ctx) -> dict:
    where, params = ["available = 1"], []
    for key, col in (("make", "make"), ("model", "model")):
        val = _text(args, key, required=False, max_len=40)
        if val:
            where.append(f"{col} LIKE ? COLLATE NOCASE")
            params.append(f"%{val}%")
    fuel = (_text(args, "fuel", required=False) or "").lower()
    if fuel:
        if fuel not in FUELS:
            raise ToolError(f"Kraftstoff muss einer von {sorted(FUELS)} sein.")
        where.append("fuel = ?")
        params.append(fuel)
    body = (_text(args, "body", required=False) or "").lower()
    if body:
        if body not in BODIES:
            raise ToolError(f"Karosserie muss eine von {sorted(BODIES)} sein.")
        where.append("body = ?")
        params.append(body)
    for key, sql in (("max_price", "price_eur <= ?"), ("max_mileage", "mileage_km <= ?"),
                     ("min_year", "year >= ?")):
        val = _int(args, key)
        if val is not None:
            where.append(sql)
            params.append(val)
    rows = conn.execute(f"SELECT * FROM vehicles WHERE {' AND '.join(where)} ORDER BY price_eur",
                        params).fetchall()
    return {
        "treffer_gesamt": len(rows),
        "fahrzeuge": [_vehicle_summary(r) for r in rows[:3]],
        "hinweis": "Höchstens drei nennen. Bei mehr Treffern nach weiteren Wünschen fragen."
        if len(rows) > 3 else None,
    }


def get_vehicle_details(conn: sqlite3.Connection, args: dict, ctx: Ctx) -> dict:
    vid = _text(args, "vehicle_id").upper()
    row = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vid,)).fetchone()
    if row is None:
        raise ToolError("Dieses Fahrzeug kenne ich nicht. Bitte erneut im Bestand suchen.")
    out = _vehicle_summary(row)
    out.update(leistung_kw=row["power_kw"], farbe=row["color"], karosserie=row["body"],
               ausstattung=json.loads(row["features"]), verfuegbar=bool(row["available"]))
    return out


# --- Termine -----------------------------------------------------------------

def _service(args: dict):
    key = (_text(args, "service") or "").lower()
    if key not in SERVICES:
        raise ToolError(f"Unbekannte Leistung. Möglich sind: {', '.join(SERVICES)}.")
    return SERVICES[key]


def _vehicle_for(conn, service, args) -> str | None:
    vid = _text(args, "vehicle_id", required=False)
    if service.needs_vehicle and not vid:
        raise ToolError("Für eine Probefahrt brauche ich das Fahrzeug. Bitte zuerst im Bestand suchen.")
    if vid:
        vid = vid.upper()
        if conn.execute("SELECT 1 FROM vehicles WHERE id = ? AND available = 1", (vid,)).fetchone() is None:
            raise ToolError("Dieses Fahrzeug ist nicht im Bestand.")
    return vid if service.needs_vehicle else None


def _bookings(conn, day_from: date, day_to: date) -> list[Booking]:
    rows = conn.execute(
        "SELECT start, end, department, vehicle_id FROM appointments "
        "WHERE status = 'booked' AND start < ? AND end > ?",
        ((day_to + timedelta(days=1)).isoformat(), day_from.isoformat())).fetchall()
    return [Booking(datetime.fromisoformat(r["start"]), datetime.fromisoformat(r["end"]),
                    r["department"], r["vehicle_id"]) for r in rows]


def _date(args, key, default: date) -> date:
    raw = _text(args, key, required=False)
    if not raw:
        return default
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        raise ToolError(f"'{key}' bitte im Format JJJJ-MM-TT.") from None


def check_availability(conn: sqlite3.Connection, args: dict, ctx: Ctx) -> dict:
    service = _service(args)
    vid = _vehicle_for(conn, service, args)
    wanted = _date(args, "date", ctx.now.date())
    bookings = _bookings(conn, ctx.now.date(), ctx.now.date() + timedelta(days=15))
    day, slots = find_slots(service, wanted, bookings, ctx.now, vid)
    if day is None:
        return {"frei": False, "hinweis": "In den nächsten zwei Wochen ist nichts frei. Rückruf anbieten."}
    shown = spread(slots)
    return {
        "frei": True,
        "leistung": service.label,
        "dauer_minuten": service.minutes,
        "tag": f"{WEEKDAYS[day.weekday()]}, {day.day}.{day.month}.",
        "ausweichtag": day != wanted,
        "termine": [{"start": s.isoformat(timespec="minutes"), "gesprochen": f"{s.hour}:{s.minute:02d} Uhr"}
                    for s in shown],
        "weitere_freie_zeiten": len(slots) - len(shown),
    }


def _new_code(conn) -> str:
    while True:
        code = f"{secrets.randbelow(900000) + 100000}"
        if conn.execute("SELECT 1 FROM appointments WHERE code = ?", (code,)).fetchone() is None:
            return code


def _confirmation(row: sqlite3.Row) -> dict:
    start = datetime.fromisoformat(row["start"])
    code = row["code"]
    return {
        "gebucht": True,
        "buchungscode": code,
        "buchungscode_gesprochen": f"{code[:3]} {code[3:]}",
        "termin": say_time(start),
        "leistung": SERVICES[row["service"]].label,
    }


def book_appointment(conn: sqlite3.Connection, args: dict, ctx: Ctx) -> dict:
    if ctx.tool_call_id:
        prev = conn.execute("SELECT * FROM appointments WHERE tool_call_id = ?", (ctx.tool_call_id,)).fetchone()
        if prev is not None:
            return _confirmation(prev)
    service = _service(args)
    vid = _vehicle_for(conn, service, args)
    name = _text(args, "name", max_len=80)
    phone = normalize_phone(_text(args, "phone", max_len=40))
    try:
        start = datetime.fromisoformat(_text(args, "start"))
    except ValueError:
        raise ToolError("'start' bitte im Format JJJJ-MM-TTTHH:MM, wie von check_availability geliefert.") from None
    start = start.replace(tzinfo=None, second=0, microsecond=0)
    # Dieselben Grenzen wie check_availability, sonst ließen sie sich mit einem
    # direkt gebauten Tool-Aufruf umgehen.
    if start < ctx.now:
        raise ToolError("Dieser Zeitpunkt liegt in der Vergangenheit.")
    if start < ctx.now + LEAD_TIME:
        raise ToolError("Termine sind frühestens eine Stunde im Voraus möglich.")
    if start.date() > ctx.now.date() + timedelta(days=MAX_DAYS_AHEAD):
        raise ToolError("Termine sind höchstens zwei Wochen im Voraus buchbar. Rückruf anbieten.")
    if (start.hour * 60 + start.minute) % int(SLOT_STEP.total_seconds() // 60):
        raise ToolError("Bitte eine der von check_availability genannten Uhrzeiten verwenden.")
    end = start + timedelta(minutes=service.minutes)
    conn.execute("BEGIN IMMEDIATE")
    try:
        in_call = conn.execute("SELECT COUNT(*) FROM appointments WHERE call_id = ?",
                               (ctx.call_id,)).fetchone()[0]
        if in_call >= MAX_BOOKINGS_PER_CALL:
            raise ToolError("In diesem Gespräch wurden schon mehrere Termine gebucht. Bitte an einen Mitarbeiter übergeben.")
        if not is_slot_free(service, start, _bookings(conn, start.date(), start.date()), vid):
            raise ToolError("Dieser Termin ist leider nicht mehr frei. Bitte erneut nach freien Zeiten suchen.")
        code = _new_code(conn)
        conn.execute(
            "INSERT INTO appointments (code, service, department, start, end, vehicle_id, name, phone,"
            " license_plate, notes, tool_call_id, call_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, service.key, service.department, start.isoformat(timespec="minutes"),
             end.isoformat(timespec="minutes"), vid, name, phone,
             _text(args, "license_plate", required=False, max_len=15),
             _text(args, "notes", required=False, max_len=500), ctx.tool_call_id, ctx.call_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return _confirmation(conn.execute("SELECT * FROM appointments WHERE code = ?", (code,)).fetchone())


def cancel_appointment(conn: sqlite3.Connection, args: dict, ctx: Ctx) -> dict:
    code = re.sub(r"\D", "", _text(args, "code", max_len=20))
    phone = normalize_phone(_text(args, "phone", max_len=40))
    # Gegen Durchprobieren: wenige Fehlversuche je Anruf. Bei 900.000 Codes und
    # drei Versuchen pro Anruf bräuchte ein Angreifer Hunderttausende Anrufe.
    conn.execute("BEGIN IMMEDIATE")
    try:
        failures = conn.execute("SELECT COUNT(*) FROM cancel_failures WHERE call_id = ?",
                                (ctx.call_id,)).fetchone()[0]
        if failures >= CANCEL_MAX_FAILURES_PER_CALL:
            raise ToolError("Zu viele Fehlversuche in diesem Gespräch. Bitte an einen Mitarbeiter übergeben.")
        row = conn.execute("SELECT * FROM appointments WHERE code = ? AND phone = ?", (code, phone)).fetchone()
        if row is None:
            conn.execute("INSERT INTO cancel_failures (call_id) VALUES (?)", (ctx.call_id,))
            conn.commit()
            raise ToolError("Zu diesem Buchungscode und dieser Telefonnummer finde ich keinen Termin.")
        conn.commit()
    except ToolError:
        if conn.in_transaction:
            conn.rollback()
        raise
    if row["status"] == "cancelled":
        return {"storniert": True, "hinweis": "Der Termin war bereits abgesagt."}
    conn.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (row["id"],))
    conn.commit()
    return {"storniert": True, "termin": say_time(datetime.fromisoformat(row["start"])),
            "leistung": SERVICES[row["service"]].label}


# --- Rückruf -----------------------------------------------------------------

def request_callback(conn: sqlite3.Connection, args: dict, ctx: Ctx) -> dict:
    if ctx.tool_call_id and conn.execute(
            "SELECT 1 FROM callbacks WHERE tool_call_id = ?", (ctx.tool_call_id,)).fetchone():
        return {"angelegt": True}
    conn.execute(
        "INSERT INTO callbacks (name, phone, topic, preferred_time, vehicle_id, tool_call_id)"
        " VALUES (?,?,?,?,?,?)",
        (_text(args, "name", max_len=80), normalize_phone(_text(args, "phone", max_len=40)),
         _text(args, "topic", max_len=500), _text(args, "preferred_time", required=False, max_len=80),
         _text(args, "vehicle_id", required=False, max_len=10), ctx.tool_call_id))
    conn.commit()
    return {"angelegt": True, "hinweis": "Ein Mitarbeiter meldet sich in der Regel am selben Werktag."}


HANDLERS: dict[str, Callable[[sqlite3.Connection, dict, Ctx], dict[str, Any]]] = {
    "search_vehicles": search_vehicles,
    "get_vehicle_details": get_vehicle_details,
    "check_availability": check_availability,
    "book_appointment": book_appointment,
    "cancel_appointment": cancel_appointment,
    "request_callback": request_callback,
}
