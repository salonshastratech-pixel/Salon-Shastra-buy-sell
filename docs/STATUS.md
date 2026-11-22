# Feature status — Milestone 2 (22 Nov 2026, v0.2.0)

Legend: **Built** = implemented, database-backed and covered by automated tests · **Partial** · **Not run** · **Planned**

## Milestone 2 — production hardening

| Area | Status | Evidence |
|---|---|---|
| PostgreSQL compatibility | **Built** | Every SQL statement from the 72-test suite (5,402 statements, 58 databases) replayed on PostgreSQL 16 with zero errors (`scripts/pg_replay.py`). The harness was itself checked to catch SQLite-only SQL. The app's live connection uses `psycopg`, which is installed on the server from `requirements.txt` — first live run happens at deployment. |
| Spreadsheet import (CSV/XLSX) | **Built** | Auto column mapping, re-mapping, per-row status (new / already in CRM / repeated in file / invalid), per-row decisions, re-check at commit, raw rows preserved, no consent assumed, import provenance on every lead. UAT-03 for imports + 7 tests. Verified in a real browser. |
| Two-step sign-in (TOTP) | **Built** | Mandatory for Super Admin and Finance, optional for others; matches RFC 6238 test vectors; code replay refused; 8 single-use recovery codes; lockout after 5 wrong codes; admin reset for lost phones. 7 tests, including a check that turning enforcement off makes them fail. |
| Background worker + job queue | **Built** | Idempotent enqueue, safe claiming, retries with back-off, dead-letter after 5 attempts, run history, Admin → Background jobs page. |
| SLA sweep (first automation) | **Built** | Every 15 minutes: alerts the assignee once per overdue task; escalates to team leads once after 24 h. |
| Structured logs, 500 handler, `/healthz` | **Built** | JSON logs with no customer data; health check reports database, schema and stuck jobs. |
| Backups — SQLite | **Built** | Online snapshot, optional encryption, manifest; `verify-backup` restores and checks integrity, foreign keys, schema and row counts. Tampered files are rejected (UAT-26 test). |
| Backups — PostgreSQL | **Built (scripts)** | `deploy/backup.sh` + `deploy/restore_check.sh` were run against PostgreSQL 16: encrypted dump → restore into a scratch database → 29/29 table counts match. Corrupted files are rejected (a bug in this check was found and fixed). |
| Deployment pack | **Not run on VPS** | Caddy, gunicorn, systemd units, timer, env template, `deploy/RUNBOOK.md`. Standard configuration, but untested until the first deployment. |
| Fixed: CSRF token missing from macro-rendered forms | **Fixed** | Shipped in M1: most forms would have failed in a real browser. The tests missed it because they injected the token. New tests read the token from the page and fail if the bug returns. |

## Milestone 1 (unchanged, still passing)
Authentication, capability RBAC with grants, audit, seller management (quick screen, dedupe, 57-field intake with
answer states, evidence review, all-in cost, financials, SWOT, readiness, stages, checklist and approval with
re-review), tasks, My Day, Team Command Center, search, field dictionary.

## Not built yet
Buyers (M3), forms & data requests (M4), Match Desk (M5), deals, teasers and admin finance ledger (M6), WhatsApp and
reporting (M7). Menu entries show the milestone. Nothing is mocked. **WhatsApp is not connected.**
