from datetime import date, datetime, timedelta

from app.domain import SERVICES, Booking, find_slots, free_slots_on, is_slot_free, spread

# Montag, 26.10.2026, 07:00
NOW = datetime(2026, 10, 26, 7, 0)
MON = date(2026, 10, 26)
SAT = date(2026, 10, 31)
SUN = date(2026, 11, 1)


def b(start, minutes, dept, vehicle=None):
    return Booking(start, start + timedelta(minutes=minutes), dept, vehicle)


def test_werkstatt_slots_respect_hours_and_duration():
    slots = free_slots_on(SERVICES["inspektion"], MON, [], NOW)
    assert slots[0] == datetime(2026, 10, 26, 8, 0)
    # 120 min müssen bis 17 Uhr fertig sein
    assert slots[-1] == datetime(2026, 10, 26, 15, 0)


def test_lead_time_hides_slots_in_the_next_hour():
    now = datetime(2026, 10, 26, 10, 10)
    slots = free_slots_on(SERVICES["oelwechsel"], MON, [], now)
    assert slots[0] == datetime(2026, 10, 26, 11, 30)


def test_saturday_only_sales_sunday_closed():
    assert free_slots_on(SERVICES["inspektion"], SAT, [], NOW) == []
    sat = free_slots_on(SERVICES["beratung"], SAT, [], NOW)
    assert sat[0].hour == 10 and sat[-1] == datetime(2026, 10, 31, 13, 30)
    assert free_slots_on(SERVICES["beratung"], SUN, [], NOW) == []


def test_capacity_two_parallel():
    start = datetime(2026, 10, 26, 9, 0)
    one = [b(start, 60, "werkstatt")]
    two = one + [b(start, 60, "werkstatt")]
    svc = SERVICES["oelwechsel"]
    assert is_slot_free(svc, start, one)
    assert not is_slot_free(svc, start, two)
    # Verkauf ist unabhängig von der Werkstatt belegt
    assert is_slot_free(SERVICES["beratung"], start, two)


def test_vehicle_cannot_be_test_driven_twice_at_once():
    start = datetime(2026, 10, 26, 11, 0)
    taken = [b(start, 45, "verkauf", "V001")]
    svc = SERVICES["probefahrt"]
    assert not is_slot_free(svc, datetime(2026, 10, 26, 11, 30), taken, "V001")
    assert is_slot_free(svc, datetime(2026, 10, 26, 11, 30), taken, "V002")
    assert is_slot_free(svc, datetime(2026, 10, 26, 12, 0), taken, "V001")


def test_find_slots_falls_forward_to_next_open_day():
    day, slots = find_slots(SERVICES["inspektion"], SAT, [], NOW)
    assert day == date(2026, 11, 2)  # Montag
    assert slots


def test_find_slots_never_returns_past_days():
    day, _ = find_slots(SERVICES["beratung"], date(2026, 10, 1), [], NOW)
    assert day == MON


def test_spread_keeps_first_and_last():
    slots = [datetime(2026, 10, 26, 8) + timedelta(minutes=30 * i) for i in range(17)]
    out = spread(slots, 6)
    assert len(out) == 6 and out[0] == slots[0] and out[-1] == slots[-1]
