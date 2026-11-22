"""Local demo launcher: loads .env (synthetic SQLite DB, dev mode) then starts the dev server on 127.0.0.1:8000.
Never use for production."""
import os, sys
from pathlib import Path
root = Path(__file__).resolve().parent
os.chdir(root)
sys.path.insert(0, str(root))
for line in (root / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
assert os.environ.get("APP_ENV") != "production", "run_local.py is for local demos only"
sys.argv = ["manage.py", "run", "--host", "127.0.0.1", "--port", os.environ.get("PORT", "8000")]
import manage
manage.main()
