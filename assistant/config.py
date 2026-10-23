"""Assistent als Code: baut die Vapi-Konfiguration aus Prompt und Tool-Schemata.

Rein (ohne Netzwerk), damit Tests die Konfiguration gegen die Tool-Handler
prüfen können. `scripts/sync_assistant.py` schickt das Ergebnis an Vapi.
"""
from __future__ import annotations

from pathlib import Path

from app.domain import SERVICES
from app.tools import BODIES, FUELS

NAME = "Autohaus Kranich"
PROMPT = (Path(__file__).parent / "prompt.md").read_text(encoding="utf-8")

FIRST_MESSAGE = ("Autohaus Kranich, hier ist Lena, die digitale Assistentin. Ich bin eine KI. "
                 "Wie kann ich Ihnen helfen?")

KEYTERMS = ["Kranich", "Probefahrt", "Inspektion", "Ölwechsel", "Reifenwechsel", "HU", "AU",
            "Klimaservice", "Kennzeichen", "Buchungscode", "Kombi", "SUV", "Hybrid", "Elektro",
            "Golf", "Passat", "Octavia", "Tesla", "Kona", "Corolla", "Corsa", "Kuga", "Clio",
            "Ibiza", "ID.3", "Q3"]


def _fn(name: str, description: str, properties: dict, required: list[str] | None = None,
        request_message: str | None = None) -> dict:
    tool = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required or []},
        },
    }
    if request_message:
        tool["messages"] = [{"type": "request-start", "content": request_message}]
    return tool


def tools(transfer_number: str | None = None) -> list[dict]:
    service_enum = list(SERVICES)
    out = [
        _fn("search_vehicles",
            "Sucht im Fahrzeugbestand. Alle Filter optional. Liefert höchstens drei Fahrzeuge, günstigste zuerst.",
            {
                "make": {"type": "string", "description": "Marke, z. B. Volkswagen"},
                "model": {"type": "string", "description": "Modell oder Teil davon, z. B. Golf"},
                "fuel": {"type": "string", "enum": sorted(FUELS)},
                "body": {"type": "string", "enum": sorted(BODIES)},
                "max_price": {"type": "integer", "description": "Höchstpreis in Euro"},
                "max_mileage": {"type": "integer", "description": "höchster Kilometerstand"},
                "min_year": {"type": "integer", "description": "frühestes Baujahr"},
            }, request_message="Einen Moment, ich schaue in unseren Bestand."),
        _fn("get_vehicle_details", "Details und Ausstattung zu einem Fahrzeug aus search_vehicles.",
            {"vehicle_id": {"type": "string", "description": "z. B. V004"}}, ["vehicle_id"]),
        _fn("check_availability",
            "Freie Termine für eine Leistung. Ist der Wunschtag voll oder geschlossen, kommt der nächste freie Tag.",
            {
                "service": {"type": "string", "enum": service_enum},
                "date": {"type": "string", "description": "Wunschtag im Format JJJJ-MM-TT"},
                "vehicle_id": {"type": "string", "description": "nur bei probefahrt, Pflicht"},
            }, ["service"], request_message="Einen Moment, ich schaue in den Kalender."),
        _fn("book_appointment",
            "Bucht einen Termin. Nur nach check_availability und nachdem Name und Telefonnummer bestätigt sind.",
            {
                "service": {"type": "string", "enum": service_enum},
                "start": {"type": "string", "description": "Startzeit genau wie von check_availability, JJJJ-MM-TTTHH:MM"},
                "name": {"type": "string"},
                "phone": {"type": "string", "description": "Rückrufnummer"},
                "vehicle_id": {"type": "string", "description": "nur bei probefahrt"},
                "license_plate": {"type": "string", "description": "Kennzeichen bei Werkstattterminen"},
                "notes": {"type": "string", "description": "kurze Anmerkung, z. B. Geräusch vorne links"},
            }, ["service", "start", "name", "phone"]),
        _fn("cancel_appointment", "Sagt einen Termin ab. Braucht Buchungscode und die bei der Buchung genannte Telefonnummer.",
            {"code": {"type": "string"}, "phone": {"type": "string"}}, ["code", "phone"]),
        _fn("request_callback", "Legt einen Rückrufwunsch für den Verkauf an.",
            {
                "name": {"type": "string"},
                "phone": {"type": "string"},
                "topic": {"type": "string", "description": "Worum es geht, ein Satz"},
                "preferred_time": {"type": "string", "description": "z. B. morgen Vormittag"},
                "vehicle_id": {"type": "string"},
            }, ["name", "phone", "topic"]),
        {"type": "endCall"},
    ]
    if transfer_number:
        out.append({
            "type": "transferCall",
            "destinations": [{
                "type": "number", "number": transfer_number,
                "description": "Verkauf und Werkstatt, wenn der Anrufer einen Menschen möchte oder die KI nicht weiterkommt",
                "message": "Ich verbinde Sie mit einem Kollegen. Einen Moment bitte.",
            }],
        })
    return out


def assistant(server_url: str, credential_id: str, transfer_number: str | None = None,
              voice: dict | None = None) -> dict:
    return {
        "name": NAME,
        "firstMessage": FIRST_MESSAGE,
        "firstMessageMode": "assistant-speaks-first",
        "transcriber": {"provider": "deepgram", "model": "nova-3", "language": "de",
                        "keyterm": KEYTERMS, "numerals": True},
        "model": {
            "provider": "anthropic",
            "model": "claude-haiku-5-5",
            "temperature": 0.3,
            "maxTokens": 300,
            "messages": [{"role": "system", "content": PROMPT}],
            "tools": tools(transfer_number),
        },
        "voice": voice or {"provider": "azure", "voiceId": "de-DE-KatjaNeural"},
        "server": {"url": server_url, "credentialId": credential_id, "timeoutSeconds": 10},
        "serverMessages": ["tool-calls", "end-of-call-report"],
        "endCallMessage": "Vielen Dank für Ihren Anruf beim Autohaus Kranich. Auf Wiederhören!",
        "maxDurationSeconds": 600,
        "artifactPlan": {"recordingEnabled": False},
    }
