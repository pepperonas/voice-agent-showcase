# voice-agent

KI-Telefonassistent für ein fiktives Autohaus (**Autohaus Kranich, Berlin**). Sprache, Telefonie und LLM laufen über [Vapi](https://vapi.ai). Die Fachlogik liegt in einem eigenen FastAPI-Server, den Vapi per Webhook aufruft.

Der Assistent sucht im Fahrzeugbestand, vergibt Termine für Probefahrten, Verkaufsberatung und Werkstatt, sagt Termine ab, legt Rückrufwünsche an und kann an einen Menschen übergeben. Plan und Fachregeln: [docs/PLAN.md](docs/PLAN.md).

## Stand

- [x] Schritt 1: Tool-Server mit SQLite und Tests (ohne Vapi)
- [ ] Schritt 2: Assistent als Code und Sync über die Vapi-API
- [ ] Schritt 3: Web-Call über Tunnel
- [ ] Schritt 4: Übersichtsseite
- [ ] Schritt 5: Telefonnummer (nur nach OK)

## Lokal starten

```bash
cp .env.example .env            # Werte eintragen, WEBHOOK_TOKEN: openssl rand -hex 24
uv sync
set -a; . ./.env; set +a
uv run uvicorn app.main:create_app --factory --port 8787
uv run pytest -q
```

## Webhook

`POST /vapi/webhook` mit `Authorization: Bearer <WEBHOOK_TOKEN>` (in Vapi als Custom Credential vom Typ Bearer Token hinterlegen). Verarbeitet werden `tool-calls` (Antwort immer HTTP 200 mit `results[{toolCallId, result|error}]`) und `end-of-call-report`. Alle anderen Nachrichten werden quittiert.

| Tool | Argumente |
|---|---|
| `search_vehicles` | `make`, `model`, `fuel` (benzin, diesel, elektro, hybrid), `body` (kleinwagen, kompakt, kombi, limousine, suv), `max_price`, `max_mileage`, `min_year` |
| `get_vehicle_details` | `vehicle_id` |
| `check_availability` | `service`, `date` (JJJJ-MM-TT), bei Probefahrt `vehicle_id` |
| `book_appointment` | `service`, `start`, `name`, `phone`, optional `vehicle_id`, `license_plate`, `notes` |
| `cancel_appointment` | `code`, `phone` |
| `request_callback` | `name`, `phone`, `topic`, optional `preferred_time`, `vehicle_id` |

Leistungen: `probefahrt`, `beratung`, `inspektion`, `oelwechsel`, `reifenwechsel`, `hu_au`, `klimaservice`.

## Datenschutz

Keine Aufzeichnung als Standard, Transkripte nur mit `STORE_TRANSCRIPTS=true`, Anrufberichte werden nach `REPORT_RETENTION_DAYS` gelöscht. Bestand und Betrieb sind fiktiv.
