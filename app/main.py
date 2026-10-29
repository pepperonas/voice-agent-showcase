"""FastAPI-Server: Webhook für Vapi."""
from __future__ import annotations

import hmac
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from app import db
from app.vapi import handle_tool_calls, store_report

TZ = ZoneInfo("Europe/Berlin")
log = logging.getLogger("voice-agent")


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

    demo_html = (Path(__file__).parent / "demo.html").read_text(encoding="utf-8")

    @app.get("/demo", response_class=HTMLResponse)
    def demo(request: Request):
        # Nur lokal: über den Tunnel könnte sonst jeder Anrufe auf eigene Kosten starten.
        # Der Tunnel kommt ebenfalls von 127.0.0.1, verrät sich aber über Host und CF-Header.
        host = request.headers.get("host", "").split(":")[0]
        if (request.headers.get("cf-connecting-ip") or host not in ("localhost", "127.0.0.1")
                or (request.client and request.client.host not in ("127.0.0.1", "::1"))):
            raise HTTPException(status_code=404)
        public_key = os.environ.get("VAPI_PUBLIC_KEY", "")
        assistant_id = os.environ.get("VAPI_ASSISTANT_ID", "")
        if not public_key or not assistant_id:
            return HTMLResponse("VAPI_PUBLIC_KEY und VAPI_ASSISTANT_ID in .env setzen.", status_code=503)
        page = demo_html.replace("__PUBLIC_KEY__", public_key).replace("__ASSISTANT_ID__", assistant_id)
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    @app.post("/vapi/webhook")
    async def webhook(request: Request):
        given = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(given, expected):
            # Grund protokollieren, nie den Wert: hilft bei falsch eingerichteten Credentials.
            reason = ("kein Authorization-Header" if not given else
                      "ohne Bearer-Präfix" if not given.startswith(b"Bearer ") else
                      f"falscher Token (Länge {len(given) - 7}, erwartet {len(token)})")
            log.warning("Webhook abgelehnt: %s", reason)
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
