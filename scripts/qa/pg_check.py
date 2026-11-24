"""Check every query TeamOS runs against a real PostgreSQL database (e.g. Supabase) — without changing any data.

1. Record the queries:  TEAMOS_SQL_SHAPES=shapes.jsonl python -m pytest        (each distinct statement once)
2. Check them:          DATABASE_URL=postgresql://... python -I scripts/qa/pg_check.py shapes.jsonl

For each statement PostgreSQL parses and type-checks it against the real tables (PREPARE, never executed, all inside
a transaction that is rolled back). Reports:
  * statements PostgreSQL rejects (syntax, GROUP BY rules, unknown functions, type errors),
  * values the app sends with a type PostgreSQL would refuse at run time (e.g. a number compared with a text column),
  * results with two columns of the same name (SQLite keeps the first, PostgreSQL the last).
Exit code 1 if anything needs fixing.
"""
import json
import os
import re
import sys

SKIP = re.compile(r"^\s*(BEGIN|COMMIT|ROLLBACK|PRAGMA|CREATE|ALTER|DROP|SAVEPOINT|RELEASE)\b|sqlite_master", re.I)
NUMERIC = {"smallint", "integer", "bigint", "numeric", "real", "double precision"}


def to_dollar(sql: str) -> tuple[str, int]:
    """Replace ? placeholders outside string literals with $1, $2, ..."""
    out, n, in_str = [], 0, False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
        if ch == "?" and not in_str:
            n += 1
            out.append(f"${n}")
        else:
            out.append(ch)
    return "".join(out), n


def load(path):
    seen, items, dups = set(), [], {}
    for line in open(path, encoding="utf-8"):
        rec = json.loads(line)
        sql = " ".join(rec["sql"].split())
        if rec.get("dup"):
            dups[sql] = rec["dup"]
        if SKIP.search(sql) or sql in seen:
            continue
        seen.add(sql)
        items.append((sql, rec.get("params") or []))
    return items, dups


def main(path):
    import psycopg
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgres://")):
        raise SystemExit("Set DATABASE_URL to the PostgreSQL database to check against.")
    items, dups = load(path)
    conn = psycopg.connect(url, autocommit=False, prepare_threshold=None,
                           **({} if "sslmode=" in url or "@localhost" in url else {"sslmode": "require"}))
    bad, typed = [], []
    with conn.cursor() as cur:
        for i, (sql, params) in enumerate(items):
            q, n = to_dollar(sql)
            cur.execute("SAVEPOINT s")
            try:
                cur.execute(f"PREPARE teamos_chk AS {q}")
                cur.execute("SELECT parameter_types::text[] FROM pg_prepared_statements WHERE name = 'teamos_chk'")
                types = cur.fetchone()[0] or []
                for j, t in enumerate(types):
                    v = params[j] if j < len(params) else None
                    if v is None:
                        continue
                    if t == "text" and isinstance(v, (int, float)) and not isinstance(v, bool):
                        typed.append((sql, j + 1, t, type(v).__name__))
                    elif t in NUMERIC and isinstance(v, str) and not re.fullmatch(r"-?\d+(\.\d+)?", v.strip()):
                        typed.append((sql, j + 1, t, f"str {v[:20]!r}"))
                cur.execute("DEALLOCATE teamos_chk")
                cur.execute("RELEASE SAVEPOINT s")
            except psycopg.Error as e:
                cur.execute("ROLLBACK TO SAVEPOINT s")
                bad.append((sql, str(e).splitlines()[0]))
    conn.rollback()
    conn.close()
    print(f"Checked {len(items)} distinct statements against PostgreSQL.")
    print(f"  rejected by PostgreSQL: {len(bad)}")
    for sql, err in bad:
        print(f"    - {err}\n      {sql[:300]}")
    print(f"  value type mismatches: {len(typed)}")
    for sql, pos, t, got in typed:
        print(f"    - ${pos} expects {t}, app sends {got}\n      {sql[:300]}")
    print(f"  results with duplicate column names: {len(dups)}")
    for sql, cols in dups.items():
        print(f"    - {cols}\n      {sql[:300]}")
    sys.exit(1 if bad or typed or dups else 0)


if __name__ == "__main__":
    main(sys.argv[1])
