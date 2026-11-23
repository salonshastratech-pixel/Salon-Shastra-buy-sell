# Feature status — Milestone 6 (23 Nov 2026, v0.6.0)

Legend: **Built** = implemented, database-backed and covered by automated tests · **Partial** · **Not run** · **Planned**

## Milestone 6 — teasers, introductions, deal room & admin finance (awaiting founder review)

| Area | Status | Evidence |
|---|---|---|
| Anonymous teasers (FR-016, UAT-50) | **Built** | Whitelisted items only (area, format, positioning, size, years, indicative price, banded sales/rent, lease remaining, structure, safe reason categories). Name, phone, address, brand, lowest price, private notes and documents can never appear; the headline is checked for the salon/owner name. Versioned; seller approves the content categories (recorded as consent), then a reviewer approves; content frozen at approval. |
| Teaser sharing | **Built** | Only for matches approved to approach; expiring 14-day link (hashed token), copy link or labelled mock WhatsApp; opening is recorded on the match. Public page shows only the frozen content + disclaimers, no-index. |
| Introductions (FR-018, UAT-15) | **Built** | Requested after interest; approver-only decision; requires both sides' recorded identity consent, an approved teaser shared, no do-not-contact. Approval stores exactly what was given to whom and the consent rows/versions relied on, then opens the deal room. |
| Deal room (FR-019, UAT-17) | **Built** | Stages per PRD §9.2 with gates (visit before "visit completed", accepted offer before agreement, diligence + handover date + approver before closed won). Visits create tasks; offers are versioned (counter = new version, history kept) and create response tasks for the other side; completed work closes its own tasks; 9-item due-diligence checklist; closed-lost needs a reason and a lesson. Closing marks both records closed won. |
| Admin finance (FR-020, FR-050, UAT-18/19) | **Built** | Fee agreements per deal (payer, type, fixed or % of the agreed price, earning condition, versioned), invoices (tax only if entered — D-05 open), receipts, expenses. Earned = only when the condition is met; due = earned − received. Sale consideration is shown separately and never counted as revenue. |
| Finance permissions (UAT-47) | **Built** | Finance specialist can view and edit the ledger but not the CRM deal room; ordinary staff get 403 and see only "commercial verification pending / terms recorded" on a deal. Every change audited. |
| Real WhatsApp delivery, inbox, reminders | **Planned (M7)** | All sends are still the labelled mock. |
| Leadership dashboards & exports | **Planned (M7)** | |
| PostgreSQL replay for the new SQL | **Not run** | No PostgreSQL here; SQL kept portable. |

Found and fixed during the browser walkthrough: offer "valid until" dates were stored as task deadlines without a time,
which crashed the deal page and would have broken My Day (now converted to 6 pm IST and validated; pages covered by a
test); "arrange a visit" and "respond to offer v1" tasks stayed open after the work was done (now auto-closed);
the teaser preview hid real values for optional items.

Tests: 133 automated tests pass (124 from M1–M5, 9 new for M6 covering UAT-15, 17, 18, 19, 47, 50). Walked through in a real browser.

---

# Milestone 5 status (23 Nov 2026, v0.5.0)

Legend: **Built** = implemented, database-backed and covered by automated tests · **Partial** · **Not run** · **Planned**

## Milestone 5 — matching engine + Match Desk (awaiting founder review)

| Area | Status | Evidence |
|---|---|---|
| Rules catalogue v0.1 | **Built** | 23 criteria in the PRD's 10 factor families (A–J, weights 20/15/10/10/10/10/8/8/5/4 = 100). Deterministic; version stored with every result. Weights are proposals (D-09). |
| Four separate outputs (§8.3) | **Built** | Eligibility gate (pass / needs a look / blocked), compatibility 0–100 over facts known on both sides, coverage % + confidence, operational readiness. Never collapsed into one number. |
| Price = all-in vs all-in | **Built** | Seller's itemised all-in requirement vs buyer's hard ceiling. Unknown deposit = incomplete, never ₹0; incomplete totals never show "headroom" (regression test). Lowest acceptable price is used internally for near-matches but never displayed. |
| Hard gates | **Built** | Must-have mismatches, budget over ceiling, excluded area, different city (unless buyer relocates), no authority, lease not transferable / too short with no renewal, do-not-contact. |
| Near matches (§8.4) | **Built** | Flexible shortfalls (e.g. 4 chairs vs 6 preferred), adjacent areas, small budget gaps where the seller is negotiable — each with the condition needed. No concession is assumed. |
| Next questions | **Built** | Every unknown or partial produces a concrete question for the buyer or seller side; blocked matches only show questions that could actually resolve the blocker. |
| Readiness & approval | **Built** | Information required → awaiting verification (listing approved, criteria confirmed, funding reviewed) → awaiting consent (both sides' contact + sharing consent) → ready to approach. "Approve to approach" is reviewer-only and re-checks everything (UAT-13). |
| Stale & incremental re-scoring (UAT-14/36) | **Built** | Input fingerprints per side; changed facts mark matches stale; only changed pairs are re-scored (record pages, Match Desk button, 15-minute worker job). Each changed result is kept as a snapshot. |
| Suppression (UAT-49) | **Built** | Not suitable / buyer declined / seller declined need a reason; the match stays out of queues until either side's facts change or a revisit date passes, then resurfaces with an explanation. |
| Match subtasks (UAT-35) | **Built** | One shared match task + independent buyer-side and seller-side tasks for each owner; duplicates reused; parent closes itself when both are done. |
| Match Desk (§8.5) | **Built** | 14 queues incl. new, need information, near matches, awaiting verification/consent, ready, stale, blocked, not suitable; start from a buyer or a salon; min fit; "only mine". 360° comparison with per-family scores, decision timeline and scoring history. |
| One-click actions | **Built** | Ask the buyer/owner the missing questions by targeted form (M4), split tasks, shortlist, approve, record interest, not suitable, hold, reopen, co-ordinator, notes with @mentions, re-score. |
| Ranked lists per record (UAT-37/38) | **Built** | Matches tab on every buyer (salons) and salon (buyers), same rules, blocked last, confidentiality respected; finance has no access. |
| Productivity layer | **Built** | *Biggest unlock* card on each record (the questions holding up the most matches, asked in one form); My Day matching tiles; Team Command Center match panel; one alert to both owners for a strong new match; `/` jumps to search. |
| Introductions, visits, offers | **Planned (M6)** | Stages after "interest confirmed" are shown as coming in Deals. |
| Geo distance / commute radius | **Planned** | Needs geocoded addresses; until then locality ranking + exclusions are used and no distance is claimed. |
| PostgreSQL replay for the new SQL | **Not run** | No PostgreSQL here; SQL kept portable. |

Tests: 124 automated tests pass (102 from M1–M4, 22 new for M5 covering PRD §8.7 examples 1–10 and UAT-10–14, 35–39, 49). Walked through in a real browser.

---

# Milestone 4 status (23 Nov 2026, v0.4.0)

Legend: **Built** = implemented, database-backed and covered by automated tests · **Partial** · **Not run** · **Planned**

## Milestone 4 — forms & data requests (awaiting founder review)

| Area | Status | Evidence |
|---|---|---|
| Versioned form templates | **Built** | `seller-quick v1`, `buyer-quick v1`, `seller-deep v1`, `buyer-deep v1`, `targeted v1`. Every question is a canonical field; the version is stored on each link and submission. Customer-facing wording and help are separate from staff wording (internal guidance never shown — tested). |
| Public quick forms `/f/seller`, `/f/buyer` (FR-009/010) | **Built** | New lead + review task, routed to the seller / buyer team; consent required and versioned; possible duplicates flagged, never linked. Signed form token, honeypot and 5-per-hour rate limit per IP. Reviewer-only fields dropped and audited. |
| Personal expiring links (FR-043) | **Built** | Token shown once to staff, stored only as a SHA-256 hash; 14-day expiry (7 for targeted); one live detailed link per record; revoke. Lifecycle: sent → opened → in progress → submitted → reviewed / more info, or expired / revoked. Delivery and read are never claimed. |
| Drafts & resume | **Built** | "Save and finish later" keeps a draft on the same link; the detailed form is prefilled with the record's current non-confidential answers. |
| Conditional questions | **Built** | Franchise-only questions appear only when "Franchise" is chosen (reuses the dictionary's `show_if`). |
| Review inbox + field-by-field apply (FR-011/047, UAT-42) | **Built** | Answers never change a record on their own. Reviewer sees "customer says" vs "on record now", ticks what to apply; applied facts are customer-declared with the submission as their source. Material change → approved listing back to re-review; buyer criteria marked "changed since review". Funding status never changes from a form. |
| Targeted missing-field requests | **Built** | Staff pick specific questions/documents (most valuable missing ones pre-selected) and a message; each question shows why it is asked. |
| Secure uploads (FR-013) | **Built** | PDF/PNG/JPG checked by content (not extension), ≤5 MB, encrypted at rest (Fernet, `FILES_KEY`), random names outside the web root. Seller documents: owner / approvers / grants. Buyer proof of funds: the whole team (D-22), finance only by grant. Every open (and denial) audited. |
| WhatsApp send — mock (UAT-41 mock) | **Built (mock only)** | Records source record, invitation ID, template, sender and a `mock-…` provider message ID; status "provider accepted", never "delivered". Clearly labelled TEST-ONLY; nothing leaves the server. Live Meta adapter is Milestone 7. |
| Chasing & expiry job | **Built** | Hourly: one chase task per link not completed after 48 h; expired links closed. |
| Automatic outbound reminders, quiet hours, frequency caps | **Planned (M7)** | Need the real messaging provider. |
| Telugu forms (FR-052, P1) | **Planned** | Templates are ready for translation (labels separated from canonical keys). |
| Staff-side document upload on a record | **Planned** | Customers can upload now; staff upload is a small follow-up. |
| UAT-47 (finance user edits deal fee) | **Moved to M6** | It depends on deal fees, which arrive with Deals & admin finance. |
| PostgreSQL replay for the new SQL | **Not run** | No PostgreSQL on this machine. New SQL is plain/portable; run `scripts/pg_replay.py` before deployment. |

Tests: 102 automated tests pass (88 from M1–M3, 14 new for M4). Verified by hand in a real browser (send → customer answers → review → apply).

---

# Milestone 3 status (23 Nov 2026, v0.3.0)

Legend: **Built** = implemented, database-backed and covered by automated tests · **Partial** · **Not run** · **Planned**

## Milestone 3 — buyer / investor management (awaiting founder review)

| Area | Status | Evidence |
|---|---|---|
| Buyer opportunities (`BY-`), quick screen, duplicate check | **Built** | Same contact can hold seller + several buyer mandates, each with its own stage and history (UAT-05). Duplicate mobile → link or flag, never silent merge. |
| Acquisition mandate (§7.2 / §7.2A) | **Built** | 38 canonical fields in 8 sections with answer states and buyer-declared provenance; hard **all-in ceiling**; inclusions (deposit / stock / working capital); change log and data version. |
| Ranked localities + exclusions | **Built** | Add, rank up/down, remove; duplicates refused; ranking changes are versioned. |
| Must / Prefer / Flexible / Avoid / Not important | **Built** | Per option for format, model, positioning and deal structure; per criterion for scalar needs (min chairs, area, rent, lease, sales, years, staff, transition). Blank ≠ not important. |
| Reviewer-only funding review | **Built** | Status and evidence visible to the whole team (D-22, decided 23 Nov); only reviewers record the review (UAT-31). Buyer or staff payloads can't set it — ignored and audited (UAT-09). |
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
