"""Übersetzung zwischen Vapi-Server-Nachrichten und den Tool-Handlern.

Format laut docs.vapi.ai/tools/custom-tools: Anfrage `message.type == "tool-calls"`
mit `toolCallList[{id, function: {name, arguments}}]`; Antwort immer HTTP 200 mit
`{"results": [{"toolCallId", "result" | "error"}]}`, Ergebnis als String.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from app.tools import HANDLERS, Ctx, ToolError

log = logging.getLogger("voice-agent")


def _arguments(fn: dict) -> dict:
    args = fn.get("arguments") or {}
    if isinstance(args, str):  # manche Modelle liefern die Argumente als JSON-String
        try:
            args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            return {"__invalid__": True}
    return args if isinstance(args, dict) else {"__invalid__": True}


def handle_tool_calls(conn: sqlite3.Connection, message: dict, now: datetime) -> dict:
    results = []
    call_id = str((message.get("call") or {}).get("id") or "-")
    for call in message.get("toolCallList") or []:
        tc_id = call.get("id", "")
        fn = call.get("function") or {}
        name = fn.get("name", "")
        args = _arguments(fn)
        handler = HANDLERS.get(name)
        try:
            if handler is None:
                raise ToolError(f"Unbekanntes Werkzeug '{name}'.")
            if args.get("__invalid__"):
                raise ToolError("Die Argumente waren kein gültiges JSON.")
            out = handler(conn, args, Ctx(now=now, tool_call_id=tc_id or None, call_id=call_id))
            results.append({"toolCallId": tc_id,
                            "result": json.dumps({k: v for k, v in out.items() if v is not None},
                                                 ensure_ascii=False)})
        except ToolError as e:
            results.append({"toolCallId": tc_id, "error": str(e)})
        except Exception:
            log.exception("Tool %s fehlgeschlagen", name)
            results.append({"toolCallId": tc_id,
                            "error": "Technischer Fehler. Bitte Rückruf anbieten."})
    return {"results": results}


def store_report(conn: sqlite3.Connection, message: dict, store_transcript: bool,
                 retention_days: int) -> None:
    call = message.get("call") or {}
    call_id = call.get("id")
    if not call_id:
        return
    analysis = message.get("analysis") or {}
    artifact = message.get("artifact") or {}
    transcript = (artifact.get("transcript") or message.get("transcript")) if store_transcript else None
    conn.execute(
        "INSERT OR REPLACE INTO call_reports (call_id, ended_reason, duration_seconds, cost_usd,"
        " summary, transcript) VALUES (?,?,?,?,?,?)",
        (call_id, message.get("endedReason"), message.get("durationSeconds"), message.get("cost"),
         analysis.get("summary") or message.get("summary"), transcript))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("DELETE FROM call_reports WHERE created_at < ?", (cutoff,))
