# Salon Shastra TeamOS — Business Exchange, Match Desk & Team Command Center

Internal operating platform for Salon Shastra's salon buy–sell team. Milestone 1: foundation, access control and
seller management; Milestone 2: production hardening; Milestone 3: buyer / investor management; Milestone 4: forms & data requests; Milestone 5: matching engine + Match Desk; **Milestone 6: teasers, introductions, deals & admin finance**. See `docs/` for
architecture, roadmap, decisions and feature status.

## Run locally (5 minutes)

Requires Python 3.11+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # edit SECRET_KEY
export $(grep -v '^#' .env | xargs)
python3 manage.py migrate
python3 manage.py seed-demo     # synthetic data only; prints demo logins
python3 manage.py run           # http://127.0.0.1:8000
```

Demo logins (synthetic): `founder@`, `shaheen@`, `harika@`, `lead@`, `accounts@` `demo.salonshastra.test`,
password `Demo-Shastra-2026`. Try logging in as each to see role differences.

In development you can switch off two-step sign-in for the demo admin with `export MFA_REQUIRED_ROLES=`
(empty). Production always enforces it for Super Admin and Finance.

Try **Spreadsheet import** as `lead@` with `docs/samples/sample_seller_leads.csv`.

Deals (M6): from a match that is approved to approach, share the salon's approved anonymous teaser (seller's
*Teaser* tab), record both sides' consent, request the introduction — an approver releases contact details and the
**Deals** room opens (visits, versioned offers, due diligence, closing). **Admin Finance** holds fees, invoices,
receipts and expenses; sale prices are never counted as company revenue.

Matching (M5): open **Match Desk** for ranked buyer↔salon matches with the reasons and next steps, or the
*Matches* tab on any buyer or salon. Each record also shows its *Biggest unlock* — the questions holding up the
most matches. Press `/` anywhere to search.

Forms (M4): open **Forms & Requests** for the review inbox and the public quick-form links (`/f/seller`,
`/f/buyer`). From any seller or buyer page, *Forms & documents* sends a detailed form or a targeted request
(copy link or the clearly-labelled mock WhatsApp). Uploads need `FILES_KEY` in `.env` (see `.env.example`).

Buyers (M3): open **Buyers / Investors**. Sign in as `harika@` (buyer team) to work mandates, and as `lead@` to
review funding and confirm criteria (*Qualification* tab). `BY-…` demo records cover a fully qualified acquirer, an
investor group awaiting funding proof, a *new salon* need, a buyer's own submission that tried to self-verify, a
seller who also wants to buy, and an *exploring only* lead. An existing demo database gets the buyers by re-running
`seed-demo` after `migrate`.

Background worker: `python3 manage.py worker` (or `--once`). Backups: `python3 manage.py backup` and
`python3 manage.py verify-backup <file>`. Deployment: `deploy/RUNBOOK.md`.

Real first admin (no demo data): `python3 manage.py create-admin --email you@salonshastra.com --name "Your Name"`.

## Tests

```bash
python3 -m pytest
```

Tests are named after the PRD's UAT scenarios (e.g. `test_uat28_...`).

PostgreSQL portability check (needs a local PostgreSQL and `psql`):
```bash
TEAMOS_SQL_LOG=/tmp/sql.jsonl python3 -m pytest && python3 scripts/pg_replay.py /tmp/sql.jsonl
```

## Branding

Colours and fonts come from salonshastra.com (see top of `teamos/static/app.css`). The logo loads from the website;
to self-host it, save the official file as `teamos/static/brand/logo.png`.

## Rules for contributors

* Business rules and permission checks go in `teamos/domain/`, never only in templates.
* Schema changes = a new numbered file in `migrations/`. Never edit an applied migration.
* No real customer data in development. Seeded records are flagged `is_synthetic` and badged in the UI.
* Don't mark integrations "live" without verified credentials and an end-to-end test.
