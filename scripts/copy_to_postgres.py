"""Copy a TeamOS SQLite database into an empty PostgreSQL database (e.g. Supabase), then prove nothing was lost.

  DATABASE_URL=postgresql://... python -I scripts/copy_to_postgres.py instance/teamos-demo.db [--allow-real]

* Refuses to copy real customer records (contacts not marked synthetic) unless --allow-real is given — the founder
  decides when real data leaves this computer.
* The target must already have the schema (the app creates it on start, or `python manage.py migrate`) and must be
  empty of business records.
* Everything happens in ONE transaction: either all tables arrive or nothing does.
* Values are converted to the exact PostgreSQL column type; anything that would not fit stops the copy with the
  table, row and column named (nothing is silently changed).
* Afterwards every table is compared row by row (count + content fingerprint) and id counters are moved past the
  copied ids so new records never clash.
"""
import hashlib
import os
import sqlite3
import sys

SKIP_TABLES = {"schema_migrations", "sqlite_sequence"}


def order_tables(src):
    tabs = [r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'") if r[0] not in SKIP_TABLES]
    deps = {t: {r[2] for r in src.execute(f"PRAGMA foreign_key_list({t})") if r[2] != t} for t in tabs}
    out, done = [], set()
    while len(out) < len(tabs):
        ready = sorted(t for t in tabs if t not in done and deps[t] <= done)
        if not ready:
            raise SystemExit(f"Circular table links: {sorted(set(tabs) - done)}")
        out += ready
        done |= set(ready)
    return out


def convert(v, pgtype, where):
    if v is None:
        return None
    if pgtype in ("integer", "bigint", "smallint"):
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, int):
            return v
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, str) and v.strip().lstrip("-").isdigit():
            return int(v.strip())
        raise SystemExit(f"Cannot store {v!r} as a whole number at {where}")
    if pgtype in ("real", "double precision", "numeric"):
        try:
            return float(v)
        except (TypeError, ValueError):
            raise SystemExit(f"Cannot store {v!r} as a number at {where}")
    if pgtype == "text":
        return v if isinstance(v, str) else str(v)
    return v


def fingerprint(rows):
    h = hashlib.sha256()
    for r in sorted(repr(tuple(x)) for x in rows):
        h.update(r.encode())
    return h.hexdigest()[:16]


def norm(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if hasattr(v, "as_integer_ratio") and not isinstance(v, (int, float)):  # Decimal
        f = float(v)
        return int(f) if f.is_integer() else f
    return v


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    allow_real = "--allow-real" in sys.argv
    if not args:
        raise SystemExit(__doc__)
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgres://")):
        raise SystemExit("Set DATABASE_URL to the target PostgreSQL database.")
    src = sqlite3.connect(args[0])
    real = src.execute("SELECT COUNT(*) FROM contacts WHERE is_synthetic = 0").fetchone()[0]
    if real and not allow_real:
        raise SystemExit(f"{real} contact(s) are real (not marked synthetic). Not copied. Re-run with --allow-real "
                         "only when the founder has approved moving real customer data.")
    import psycopg
    dst = psycopg.connect(url, autocommit=False, prepare_threshold=None,
                          **({} if "sslmode=" in url or "@localhost" in url else {"sslmode": "require"}))
    cur = dst.cursor()
    sv = src.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    cur.execute("SELECT MAX(version) FROM schema_migrations")
    dv = cur.fetchone()[0]
    if not dv or dv < sv:
        raise SystemExit(f"Target schema is at {dv}, source at {sv}: start the app (or manage.py migrate) on the "
                         "target first.")
    cur.execute("SELECT COUNT(*) FROM opportunities")
    if cur.fetchone()[0]:
        raise SystemExit("Target already has records — refusing to mix data. Use an empty database.")
    tables = order_tables(src)
    report = []
    try:
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
        for t in tables:
            cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = "
                        "current_schema() AND table_name = %s", (t,))
            pg_types = dict(cur.fetchall())
            if not pg_types:
                raise SystemExit(f"Table {t} is missing in the target database.")
            cols = [r[1] for r in src.execute(f"PRAGMA table_info({t})")]
            missing = [c for c in cols if c not in pg_types]
            if missing:
                raise SystemExit(f"Columns missing in target {t}: {missing}")
            order = "id" if "id" in cols else ", ".join(cols)
            rows = src.execute(f"SELECT {', '.join(cols)} FROM {t} ORDER BY {order}").fetchall()
            data = [[convert(v, pg_types[c], f"{t} row {i + 1} column {c}") for v, c in zip(r, cols)]
                    for i, r in enumerate(rows)]
            if data:
                ph = ", ".join(["%s"] * len(cols))
                cur.executemany(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({ph})", data)
            if "id" in cols and pg_types.get("id") in ("bigint", "integer"):
                cur.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), COALESCE(MAX(id), 0) + 1, false) "
                            f"FROM {t}")
            cur.execute(f"SELECT {', '.join(cols)} FROM {t}")
            back = [[norm(v) for v in r] for r in cur.fetchall()]
            want = [[norm(v) for v in r] for r in data]
            ok = len(back) == len(want) and fingerprint(back) == fingerprint(want)
            report.append((t, len(rows), ok))
            if not ok:
                raise SystemExit(f"Verification failed for {t}: {len(want)} rows sent, {len(back)} read back "
                                 "or content differs.")
        dst.commit()
    except BaseException:
        dst.rollback()
        print("Nothing was copied (all changes rolled back).")
        raise
    total = sum(n for _, n, _ in report)
    print(f"Copied {total} rows in {len(report)} tables; every table verified (count + content).")
    for t, n, _ in report:
        if n:
            print(f"  {t:28} {n:6}  ✓")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
