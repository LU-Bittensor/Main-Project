export SUPABASE_URL=https://cwyxwmzquatqlbufsdob.supabase.co
export SUPABASE_PUBLISHABLE_KEY='sb_publishable_7NtcuZPUT618En1FAYVAVA_K0mBwNW9'
export GRANT_DATA_DIR="$PWD/.grant-supabase-local"
# Generate once and retain this key across API restarts.
(umask 077; mkdir -p "$GRANT_DATA_DIR"; if [ ! -f "$GRANT_DATA_DIR/master.key" ]; then
  .venv/bin/python -c 'import os, pathlib; p = pathlib.Path(os.environ["GRANT_DATA_DIR"]) / "master.key"; p.write_bytes(os.urandom(32))'
fi)
export GRANT_MASTER_KEY="$(.venv/bin/python -c 'import os, pathlib; print((pathlib.Path(os.environ["GRANT_DATA_DIR"]) / "master.key").read_bytes().hex())')"
.venv/bin/python grant_api.py