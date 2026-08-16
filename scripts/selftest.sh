#!/usr/bin/env bash
# POSIX wrapper. The portable runner is scripts/selftest.py.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  exec "${ROOT}/.venv/bin/python" "${ROOT}/scripts/selftest.py" "$@"
fi
exec python3 "${ROOT}/scripts/selftest.py" "$@"
