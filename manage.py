#!/usr/bin/env python3
"""Salon Shastra TeamOS management commands.

  python3 manage.py migrate
  python3 manage.py create-admin --email founder@salonshastra.com --name "Founder"
  python3 manage.py seed-demo          # synthetic data, clearly flagged; refuses in production
  python3 manage.py worker [--once]    # background jobs (SLA sweep, later re-scoring/messages)
  python3 manage.py backup --out backups && python3 manage.py verify-backup backups/<file>
  python3 manage.py run [--port 8000]  # development server only
"""
import argparse
import getpass
import os
import sys

from teamos import create_app
from teamos.db import migrate, open_standalone
from teamos.security import hash_password, validate_password_strength
from teamos.timeutil import now_iso


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    ca = sub.add_parser("create-admin")
    ca.add_argument("--email", required=True)
    ca.add_argument("--name", required=True)
    sub.add_parser("seed-demo")
    wk = sub.add_parser("worker")
    wk.add_argument("--once", action="store_true", help="schedule + run due jobs once, then exit (for cron/tests)")
    bk = sub.add_parser("backup")
    bk.add_argument("--out", default="backups")
    vb = sub.add_parser("verify-backup")
    vb.add_argument("file")
    rn = sub.add_parser("run")
    rn.add_argument("--port", type=int, default=8000)
    rn.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    app = create_app()
    url = app.config["DATABASE_URL"]

    if args.cmd == "migrate":
        db = open_standalone(url)
        print("Applied:", migrate(db) or "nothing (up to date)")
    elif args.cmd == "create-admin":
        pw = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Password: ")
        err = validate_password_strength(pw)
        if err:
            sys.exit(err)
        db = open_standalone(url)
        db.insert("users", {"email": args.email.strip().lower(), "full_name": args.name, "role": "super_admin",
                            "team": "admin", "password_hash": hash_password(pw), "is_active": 1,
                            "created_at": now_iso()})
        print("Super admin created.")
    elif args.cmd == "seed-demo":
        if app.config["APP_ENV"] == "production":
            sys.exit("Refusing to seed synthetic data into production.")
        from scripts.seed_demo import seed
        with app.app_context():
            seed(open_standalone(url))
    elif args.cmd == "worker":
        from teamos import jobs
        db = open_standalone(url)
        if args.once:
            jobs.schedule_periodic(db)
            print("Processed", jobs.run_once(db), "job(s)")
        else:
            jobs.worker_loop(db)
    elif args.cmd == "backup":
        if not url.startswith("sqlite:///"):
            sys.exit("PostgreSQL: use deploy/backup.sh (pg_dump). This command backs up SQLite only.")
        from teamos.backup import backup_sqlite
        m = backup_sqlite(url[len("sqlite:///"):], args.out)
        print(f"Backup written: {args.out}/{m['file']} (encrypted={m['encrypted']}, schema={m['schema']})")
    elif args.cmd == "verify-backup":
        from teamos.backup import verify_sqlite_backup
        ok, report = verify_sqlite_backup(args.file)
        print("\n".join(report))
        sys.exit(0 if ok else 1)
    elif args.cmd == "run":
        app.config["COOKIE_SECURE"] = False  # local http only
        app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
