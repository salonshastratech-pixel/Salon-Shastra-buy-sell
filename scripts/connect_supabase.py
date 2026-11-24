"""Connect TeamOS to Supabase and load the synthetic demo data — one command, nothing to edit by hand.

    python scripts/connect_supabase.py            # asks for the database password (typing is hidden)
    python scripts/connect_supabase.py --start    # same, then starts TeamOS on Supabase at http://127.0.0.1:8003

* The password is typed at a hidden prompt, tested, and saved only in instance/supabase.env (never uploaded).
* Demo logins all get the password Demo@123 (override with SEED_PASSWORD). Synthetic data only — the real pilot
  database is never touched (D-71).
"""
import getpass
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "instance" / "supabase.env"
REF = "txxbxmcjfewqhzmvrcel"
HOSTS = ["aws-0-ap-south-1.pooler.supabase.com", "aws-1-ap-south-1.pooler.supabase.com"]


def try_connect(url):
    import psycopg
    try:
        with psycopg.connect(url, prepare_threshold=None, connect_timeout=15, sslmode="require") as c:
            return c.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], None
    except Exception as e:  # noqa: BLE001 — report any connection problem in plain words
        return None, str(e).strip().splitlines()[0]


def find_url():
    saved = ""
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL=") and "[YOUR-PASSWORD]" not in line and "PASTE-HOST" not in line:
                saved = line.split("=", 1)[1].strip()
    if saved:
        version, err = try_connect(saved)
        if version:
            print("Using the saved Supabase connection.")
            return saved
        print(f"Saved connection did not work ({err}).")
    print("Supabase database password (Supabase dashboard → Project Settings → Database; typing is hidden):")
    pw = getpass.getpass("> ")
    if not pw:
        sys.exit("No password entered.")
    last = None
    for host in HOSTS:
        url = f"postgresql://postgres.{REF}:{quote(pw, safe='')}@{host}:5432/postgres"
        version, err = try_connect(url)
        if version:
            ENV_FILE.parent.mkdir(exist_ok=True)
            ENV_FILE.write_text("# TeamOS → Supabase (written by scripts/connect_supabase.py; never uploaded)\n"
                                f"DATABASE_URL={url}\n", encoding="utf-8")
            print(f"Connected (schema {version}). Connection saved to instance/supabase.env.")
            return url
        last = err
    if last and "password" in last.lower():
        sys.exit("Supabase says the password is wrong. Reset it under Project Settings → Database → "
                 "Reset database password, then run this again.")
    sys.exit(f"Could not reach Supabase: {last}\nIf the project is paused, open the dashboard and press Restore.")


def main():
    url = find_url()
    env = dict(os.environ, DATABASE_URL=url, SEED_PASSWORD=os.environ.get("SEED_PASSWORD", "Demo@123"))
    py = sys.executable
    print("Bringing tables up to date…")
    subprocess.run([py, "manage.py", "migrate"], cwd=ROOT, env=env, check=True)
    print("Loading synthetic demo data…")
    subprocess.run([py, "manage.py", "seed-demo"], cwd=ROOT, env=env, check=True)
    import psycopg
    with psycopg.connect(url, prepare_threshold=None, sslmode="require") as c:
        real = c.execute("SELECT COUNT(*) FROM contacts WHERE is_synthetic = 0").fetchone()[0]
        n = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in ("users", "contacts", "opportunities", "tasks", "match_candidates", "deals", "messages")}
    print("In Supabase now: " + ", ".join(f"{v} {k}" for k, v in n.items()) + f"; real contacts: {real}.")
    if "--start" in sys.argv:
        print("Starting TeamOS on Supabase at http://127.0.0.1:8003  (Ctrl+C to stop)")
        subprocess.run([py, "run_local.py"], cwd=ROOT, check=False,
                       env=dict(env, PORT="8003", DATA_NOTICE="SUPABASE — synthetic demo data"))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
