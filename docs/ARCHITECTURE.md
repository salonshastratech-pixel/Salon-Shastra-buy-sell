# Salon Shastra TeamOS — Architecture

Version 0.1 (Milestone 1) · 22 Nov 2026 · Source of requirements: PRD TeamOS v1.1

## 1. What we are building

An internal operating system for a salon business-brokerage team: one place where every opportunity has an owner,
a stage, a next action and a history; where seller and buyer facts are captured with provenance (claimed vs
reviewed vs evidenced); where an explainable engine proposes buyer↔seller matches; and where sensitive information
(seller identity, company fees) is released only by permission and consent.

Design rules carried from the PRD: evidence ≠ claim; matching is a recommendation, not permission to introduce;
confidentiality by default; team-wide visibility with field-level restrictions; humans approve consequential actions.

## 2. Stack decision

| Layer | Choice | Why |
|---|---|---|
| Language / framework | **Python 3.12+, Flask 3** | Small, explicit, easy for a future developer to own. Python suits the scoring engine, imports (openpyxl) and reporting. |
| UI | **Server-rendered Jinja + small vanilla JS** | Fast on mid-range Android phones, no SPA build chain, every action works without JS. Can add HTMX-style partial updates later without a rewrite. |
| Database | **PostgreSQL 16** (production) · SQLite (dev/test) | Relational integrity, transactions for approvals/finance, mature backups. SQL is written to be portable; migrations are plain `.sql` files. |
| Data access | Thin helper over DB-API (no ORM) | Reviewable SQL, no hidden queries; migrations are explicit and checksum-protected. |
| Background work | **DB-backed job table** (`jobs`) + worker process | Re-scoring, reminders, webhook retries. Avoids Redis at this scale (< 20 users). |
| Files | Private object storage (S3-compatible) or encrypted VPS volume, served only through authorised routes | No guessable public URLs (PRD §13). *Milestone 4.* |
| Messaging | Provider adapter → **Meta WhatsApp Cloud API**; mock adapter for dev/UAT | Official platform, number owned by Salon Shastra. *Milestone 7.* |
| Web server | gunicorn behind Caddy (automatic TLS) | Simple, robust. |

### Hosting on Hostinger

Shared/"Web" hosting is **not suitable** (no long-running worker, no PostgreSQL, limited process control).
Recommended: **Hostinger KVM VPS (KVM 2: 2 vCPU / 8 GB)**, Ubuntu 24.04, running:

```
Caddy (TLS, :443) → gunicorn (Flask app, 3 workers) → PostgreSQL 16 (local, not exposed)
                  └ worker process (jobs: re-score, reminders, webhook retries)
nightly: pg_dump → age/GPG encrypt → off-site bucket (e.g. Backblaze B2 / AWS S3 Mumbai); monthly restore test
```

All credentials live in a root-owned `.env` on the VPS (never in git). Salon Shastra owns the Hostinger account,
domain, GitHub organisation and Meta Business assets (PRD §18).

## 3. Module structure

```
teamos/
  __init__.py        app factory, security headers, error handling
  db.py              connection, portable SQL helpers, migration runner
  security.py        login, server-side sessions, CSRF, lockout, route guards
  permissions.py     capability matrix + scoped/time-limited grants
  audit.py           append-only audit trail
  timeutil.py        UTC storage, IST display and day boundaries
  domain/            business rules — no HTTP code
    seller_fields.py field dictionary (single source for forms, privacy, matching inputs)
    sellers.py       lead creation, dedupe, provenance, all-in cost, financials, readiness, stages, approval
    tasks.py         tasks, next-action rule, activities, notifications
    buyers.py (M3 ✓)   forms.py + form_templates.py (M4 ✓)   matching.py (M5 ✓)   teasers.py + deals.py + finance.py (M6 ✓)   (M5) matching/   (M6) deals.py, finance.py   (M7) messaging/
  web/               thin HTTP handlers per area (auth, main, sellers, tasks, admin)
  templates/, static/
migrations/          0001_foundation.sql, …
scripts/seed_demo.py synthetic data (refuses to run in production)
tests/               pytest, mapped to PRD UAT IDs
```

Rule: **all permission checks and business rules live in `domain/` and are enforced server-side**; templates only hide controls.

## 4. Data model (key entities)

```
users ─< user_grants                       sessions, login_attempts, audit_events (append-only)
contacts ─< contact_consents (append-only ledger: purpose × channel × granted)
contacts ─< salon_businesses (one owner, many outlets)
contacts ─< opportunities (kind = seller | buyer; one person can have many over time)
opportunities ─1 seller_listings (specific salon offered; canonical fields; data_version; approval_state)
seller_listings ─< field_provenance (answer_state + evidence_status per field)
               ─< field_change_log (versioned history)
               ─< seller_monthly_financials (NULL ≠ ₹0)
               ─< swot_items, listing_checklist, listing_approvals (decision + data_version)
opportunities ─< stage_history, activities, tasks ─< task_events
duplicate_reviews, notifications, jobs
```

Coming next (migrations 0002+): `buyer_mandates` (+ ranked localities, must/prefer/flexible/avoid criteria),
`form_templates / form_invitations / form_submissions`, `match_runs / match_candidates / match_factor_results /
match_subtasks`, `deals / offers / milestones`, `fee_agreements / invoices / receipts` (admin-only),
`wa_accounts / conversations / messages / templates / webhook_events`.

### Why these shapes

* **Answer state is separate from the value.** `rent_deposit = NULL` + `answer_state = unknown` is different from
  "never asked" (no provenance row) and from `₹0`. Matching will reduce *evidence coverage* for unknowns, never reward them.
* **Evidence status is reviewer-only.** Editing a value resets it to seller-declared; only `evidence.review` can raise it.
* **`data_version` on listings** lets approvals and (later) match snapshots record exactly which facts they relied on.
  A material change after approval flips the listing to *re-review required* and keeps the old approval visible.
* **Two kinds of money are never mixed:** seller business economics live on the listing; Salon Shastra fees will live
  in separate admin-only tables behind `finance.view`.

## 5. Access control

Capabilities, not page checks. Roles give defaults (PRD §3.2 proposal); `user_grants` add explicit, reasoned,
optionally record-scoped and expiring exceptions. Restricted seller fields are visible to approvers, explicit
grantees, and the primary owner who collected them; for everyone else they are removed server-side before
rendering *and* cannot be written. Every denial is audited. See the live matrix at **Admin → Users & access**.

## 6. Matching engine design (Milestone 5 — designed now, built after buyers and forms)

Deterministic, versioned rules catalogue. For each buyer mandate × active listing:

1. **Hard gates** → pass / blocked / review-required (e.g. all-in cost > buyer hard ceiling where seller is not
   negotiable; lease *not permitted*; unacceptable deal structure; no consent/opt-out). Gates never overridable for consent/security.
2. **Compatibility 0–100** over applicable sub-factors in families A–J (PRD §8.2). "Not important" removes a factor
   from the denominator; unknown counterparty data scores 0 *and* lowers coverage.
3. **Evidence coverage %** = weight of applicable facts that are known and reviewed, with freshness.
4. **Operational readiness** = what action is permitted next (data request, approval, consent, intro candidate).

Each result stores model version, both parties' `data_version`, factor breakdown and generated *next questions*
("collect lease expiry from seller"). Changes to material fields enqueue incremental re-scoring jobs for the affected
side only. Rejected candidates are suppressed until a material fact changes.

## 7. WhatsApp integration (Milestone 7)

`MessagingProvider` interface with `MetaCloudProvider` and `MockProvider`. Outbound path: eligibility check at send
time (consent, opt-out, template approved, placeholders filled, quiet hours, frequency cap, duplicate guard) →
`queued` → provider accepted → delivered/read/failed via signed webhooks (idempotent by provider event ID). Ambiguous
timeouts go to *uncertain* for human review — never blind resends. The UI will always show **TEST/MOCK** until real
credentials pass an end-to-end check.

## 8. Known trade-offs

* SQLite in dev vs PostgreSQL in prod: SQL is portable and the PG adapter is written, but **untested here** (the
  driver could not be installed in this offline build environment). Running the suite against PostgreSQL is the first task of M2.
* Server-rendered UI means some interactions reload the page. Acceptable for M1; partial updates can be added.
* Brand tokens are taken from salonshastra.com's live CSS variables; confirm against an official brand kit.
