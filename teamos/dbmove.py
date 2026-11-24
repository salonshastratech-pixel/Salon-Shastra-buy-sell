"""One-time database move (e.g. Supabase Mumbai → Supabase Singapore, next to the app server).

The admin puts the new database's address in the server setting MOVE_TARGET_URL and presses "Copy now" on
Admin → Database move. TeamOS then:
  1. creates every table in the new database (its own migrations),
  2. copies every table in link order, all in ONE transaction on the new database (all or nothing),
  3. compares row counts table by table and moves id counters past the copied ids.
The old database is only read, never changed. Afterwards the admin points DATABASE_URL at the new database.
No password is ever shown or stored by TeamOS: both addresses live only in the server's settings.
"""
from __future__ import annotations

import re

from .db import MIGRATIONS_DIR, Database, migrate

SKIP = {"schema_migrations"}


def table_order() -> list[str]:
    """Tables in the order the migrations create them — every table is created after the tables it links to."""
    out = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        for m in re.finditer(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)", path.read_text(encoding="utf-8"), re.I):
            if m.group(1) not in out and m.group(1) not in SKIP:
                out.append(m.group(1))
    return out


def _columns(db: Database, table: str) -> list[str]:
    if db.dialect == "postgres":
        return [r["column_name"] for r in db.all(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = ? "
            "ORDER BY ordinal_position", (table,))]
    return [r["name"] for r in db.all(f"PRAGMA table_info({table})")]


def counts(db: Database) -> dict[str, int]:
    return {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in table_order()}


def copy(source: Database, target: Database) -> dict:
    """Copy everything from source into target (target's own business tables are emptied first)."""
    migrate(target)
    tables = table_order()
    copied = {}
    with target:
        if target.dialect == "postgres":
            target.execute("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY CASCADE")
        else:
            for t in reversed(tables):
                target.execute(f"DELETE FROM {t}")
        for t in tables:
            src_cols = _columns(source, t)
            cols = [c for c in _columns(target, t) if c in src_cols]
            rows = source.all(f"SELECT {', '.join(cols)} FROM {t} ORDER BY 1") if cols else []
            if rows:
                marks = ", ".join("?" for _ in cols)
                sql = target._sql(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({marks})")
                vals = [tuple(r[c] for c in cols) for r in rows]
                if target.dialect == "postgres":
                    with target.conn.cursor() as cur:
                        cur.executemany(sql, vals)
                else:
                    target.conn.executemany(sql, vals)
            copied[t] = len(rows)
        if target.dialect == "postgres":  # new records must get ids after the copied ones
            for t in tables:
                if "id" in _columns(target, t):
                    target.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "
                                   f"COALESCE((SELECT MAX(id) FROM {t}), 0) + 1, false)")
    after = counts(target)
    diff = {t: (copied[t], after[t]) for t in tables if copied[t] != after[t]}
    return {"tables": len(tables), "rows": sum(copied.values()), "copied": copied, "mismatch": diff}
