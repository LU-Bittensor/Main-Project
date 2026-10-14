#!/usr/bin/env bash
# Load Supabase (+ optional VITE_*) from health-locker-guard/.env into this shell.
#
#   cd Main-Project/key-generation
#   source ./load_supabase_env.sh
#   python grant_api.py
#
# Or one shot:
#   source ./load_supabase_env.sh && python grant_api.py

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "Source this script instead of executing it:" >&2
  echo "  source ${0}" >&2
  exit 1
fi

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HLG_ENV="${MEDILINK_ENV_FILE:-${_SCRIPT_DIR}/../../health-locker-guard/load_env.sh}"

if [[ ! -f "${_HLG_ENV}" ]]; then
  echo "Missing ${_HLG_ENV}" >&2
  echo "Clone/open health-locker-guard next to Main-Project, or set MEDILINK_ENV_FILE." >&2
  unset _SCRIPT_DIR _HLG_ENV
  return 1 2>/dev/null || exit 1
fi

# shellcheck disable=SC1090
source "${_HLG_ENV}"

if [[ -z "${GRANT_MASTER_KEY:-}" ]]; then
  export GRANT_DATA_DIR="${GRANT_DATA_DIR:-${_SCRIPT_DIR}/.grant-pairs-local}"
  mkdir -p "${GRANT_DATA_DIR}"
  if [[ ! -f "${GRANT_DATA_DIR}/master.key" ]]; then
    (umask 077; openssl rand -out "${GRANT_DATA_DIR}/master.key" 32)
    echo "Created ${GRANT_DATA_DIR}/master.key"
  fi
  export GRANT_MASTER_KEY="$(xxd -p -c 256 "${GRANT_DATA_DIR}/master.key")"
  echo "GRANT_MASTER_KEY loaded from ${GRANT_DATA_DIR}/master.key"
fi

if [[ -z "${SUPABASE_URL:-}" || -z "${SUPABASE_PUBLISHABLE_KEY:-}" ]]; then
  echo "SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY still unset after loading env." >&2
  unset _SCRIPT_DIR _HLG_ENV
  return 1 2>/dev/null || exit 1
fi

unset _SCRIPT_DIR _HLG_ENV
