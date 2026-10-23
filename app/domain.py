"""Fachregeln des Autohauses: Leistungen, Öffnungszeiten, freie Termine.

Rein und ohne Datenbank, damit die Regeln einzeln testbar sind. Alle Zeiten
sind naive datetime-Werte in Ortszeit (Europe/Berlin).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

SLOT_STEP = timedelta(minutes=30)
LEAD_TIME = timedelta(hours=1)
MAX_DAYS_AHEAD = 14


@dataclass(frozen=True)
class Service:
    key: str
    label: str
    department: str  # "verkauf" oder "werkstatt"
    minutes: int
    needs_vehicle: bool = False


SERVICES: dict[str, Service] = {s.key: s for s in [
    Service("probefahrt", "Probefahrt", "verkauf", 45, needs_vehicle=True),
    Service("beratung", "Verkaufsberatung", "verkauf", 30),
    Service("inspektion", "Inspektion", "werkstatt", 120),
    Service("oelwechsel", "Ölwechsel", "werkstatt", 60),
    Service("reifenwechsel", "Reifenwechsel", "werkstatt", 30),
    Service("hu_au", "HU/AU", "werkstatt", 60),
    Service("klimaservice", "Klimaservice", "werkstatt", 60),
]}

# Wochentag (0 = Montag) -> (Öffnung, Schluss)
HOURS: dict[str, dict[int, tuple[time, time]]] = {
    "verkauf": {**{d: (time(9), time(18)) for d in range(5)}, 5: (time(10), time(14))},
    "werkstatt": {d: (time(8), time(17)) for d in range(5)},
}
CAPACITY = {"verkauf": 2, "werkstatt": 2}


@dataclass(frozen=True)
class Booking:
    start: datetime
    end: datetime
    department: str
    vehicle_id: str | None = None


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def is_slot_free(service: Service, start: datetime, bookings: list[Booking],
                 vehicle_id: str | None = None) -> bool:
    """Passt der Termin in die Öffnungszeit, die Kapazität und (bei Probefahrten) das Fahrzeug?"""
    hours = HOURS[service.department].get(start.weekday())
    if hours is None:
        return False
    end = start + timedelta(minutes=service.minutes)
    if start.time() < hours[0] or end.time() > hours[1] or end.date() != start.date():
        return False
    clashing = [b for b in bookings if b.department == service.department
                and overlaps(start, end, b.start, b.end)]
    if len(clashing) >= CAPACITY[service.department]:
        return False
    if vehicle_id and any(b.vehicle_id == vehicle_id and overlaps(start, end, b.start, b.end)
                          for b in bookings):
        return False
    return True


def free_slots_on(service: Service, day: date, bookings: list[Booking], now: datetime,
                  vehicle_id: str | None = None) -> list[datetime]:
    hours = HOURS[service.department].get(day.weekday())
    if hours is None:
        return []
    earliest = now + LEAD_TIME
    t = datetime.combine(day, hours[0])
    out = []
    while t < datetime.combine(day, hours[1]):
        if t >= earliest and is_slot_free(service, t, bookings, vehicle_id):
            out.append(t)
        t += SLOT_STEP
    return out


def find_slots(service: Service, wanted: date, bookings: list[Booking], now: datetime,
               vehicle_id: str | None = None) -> tuple[date | None, list[datetime]]:
    """Freie Slots am Wunschtag, sonst am nächsten Tag mit freien Slots (bis MAX_DAYS_AHEAD)."""
    last = now.date() + timedelta(days=MAX_DAYS_AHEAD)
    day = max(wanted, now.date())
    while day <= last:
        slots = free_slots_on(service, day, bookings, now, vehicle_id)
        if slots:
            return day, slots
        day += timedelta(days=1)
    return None, []


def spread(slots: list[datetime], n: int = 6) -> list[datetime]:
    """Höchstens n Slots, gleichmäßig über den Tag verteilt (am Telefon liest man keine 18 Zeiten vor)."""
    if len(slots) <= n:
        return slots
    step = (len(slots) - 1) / (n - 1)
    return [slots[round(i * step)] for i in range(n)]
