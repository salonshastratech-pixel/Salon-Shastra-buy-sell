# Handover — Salon Shastra TeamOS

Internal platform for Salon Shastra's salon buy–sell team: sellers, buyers/investors, customer forms, and the
Match Desk. Current version: **v0.7.0 (Milestone 7)**. Next: Milestone 8 — measured improvements (after founder review).

Read in this order: this file → `README.md` → `docs/STATUS.md` (what is built, with evidence) → `docs/ROADMAP.md`
→ `docs/DECISIONS.md` (founder decisions — do not change an approved rule without the founder) →
`docs/ARCHITECTURE.md`. The product requirements are in the PRD v1.1 (kept outside this repository).

## Access — please don't share passwords

Ask the account owner to add you as a **collaborator** (repository → Settings → Collaborators → Add people) and
sign in with **your own** GitHub account. Sharing one login means nobody can tell who changed what, and it
cannot be taken back from one person without locking out everyone. Turn on two-factor authentication.

## Run it on a new Windows laptop (≈15 minutes)

1. Install **Python 3.12** from python.org (tick "Add python.exe to PATH") and **Git** from git-scm.com.
2. Get the code (PowerShell):
   ```powershell
   git clone https://github.com/salonshastratech-pixel/Salon-Shastra-buy-sell.git C:\code\teamos
   cd C:\code\teamos
   ```
   Keep the folder path short — pip fails on very long Windows paths.
3. Create the environment and install:
   ```powershell
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
4. Create your local settings: copy `.env.example` to `.env`, then fill in two values:
   * `SECRET_KEY` — any long random string: `.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"`
   * `FILES_KEY` — `.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   * For a local demo also set `MFA_REQUIRED_ROLES=` (empty) and `COOKIE_SECURE=0`.
   `.env` is never committed. Each environment (your laptop, the server) has its own keys.
5. Create the database with synthetic demo data and start the app:
   ```powershell
   .venv\Scripts\python.exe manage.py migrate
   .venv\Scripts\python.exe manage.py seed-demo
   .venv\Scripts\python.exe run_local.py
   ```
   Both `manage.py` and `run_local.py` read `.env` automatically (real environment variables take priority).
6. Open http://localhost:8000 — demo logins are in `LOCAL_DEMO.md` (all synthetic).

Tests (≈6 minutes): `.venv\Scripts\python.exe -m pytest -q` — all 124 should pass before any change is merged.

## Ground rules for this codebase

* Business rules and permission checks live in `teamos/domain/`, never only in templates.
* Database changes = a new numbered file in `migrations/`. Never edit a migration that has been applied.
* No real customer data on laptops or in the repository. Seed data is synthetic and badged.
* WhatsApp is a **labelled mock** until Milestone 7; never present anything as "sent" or "delivered" that wasn't.
* The matching rules are versioned (`match-rules v0.1`). Changing a weight or gate = bump the version and record
  the decision in `docs/DECISIONS.md`.
* Commit small, describe *why*, and keep `docs/STATUS.md` honest (Built / Partial / Not run / Planned).

## Not done yet / known gaps

* Not deployed anywhere. The VPS deployment pack is in `deploy/` (see `deploy/RUNBOOK.md`) but has never been run.
* PostgreSQL: the production database path has not been exercised for the M3–M5 SQL; run `scripts/pg_replay.py`
  against a real PostgreSQL before deploying.
* The background worker (`manage.py worker`) must run in production for chasing, expiry and 15-minute re-scoring.
