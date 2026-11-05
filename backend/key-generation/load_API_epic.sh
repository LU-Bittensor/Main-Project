#!/usr/bin/env bash
# Run the real-account Epic API without requiring a local blockchain node.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi
export GRANT_DATA_DIR="${GRANT_DATA_DIR:-$PWD/.grant-epic-ui-local}"
umask 077
mkdir -p "$GRANT_DATA_DIR"
if [ ! -f "$GRANT_DATA_DIR/master.key" ]; then
  .venv/bin/python -c 'import os,pathlib; pathlib.Path(os.environ["GRANT_DATA_DIR"],"master.key").write_bytes(os.urandom(32))'
fi
export GRANT_MASTER_KEY="$(.venv/bin/python -c 'import os,pathlib; print(pathlib.Path(os.environ["GRANT_DATA_DIR"],"master.key").read_bytes().hex())')"
exec .venv/bin/python grant_api.py --port 5001
