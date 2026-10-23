"""Legt den Assistenten bei Vapi an oder aktualisiert ihn (idempotent über den Namen).

    set -a; . ./.env; set +a
    PUBLIC_URL=https://xyz.trycloudflare.com uv run python -m scripts.sync_assistant [--dry-run]

Braucht VAPI_API_KEY (Private Key), WEBHOOK_TOKEN und PUBLIC_URL. Optional
TRANSFER_NUMBER (Weiterleitung an einen Menschen, E.164).
Das Bearer-Credential für den Webhook wird ebenfalls über den Namen gefunden
oder angelegt; bei geändertem Token wird es aktualisiert.
"""
from __future__ import annotations

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


def ensure_credential(c: httpx.Client, token: str) -> str:
    plan = {"type": "bearer", "token": token, "headerName": "Authorization", "bearerPrefixEnabled": True}
    existing = [x for x in _check(c.get("/credential")) if x.get("name") == CREDENTIAL_NAME]
    if existing:
        cid = existing[0]["id"]
        _check(c.patch(f"/credential/{cid}", json={"authenticationPlan": plan}))
        return cid
    created = _check(c.post("/credential", json={"provider": "custom-credential", "name": CREDENTIAL_NAME,
                                                 "authenticationPlan": plan}))
    return created["id"]


def main() -> None:
    dry = "--dry-run" in sys.argv
    url = os.environ.get("PUBLIC_URL", "").rstrip("/")
    token = os.environ.get("WEBHOOK_TOKEN", "")
    if not url.startswith("https://"):
        sys.exit("PUBLIC_URL muss eine https-Adresse sein (Tunnel oder Server).")
    if len(token) < 24:
        sys.exit("WEBHOOK_TOKEN fehlt oder ist zu kurz.")
    transfer = os.environ.get("TRANSFER_NUMBER") or None

    if dry:
        cfg = assistant(f"{url}/vapi/webhook", "<credential-id>", transfer)
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
        return

    with _client() as c:
        cid = ensure_credential(c, token)
        cfg = assistant(f"{url}/vapi/webhook", cid, transfer)
        found = [a for a in _check(c.get("/assistant", params={"limit": 100})) if a.get("name") == NAME]
        if found:
            aid = found[0]["id"]
            _check(c.patch(f"/assistant/{aid}", json=cfg))
            print(f"aktualisiert: {NAME} ({aid})")
        else:
            aid = _check(c.post("/assistant", json=cfg))["id"]
            print(f"angelegt: {NAME} ({aid})")
        print(f"Webhook: {url}/vapi/webhook  Credential: {cid}")


if __name__ == "__main__":
    main()
