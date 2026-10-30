#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
COMMAND="${1:-start}"
REQUESTED_PORT="${PORT:-}"
if [[ $# -gt 0 ]]; then shift; fi

BOOTSTRAP_PYTHON="${PYTHON:-}"
if [[ -z "$BOOTSTRAP_PYTHON" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    BOOTSTRAP_PYTHON="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    BOOTSTRAP_PYTHON="$(command -v python)"
  else
    printf '%s\n' "Python 3.9 or 3.12 is required. Install Python, then rerun ./run.sh." >&2
    exit 1
  fi
fi

PY_MINOR="$("$BOOTSTRAP_PYTHON" -c 'import sys; print(sys.version_info.minor if sys.version_info.major == 3 else 0)')"
if [[ "$PY_MINOR" != "9" && "$PY_MINOR" != "12" ]]; then
  printf '%s\n' "Use Python 3.9 or 3.12; found $($BOOTSTRAP_PYTHON --version 2>&1)." >&2
  exit 1
fi

VENV="$ROOT/.venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$BOOTSTRAP_PYTHON" -m venv --system-site-packages "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
PYTHON="$VENV/bin/python"

REQ_HASH="$("$PYTHON" -c 'import hashlib,pathlib; print(hashlib.sha256(pathlib.Path("requirements.txt").read_bytes()).hexdigest())')"
REQ_MARKER="$VENV/.requirements.sha256"
if [[ ! -f "$REQ_MARKER" || "$(cat "$REQ_MARKER")" != "$REQ_HASH" ]]; then
  PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1 "$PYTHON" -m pip install -r requirements.txt
  printf '%s\n' "$REQ_HASH" > "$REQ_MARKER"
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

load_env() {
  eval "$(PYTHONPATH="$ROOT" "$PYTHON" -c 'import shlex; from pathlib import Path; from tools.env_file import read_env; print("\n".join("export %s=%s" % (key, shlex.quote(value)) for key, value in read_env(Path(".env")).items()))')"
  if [[ -n "$REQUESTED_PORT" ]]; then PORT="$REQUESTED_PORT"; export PORT; fi
}

set_env_value() {
  local key="$1"
  local value="$2"
  CRASHCART_ENV_KEY="$key" CRASHCART_ENV_VALUE="$value" PYTHONPATH="$ROOT" "$PYTHON" -c 'import os; from pathlib import Path; from tools.env_file import update_env; update_env(Path(".env"), {os.environ["CRASHCART_ENV_KEY"]: os.environ["CRASHCART_ENV_VALUE"]})'
}

env_has_key() {
  PYTHONPATH="$ROOT" "$PYTHON" -c 'import sys; from pathlib import Path; from tools.env_file import read_env; values=read_env(Path(".env")); sys.exit(0 if (values.get("LLM_API_KEY") or values.get("NEBIUS_API_KEY")) else 1)'
}

ensure_key_and_gate0() {
  load_env
  if ! env_has_key; then
    local api_key
    printf '%s' 'Paste your Nebius Token Factory API key: '
    IFS= read -r -s api_key
    printf '\n'
    if [[ -z "$api_key" ]]; then
      printf '%s\n' "No key entered. Paste it into .env as LLM_API_KEY, then rerun ./run.sh." >&2
      exit 1
    fi
    CRASHCART_CAPTURED_KEY="$api_key" PYTHONPATH="$ROOT" "$PYTHON" -c 'import os; from pathlib import Path; from tools.env_file import update_env; update_env(Path(".env"), {"LLM_API_KEY": os.environ["CRASHCART_CAPTURED_KEY"]})'
    unset api_key CRASHCART_CAPTURED_KEY
  fi
  set_env_value LLM_MODE live
  load_env
  export LLM_MODE=live
  if ! "$PYTHON" -c 'import sys,time; from pathlib import Path; p=Path("gate0_report.json"); sys.exit(0 if p.exists() and time.time()-p.stat().st_mtime <= 86400 else 1)'; then
    "$PYTHON" tools/smoke_test.py
    "$PYTHON" tools/apply_gate0.py
  fi
  load_env
  export LLM_MODE=live
}

ensure_incidents() {
  if [[ ! -d incidents ]]; then
    "$PYTHON" tools/make_incident.py --all --seed 7 --out incidents
  fi
}

prepare() {
  ensure_incidents
}

start_server() {
  local port="${PORT:-8080}"
  local url="http://127.0.0.1:${port}"
  printf 'CrashCart mode: %s\n' "${LLM_MODE:-live}"
  printf 'CrashCart is starting at %s\n' "$url"
  if [[ "${CRASHCART_NO_BROWSER:-0}" != "1" ]]; then
    if command -v open >/dev/null 2>&1; then
      open "$url" >/dev/null 2>&1 &
    elif command -v xdg-open >/dev/null 2>&1; then
      xdg-open "$url" >/dev/null 2>&1 &
    fi
  fi
  exec "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$port"
}

prepare
case "$COMMAND" in
  start)
    ensure_key_and_gate0
    start_server
    ;;
  mock)
    load_env
    export LLM_MODE=mock
    start_server
    ;;
  eval)
    QUICK=0
    YES=0
    for option in "$@"; do
      case "$option" in
        --quick) QUICK=1 ;;
        --yes) YES=1 ;;
        *) printf 'Unknown eval option: %s\n' "$option" >&2; exit 2 ;;
      esac
    done
    ensure_key_and_gate0
    if [[ "$QUICK" == "1" ]]; then
      INCIDENTS="xid79_demo,thermal_throttle,power_cap"
      "$PYTHON" tools/estimate_eval_cost.py --incidents "$INCIDENTS"
    else
      INCIDENTS=all
      "$PYTHON" tools/estimate_eval_cost.py --incidents all
    fi
    if [[ "$YES" != "1" ]]; then
      read -r -p 'Start the live evaluation? [y/N] ' ANSWER
      case "$ANSWER" in y|Y|yes|YES) ;; *) printf '%s\n' 'Evaluation cancelled.'; exit 0 ;; esac
    fi
    "$PYTHON" tools/run_eval.py --incidents "$INCIDENTS" --systems crashcart,tail,no_eyes --out eval/results.json
    "$PYTHON" tools/make_report.py
    ;;
  replays)
    ensure_key_and_gate0
    PORT="${PORT:-8080}"
    URL="http://127.0.0.1:${PORT}"
    "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" > /dev/null 2>&1 &
    SERVER_PID=$!
    trap 'kill "$SERVER_PID" >/dev/null 2>&1 || true; wait "$SERVER_PID" 2>/dev/null || true' EXIT INT TERM
    READY=0
    for attempt in $(seq 1 60); do
      if curl -fsS "$URL/healthz" >/dev/null 2>&1; then READY=1; break; fi
      sleep 1
    done
    if [[ "$READY" != "1" ]]; then
      printf '%s\n' "Could not start the local API at $URL." >&2
      exit 1
    fi
    for INCIDENT in xid79_demo thermal_throttle ib_flap; do
      RESPONSE="$(curl -fsS -X POST "$URL/api/run" -H 'Content-Type: application/json' -d "{\"incident_id\":\"$INCIDENT\",\"use_eyes\":\"${USE_EYES:-true}\"}")"
      RUN_ID="$(printf '%s' "$RESPONSE" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')"
      REPORT=''
      FAILED=0
      for attempt in $(seq 1 1800); do
        if REPORT="$(curl -fsS "$URL/api/report/$RUN_ID" 2>/dev/null)"; then
          FAILED="$(printf '%s' "$REPORT" | "$PYTHON" -c 'import json,sys; print(1 if json.load(sys.stdin).get("error") else 0)')"
          break
        fi
        sleep 1
      done
      if [[ -z "$REPORT" ]]; then
        printf 'Run failed for %s; inspect the API output and retry.\n' "$INCIDENT" >&2
        exit 1
      fi
      if [[ "$FAILED" == "1" ]]; then
        printf '%s\n' "$(printf '%s' "$REPORT" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin).get("error", "Live run failed."))')" >&2
        exit 1
      fi
      COST="$(printf '%s' "$REPORT" | "$PYTHON" -c 'import json,sys; print("%.6f" % json.load(sys.stdin).get("cost_usd", 0))')"
      mkdir -p samples/runs
      cp "${RUNS_DIR:-./runs}/$RUN_ID/events.jsonl" "samples/runs/$INCIDENT.jsonl"
      printf 'Recorded %s (cost $%s)\n' "$INCIDENT" "$COST"
    done
    ;;
  docker)
    ensure_key_and_gate0
    docker build --platform linux/amd64 -t crashcart:latest .
    docker run --rm --env-file .env -p 8080:8080 -e PORT=8080 crashcart:latest
    printf '\nPush and deploy commands (not run):\n'
    printf '%s\n' 'docker tag crashcart:latest ghcr.io/<your-user>/crashcart:v1'
    printf '%s\n' 'docker push ghcr.io/<your-user>/crashcart:v1'
    printf '%s\n' 'nebius ai endpoint create --name crashcart --image ghcr.io/<your-user>/crashcart:v1 --container-port 8080 --public'
    printf '%s\n' 'Check `nebius ai endpoint create --help` for the secret/environment flag before deployment.'
    ;;
  *)
    printf 'Usage: ./run.sh [start|mock|eval [--quick] [--yes]|replays|docker]\n' >&2
    exit 2
    ;;
esac
