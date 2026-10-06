#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x .venv-app/bin/python ]]; then
  project_python=""
  for candidate in python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      project_python="$candidate"
      break
    fi
  done
  if [[ -z "$project_python" ]]; then
    echo "Install Python 3.12+ first. See README.md."
    exit 1
  fi
  if command -v uv >/dev/null 2>&1; then
    uv venv --python "$project_python" .venv-app
  else
    "$project_python" -m venv .venv-app
  fi
fi

if ! .venv-app/bin/python -c 'import flask, httpx, pypdf, dotenv, gunicorn' >/dev/null 2>&1; then
  if command -v uv >/dev/null 2>&1; then
    uv pip install --link-mode=copy --python .venv-app/bin/python -r requirements.lock
  else
    .venv-app/bin/python -m pip install -r requirements.lock
  fi
fi
if [[ ! -f .env ]]; then
  cp .env.example .env
fi
echo "Folio is starting. Open http://127.0.0.1:8000 (or the HOST/PORT in .env)."
exec .venv-app/bin/python app.py
