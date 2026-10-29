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

# Vapi formatiert Zahlen, Uhrzeiten und Beträge nach englischen Regeln (aus "1200"
# wird "twelve hundred", ab 2025 Einzelziffern). Die deutsche Stimme liest das
# selbst richtig, darum nur die sprachneutralen Formatierer.
FORMATTERS = ["markdown", "asterisk", "stripAsterisk", "quote", "newline", "dash"]
REPLACEMENTS = [("Seat", "Se-at"), ("ID.3", "I D 3"), ("HU/AU", "H U und A U"), ("TSI", "T S I"),
                ("TDI", "T D I"), ("TFSI", "T F S I"), ("kWh", "Kilowattstunden")]

# Ein Anrufbericht, der eine Zusage behauptet, die nie im System angekommen ist, ist
# schlimmer als keiner: das Team verlässt sich darauf. Darum gilt nur, was ein Werkzeug
# bestätigt hat (Testanruf 29.10.: "Rückruf vereinbart", obwohl keiner angelegt war).
SUMMARY_PROMPT = ("Du fasst Telefonate eines Autohauses für das Team zusammen. Schreibe auf Deutsch zwei bis "
                  "drei Sätze im Klartext ohne Markdown: Anliegen, Ergebnis und was das Team noch tun muss. "
                  "Als erledigt gilt nur, was ein Werkzeug bestätigt hat: ein Termin mit Buchungscode, ein "
                  "angelegter Rückruf, eine bestätigte Absage. Hat die KI etwas nur angeboten oder endete das "
                  "Gespräch vorher, schreibe ausdrücklich, dass es offen ist und keine Daten aufgenommen wurden. "
                  "Keine Telefonnummern. Nur die Zusammenfassung.")

# Fachvokabular für die Spracherkennung: Marken und Modelle aus dem Bestand,
# Leistungen und Begriffe, die am Telefon fallen.
KEYTERMS = ["Autohaus Kranich", "Probefahrt", "Inspektion", "Ölwechsel", "Reifenwechsel", "HU", "AU",
            "Hauptuntersuchung", "Klimaservice", "Kennzeichen", "Buchungscode", "Kombi", "SUV",
            "Kleinwagen", "Hybrid", "Plug-in-Hybrid", "Elektro", "Automatik", "Schaltgetriebe",
            "Volkswagen", "VW", "Skoda", "Seat", "Audi", "BMW", "Mercedes", "Opel", "Ford", "Renault",
            "Toyota", "Hyundai", "Tesla", "Golf", "Passat", "Octavia", "Model 3", "Kona", "Corolla",
            "Corsa", "Kuga", "Clio", "Ibiza", "ID.3", "Q3", "A-Klasse", "3er Touring",
            "Finanzierung", "Inzahlungnahme"]


def transcriber(preset: str = "soniox") -> dict:
    """Spracherkennung. Voreinstellungen zum Vergleichen, Standard Soniox.

    Vapis Standardprofil ist auf schnelles Turn-Taking getrimmt und beendet den
    Redebeitrag schon bei kurzen Pausen; Anrufer, die eine Telefonnummer in
    Gruppen diktieren, werden dabei abgeschnitten. Darum hier geduldiger.
    """
    deepgram = {"provider": "deepgram", "model": "nova-3", "language": "de", "keyterm": KEYTERMS,
                "endpointing": 300}
    presets = {
        "soniox": {
            "provider": "soniox", "model": "stt-rt-v5", "language": "de", "languageHintsStrict": True,
            "customVocabulary": KEYTERMS,
            "contextGeneral": [
                {"key": "domain", "value": "Autohaus, Gebrauchtwagenverkauf und Werkstatt"},
                {"key": "topic", "value": "Anrufer fragen nach Fahrzeugen, Probefahrten und Werkstattterminen"},
            ],
            "endpointSensitivity": -0.2,
            "endpointLatencyAdjustmentLevel": 1,
            "maxEndpointDelayMs": 1200,
            "fallbackPlan": {"transcribers": [deepgram]},
        },
        "deepgram": deepgram,
        "azure": {"provider": "azure", "language": "de-DE", "segmentationStrategy": "Semantic",
                  "segmentationSilenceTimeoutMs": 800},
    }
    if preset not in presets:
        raise ValueError(f"Unbekannte Spracherkennung '{preset}', möglich: {', '.join(presets)}")
    return presets[preset]


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
            {"vehicle_id": {"type": "string", "description": "z. B. V004"}}, ["vehicle_id"],
            request_message="Einen Moment, ich hole die Details."),
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
            }, ["service", "start", "name", "phone"], request_message="Ich trage den Termin ein."),
        _fn("cancel_appointment", "Sagt einen Termin ab. Braucht Buchungscode und die bei der Buchung genannte Telefonnummer.",
            {"code": {"type": "string"}, "phone": {"type": "string"}}, ["code", "phone"],
            request_message="Einen Moment, ich suche den Termin."),
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
              voice: dict | None = None, stt: str = "soniox") -> dict:
    return {
        "name": NAME,
        "firstMessage": FIRST_MESSAGE,
        "firstMessageMode": "assistant-speaks-first",
        "transcriber": transcriber(stt),
        "model": {
            "provider": "anthropic",
            "model": "claude-haiku-5-5",
            "temperature": 0.3,
            "maxTokens": 300,
            "messages": [{"role": "system", "content": PROMPT}],
            "tools": tools(transfer_number),
        },
        "voice": {**(voice or {"provider": "azure", "voiceId": "de-DE-KatjaNeural"}),
                  "chunkPlan": {"formatPlan": {
                      "formattersEnabled": FORMATTERS,
                      "replacements": [{"type": "exact", "key": k, "value": v, "replaceAllEnabled": True}
                                       for k, v in REPLACEMENTS]}}},
        "analysisPlan": {"summaryPlan": {"enabled": True, "messages": [
            {"role": "system", "content": SUMMARY_PROMPT},
            {"role": "user", "content": "Gespräch:\n\n{{transcript}}\n\nEnde-Grund: {{endedReason}}"}]}},
        "server": {"url": server_url, "credentialId": credential_id, "timeoutSeconds": 10},
        "serverMessages": ["tool-calls", "end-of-call-report"],
        "endCallMessage": "Vielen Dank für Ihren Anruf beim Autohaus Kranich. Auf Wiederhören!",
        "maxDurationSeconds": 600,
        "artifactPlan": {"recordingEnabled": False},
    }
