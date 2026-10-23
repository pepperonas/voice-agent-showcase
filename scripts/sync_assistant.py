"""Legt den Assistenten bei Vapi an oder aktualisiert ihn (idempotent über den Namen).

    set -a; . ./.env; set +a
    PUBLIC_URL=https://xyz.trycloudflare.com uv run python -m scripts.sync_assistant [--dry-run]

Braucht VAPI_API_KEY (Private Key), WEBHOOK_TOKEN und PUBLIC_URL. Optional
TRANSFER_NUMBER (Weiterleitung an einen Menschen, E.164).
Das Bearer-Credential für den Webhook trägt einen Fingerabdruck des Tokens im
Namen; bei geändertem Token entsteht ein neues, alte werden danach entfernt.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

import httpx

from assistant.config import NAME, assistant

API = "https://api.vapi.ai"
CREDENTIAL_NAME = "voice-agent-webhook"


def _client() -> httpx.Client:
    key = os.environ.get("VAPI_API_KEY", "")
    if not key:
        sys.exit("VAPI_API_KEY fehlt.")
    return httpx.Client(base_url=API, headers={"Authorization": f"Bearer {key}"}, timeout=30)


def _check(r: httpx.Response) -> dict | list:
    if r.status_code >= 400:
        sys.exit(f"Vapi {r.request.method} {r.request.url.path}: {r.status_code} {r.text[:500]}")
    return r.json()


def credential_name(token: str) -> str:
    # Fingerabdruck statt Token im Namen: ein geänderter Token ergibt ein neues Credential.
    return f"{CREDENTIAL_NAME}-{hashlib.sha256(token.encode()).hexdigest()[:10]}"


def ensure_credential(c: httpx.Client, token: str) -> tuple[str, list[str]]:
    """Liefert die Credential-ID und die IDs veralteter Credentials (zum Aufräumen).

    Ein bestehendes Credential wird nie per PATCH geändert: nach einem PATCH lehnte
    der Webhook die Anfragen von Vapi mit 401 ab (2026-10-23, zweiter Testanruf).
    """
    name = credential_name(token)
    mine = [x for x in _check(c.get("/credential")) if str(x.get("name", "")).startswith(CREDENTIAL_NAME)]
    current = [x["id"] for x in mine if x["name"] == name]
    stale = [x["id"] for x in mine if x["name"] != name]
    if current:
        return current[0], stale
    plan = {"type": "bearer", "token": token, "headerName": "Authorization", "bearerPrefixEnabled": True}
    created = _check(c.post("/credential", json={"provider": "custom-credential", "name": name,
                                                 "authenticationPlan": plan}))
    return created["id"], stale


def main() -> None:
    dry = "--dry-run" in sys.argv
    url = os.environ.get("PUBLIC_URL", "").rstrip("/")
    token = os.environ.get("WEBHOOK_TOKEN", "")
    if not url.startswith("https://"):
        sys.exit("PUBLIC_URL muss eine https-Adresse sein (Tunnel oder Server).")
    if len(token) < 24:
        sys.exit("WEBHOOK_TOKEN fehlt oder ist zu kurz.")
    transfer = os.environ.get("TRANSFER_NUMBER") or None
    stt = os.environ.get("TRANSCRIBER", "soniox")

    if dry:
        cfg = assistant(f"{url}/vapi/webhook", "<credential-id>", transfer, stt=stt)
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
        return

    with _client() as c:
        cid, stale = ensure_credential(c, token)
        cfg = assistant(f"{url}/vapi/webhook", cid, transfer, stt=stt)
        found = [a for a in _check(c.get("/assistant", params={"limit": 100})) if a.get("name") == NAME]
        if found:
            aid = found[0]["id"]
            _check(c.patch(f"/assistant/{aid}", json=cfg))
            print(f"aktualisiert: {NAME} ({aid})")
        else:
            aid = _check(c.post("/assistant", json=cfg))["id"]
            print(f"angelegt: {NAME} ({aid})")
        # Erst nachdem der Assistent auf das neue Credential zeigt, alte entfernen.
        for old_id in stale:
            r = c.delete(f"/credential/{old_id}")
            print(f"altes Credential {old_id} entfernt" if r.status_code < 400
                  else f"altes Credential {old_id} nicht entfernt: {r.status_code} {r.text[:200]}")
        print(f"Webhook: {url}/vapi/webhook  Credential: {cid}  Spracherkennung: {stt}")


if __name__ == "__main__":
    main()
