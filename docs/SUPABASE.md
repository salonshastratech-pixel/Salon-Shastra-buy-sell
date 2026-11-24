# TeamOS on Supabase (PostgreSQL)

Supabase project: **salon-shastra-teamos** · region **Mumbai (ap-south-1)** · free plan ($0 / month; free projects
pause after about a week without use — open the dashboard and press *Restore* if that happens).

## What is set up (24 Nov 2026)

| Item | State |
|---|---|
| Tables | All TeamOS tables created from the same migration files the app uses (0001–0015), recorded in `schema_migrations` with the same checksums, so the app sees the database as up to date. Column-for-column identical to the local database (checked table by table). |
| Public API lock | Supabase also exposes tables through a public web API. TeamOS does not use it, so every table has row-level security switched on with **no** access rules and the `anon` / `authenticated` roles have no rights: the public API can read or write nothing. TeamOS connects directly as the database owner. |
| Indexes | Extra indexes for the links the app follows (migration 0015, from Supabase's performance check). |
| Data | Empty until the connection below is added; then fresh **synthetic** demo data only. Real pilot data stays on this computer until the founder decides otherwise (D-71). |

## Connecting TeamOS

1. Supabase dashboard → project → **Connect** → **Session pooler** → copy the URI; put the database password in place
   of `[YOUR-PASSWORD]` (forgot it: Project Settings → Database → Reset database password).
2. Save one line in `instance/supabase.env` (the `instance/` folder is never uploaded to GitHub):

       DATABASE_URL=postgresql://postgres.txxbxmcjfewqhzmvrcel:PASSWORD@aws-…pooler.supabase.com:5432/postgres

3. Start TeamOS with that environment (it encrypts the connection automatically and creates any missing tables).

The app turns PostgreSQL-specific behaviour into what it expects: `%` in queries is escaped, totals (`SUM`) come back as
plain numbers, and server-side prepared statements are off (the Supabase pooler does not support them).

## Checking that everything maps correctly

    TEAMOS_SQL_SHAPES=shapes.jsonl python -m pytest            # record every distinct query the app makes
    DATABASE_URL=… python -I scripts/qa/pg_check.py shapes.jsonl   # PostgreSQL type-checks each one (nothing executed)

`pg_check` reports queries PostgreSQL would reject, values whose type PostgreSQL would refuse at run time, and results
with two columns of the same name (SQLite keeps the first, PostgreSQL the last).

## Moving data

    DATABASE_URL=… python -I scripts/copy_to_postgres.py instance/<file>.db

One transaction (all or nothing), exact type conversion (stops and names the row if a value does not fit), then every
table is compared row by row and id counters are moved on. It refuses real customer records unless `--allow-real`
is given — only after the founder approves moving pilot data.
