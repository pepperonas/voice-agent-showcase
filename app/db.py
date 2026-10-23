"""SQLite-Speicher: Bestand, Termine, Rückrufe, Anrufberichte."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles (
    id TEXT PRIMARY KEY,
    make TEXT NOT NULL, model TEXT NOT NULL, year INTEGER NOT NULL,
    mileage_km INTEGER NOT NULL, price_eur INTEGER NOT NULL,
    fuel TEXT NOT NULL, body TEXT NOT NULL, transmission TEXT NOT NULL,
    power_kw INTEGER NOT NULL, color TEXT NOT NULL, features TEXT NOT NULL,
    available INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    service TEXT NOT NULL, department TEXT NOT NULL,
    start TEXT NOT NULL, end TEXT NOT NULL,
    vehicle_id TEXT REFERENCES vehicles(id),
    name TEXT NOT NULL, phone TEXT NOT NULL,
    license_plate TEXT, notes TEXT,
    status TEXT NOT NULL DEFAULT 'booked',
    tool_call_id TEXT UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS callbacks (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL, phone TEXT NOT NULL, topic TEXT NOT NULL,
    preferred_time TEXT, vehicle_id TEXT,
    tool_call_id TEXT UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS call_reports (
    call_id TEXT PRIMARY KEY,
    ended_reason TEXT, duration_seconds REAL, cost_usd REAL,
    summary TEXT, transcript TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

INVENTORY = Path(__file__).resolve().parent.parent / "data" / "inventory.json"


def connect(path: str) -> sqlite3.Connection:
    # Autocommit; Buchungen öffnen ihre Transaktion selbst mit BEGIN IMMEDIATE.
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init(conn: sqlite3.Connection, seed: bool = True) -> None:
    conn.executescript(SCHEMA)
    if seed and conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0] == 0:
        for v in json.loads(INVENTORY.read_text(encoding="utf-8")):
            conn.execute(
                "INSERT INTO vehicles (id, make, model, year, mileage_km, price_eur, fuel, body,"
                " transmission, power_kw, color, features) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (v["id"], v["make"], v["model"], v["year"], v["mileage_km"], v["price_eur"],
                 v["fuel"], v["body"], v["transmission"], v["power_kw"], v["color"],
                 json.dumps(v["features"], ensure_ascii=False)))
    conn.commit()
