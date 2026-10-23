"""FastAPI-Server: Webhook für Vapi."""
from __future__ import annotations

import hmac
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request

from app import db
from app.vapi import handle_tool_calls, store_report

TZ = ZoneInfo("Europe/Berlin")


def create_app(db_path: str | None = None, token: str | None = None,
               clock=lambda: datetime.now(TZ).replace(tzinfo=None)) -> FastAPI:
    db_path = db_path or os.environ.get("DB_PATH", "voice-agent.db")
    token = token if token is not None else os.environ.get("WEBHOOK_TOKEN", "")
    if len(token) < 24:
        raise RuntimeError("WEBHOOK_TOKEN fehlt oder ist kürzer als 24 Zeichen.")
    store_transcripts = os.environ.get("STORE_TRANSCRIPTS", "false").lower() == "true"
    retention = int(os.environ.get("REPORT_RETENTION_DAYS", "30"))

    init_conn = db.connect(db_path)
    db.init(init_conn)
    init_conn.close()

    app = FastAPI(title="Autohaus Kranich Telefonassistent", docs_url=None, redoc_url=None)
    expected = f"Bearer {token}".encode()

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/vapi/webhook")
    async def webhook(request: Request):
        given = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(given, expected):
            raise HTTPException(status_code=401)
        body = await request.json()
        message = body.get("message") or {}
        kind = message.get("type")
        conn = db.connect(db_path)
        try:
            if kind == "tool-calls":
                return handle_tool_calls(conn, message, clock())
            if kind == "end-of-call-report":
                store_report(conn, message, store_transcripts, retention)
            return {}
        finally:
            conn.close()

    return app


logging.basicConfig(level=logging.INFO)
# Start: uv run uvicorn app.main:create_app --factory --port 8787
