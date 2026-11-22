# Feature status — Milestone 3 (23 Nov 2026, v0.3.0)

Legend: **Built** = implemented, database-backed and covered by automated tests · **Partial** · **Not run** · **Planned**

## Milestone 3 — buyer / investor management (awaiting founder review)

| Area | Status | Evidence |
|---|---|---|
| Buyer opportunities (`BY-`), quick screen, duplicate check | **Built** | Same contact can hold seller + several buyer mandates, each with its own stage and history (UAT-05). Duplicate mobile → link or flag, never silent merge. |
| Acquisition mandate (§7.2 / §7.2A) | **Built** | 38 canonical fields in 8 sections with answer states and buyer-declared provenance; hard **all-in ceiling**; inclusions (deposit / stock / working capital); change log and data version. |
| Ranked localities + exclusions | **Built** | Add, rank up/down, remove; duplicates refused; ranking changes are versioned. |
| Must / Prefer / Flexible / Avoid / Not important | **Built** | Per option for format, model, positioning and deal structure; per criterion for scalar needs (min chairs, area, rent, lease, sales, years, staff, transition). Blank ≠ not important. |
| Reviewer-only funding review | **Built** | Status team-visible; evidence notes restricted to reviewers or a per-case `buyer.view_funding` grant (UAT-31). Buyer or staff payloads can't set it — ignored and audited (UAT-09). |
| Criteria confirmation | **Built** | Reviewer confirms the current data version; any later change marks it "changed since review" and re-opens the queue. |
| Seriousness score (§7.3) | **Built** | 6 factors / 100 with reasons, version, freshness and reviewer; missing answers score 0; evidence points need a reviewer. Advisory only. |
| Buyer funnel (§7.1) | **Built** | Gates for requirements captured, basic qualified, funding review and qualified to shortlist; *new salon* need never qualifies as a resale buyer; holding stages need a follow-up. Stages needing M5/M6 are shown but blocked, not simulated. |
| Respondent submission path (UAT-02) | **Partial** | Server-side path built and tested (routing to buyer team, review task, no approvals). The public form page/link ships in Milestone 4. |
| Shared screens | **Built** | My Day, Team Command Center (new buyer review queue), Tasks, Search, Duplicates and Field dictionary handle buyers. |
| Matches tab | **Planned (M5)** | Honest empty state — no match history is simulated. |
| PostgreSQL replay for the new SQL | **Not run** | No PostgreSQL on this machine. The new migration avoids SQLite-only syntax (list view groups localities in Python instead of `group_concat`). Re-run `scripts/pg_replay.py` before deployment. |

Tests: 88 automated tests pass (73 from M1/M2 incl. two review fixes, 15 new for M3). Verified by hand in a real browser.

## Fixed during the M2 review (before M3)
* Finance role could read seller names / refs through *Tasks → All open (team)*. The list now applies the same rule as the task page. Regression test added.
* MFA replay test was timing-flaky (could cross a 30-second boundary). The app logic was correct; the test now replays the exact code.

---

# Milestone 2 status (22 Nov 2026, v0.2.0)

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
Forms & data requests (M4), Match Desk (M5), deals, teasers and admin finance ledger (M6), WhatsApp and
reporting (M7). Menu entries show the milestone. Nothing is mocked. **WhatsApp is not connected.**
