#!/usr/bin/env bash
# Startet die Demo mit einem Befehl: Server, Tunnel, Vapi-Sync, Testseite.
#
#   ./demo.sh            starten (Ctrl+C beendet Server und Tunnel wieder)
#   ./demo.sh --no-open  ohne Browser zu öffnen
#
# Die Tunnel-Adresse ändert sich bei jedem Start. Deshalb teilt das Skript sie
# Vapi über scripts/sync_assistant.py mit; ohne diesen Schritt liefen die
# Tool-Aufrufe ins Leere und der Assistent würde schweigen.
set -euo pipefail

cd "$(dirname "$0")"
PORT="${PORT:-8787}"
LOGDIR=".demo"
OPEN=1
[[ "${1:-}" == "--no-open" ]] && OPEN=0

say() { printf '\033[1m▸ %s\033[0m\n' "$*"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

command -v uv >/dev/null || fail "uv fehlt (https://docs.astral.sh/uv/)."
command -v cloudflared >/dev/null || fail "cloudflared fehlt (brew install cloudflared)."
[[ -f .env ]] || fail ".env fehlt. Vorlage: cp .env.example .env"
set -a; . ./.env; set +a
for v in VAPI_API_KEY VAPI_PUBLIC_KEY VAPI_ASSISTANT_ID WEBHOOK_TOKEN; do
  [[ -n "${!v:-}" && "${!v}" != __SET_ME__* ]] || fail "$v ist in .env nicht gesetzt."
done
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  fail "Port $PORT ist belegt (läuft die Demo schon?). Anderen Port: PORT=8788 ./demo.sh"
fi

mkdir -p "$LOGDIR"
SERVER_PID="" TUNNEL_PID=""
cleanup() {
  trap - EXIT INT TERM
  echo
  say "Beende Server und Tunnel"
  [[ -n "$TUNNEL_PID" ]] && kill "$TUNNEL_PID" 2>/dev/null || true
  [[ -n "$SERVER_PID" ]] && kill "$SERVER_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

say "Starte Server auf Port $PORT"
uv run uvicorn app.main:create_app --factory --port "$PORT" >"$LOGDIR/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 30); do
  curl -sf -m 2 "http://localhost:$PORT/health" >/dev/null && break
  kill -0 "$SERVER_PID" 2>/dev/null || fail "Server ist abgestürzt, siehe $LOGDIR/server.log"
  sleep 1
done
curl -sf -m 2 "http://localhost:$PORT/health" >/dev/null || fail "Server antwortet nicht, siehe $LOGDIR/server.log"

say "Öffne Tunnel"
cloudflared tunnel --no-autoupdate --url "http://localhost:$PORT" >"$LOGDIR/tunnel.log" 2>&1 &
TUNNEL_PID=$!
URL=""
for _ in $(seq 1 45); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOGDIR/tunnel.log" | head -1 || true)
  [[ -n "$URL" ]] && break
  sleep 1
done
[[ -n "$URL" ]] || fail "Keine Tunnel-Adresse erhalten, siehe $LOGDIR/tunnel.log"

# Erreichbarkeit über einen öffentlichen Resolver prüfen: der lokale DNS (z. B.
# Pi-hole) merkt sich frische Namen anfangs als nicht existent.
say "Warte, bis $URL öffentlich erreichbar ist"
HOST="${URL#https://}"
OK=0
for _ in $(seq 1 45); do
  IP=$(dig +short @1.1.1.1 "$HOST" 2>/dev/null | head -1 || true)
  if [[ -n "$IP" ]] && curl -sf -m 4 --resolve "$HOST:443:$IP" "$URL/health" >/dev/null; then OK=1; break; fi
  sleep 2
done
[[ "$OK" == 1 ]] || fail "Tunnel ist von außen nicht erreichbar, siehe $LOGDIR/tunnel.log"

say "Teile Vapi die neue Adresse mit"
PUBLIC_URL="$URL" uv run python -m scripts.sync_assistant

DEMO="http://localhost:$PORT/demo"
say "Bereit: $DEMO  (Ctrl+C beendet alles)"
[[ "$OPEN" == 1 ]] && command -v open >/dev/null && open "$DEMO"

wait "$SERVER_PID"
