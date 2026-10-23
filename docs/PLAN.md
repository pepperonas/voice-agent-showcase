# Plan: KI-Telefonassistent für ein Autohaus

Probeprojekt zur Vorbereitung auf eine Voice-Agent-Testaufgabe. Fiktiver Betrieb: **Autohaus Kranich, Berlin** (Verkauf und Werkstatt).

## Entscheidungen

- Plattform: **Vapi** (Spracherkennung, LLM, Sprachausgabe, Telefonie). Eigene Logik nur im Tool-Server.
- Erreichbarkeit: zuerst Web-Call, Telefonnummer erst nach ausdrücklichem OK.
- Tool-Server: **Python, FastAPI, SQLite** (Standardbibliothek `sqlite3`), Tests mit pytest.
- Assistent als Code: Prompt, Stimme, Modelle und Tools liegen versioniert im Repo und werden per Skript über die Vapi-API abgeglichen.

## Was der Agent kann

| Tool | Zweck |
|---|---|
| `search_vehicles` | Bestand durchsuchen (Marke, Modell, Preis, Kraftstoff, Karosserie, Kilometer, Baujahr) |
| `get_vehicle_details` | Details zu einem Fahrzeug |
| `check_availability` | freie Termine für Probefahrt, Verkaufsberatung oder Werkstattleistung |
| `book_appointment` | Termin buchen, liefert einen Buchungscode |
| `cancel_appointment` | Termin mit Buchungscode und Telefonnummer absagen |
| `request_callback` | Rückrufwunsch für den Verkauf anlegen |
| `transferCall` | Weiterleitung an einen Menschen (Vapi-intern, kein Server nötig) |

## Fachregeln

- Verkauf (Probefahrt 45 min, Beratung 30 min): Mo bis Fr 9 bis 18 Uhr, Sa 10 bis 14 Uhr. Zwei Berater parallel. Ein Fahrzeug kann nicht gleichzeitig zweimal probegefahren werden.
- Werkstatt: Mo bis Fr 8 bis 17 Uhr, zwei Hebebühnen. Leistungen: Inspektion 120 min, Ölwechsel 60, Reifenwechsel 30, HU/AU 60, Klimaservice 60.
- Raster 30 Minuten, frühester Termin eine Stunde ab jetzt, Suche höchstens 14 Tage voraus. Sonntage geschlossen. Feiertage sind bewusst noch nicht abgebildet.
- Wiederholte Tool-Aufrufe (gleiche `toolCallId`) buchen nicht doppelt.

## Datenschutz

- Der Agent sagt zu Beginn, dass er ein KI-Assistent ist.
- Keine Aufzeichnung als Standard. Gespeichert werden nur Name, Telefonnummer und Terminangaben.
- Anrufberichte (Zusammenfassung, Dauer, Ende-Grund, Kosten) werden nach `REPORT_RETENTION_DAYS` (Standard 30) gelöscht. Transkripte nur, wenn `STORE_TRANSCRIPTS=true`.
- Webhook nur mit Bearer-Token (Vapi Custom Credential), Vergleich in konstanter Zeit.

## Schritte

1. Server und Datenbank, getestet ohne Vapi. **(läuft)**
2. Assistenten-Konfiguration (`assistant/`) und Sync-Skript über die Vapi-API.
3. Web-Call über Tunnel (cloudflared oder ngrok), End-to-End-Test.
4. Übersichtsseite für Termine, Rückrufe und Anrufberichte.
5. Telefonnummer, nur nach OK.
