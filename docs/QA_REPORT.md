# QA report — after Milestone 6 (23 Nov 2026, v0.6.0)

Scope: everything built in Milestones 1–6, tested beyond the automated suite. All findings below are fixed and covered
by tests unless marked otherwise. Repeat these checks before every release (scripts described at the end).

## 1. Every page as every role (crawler)
* 1,147 page loads (founder 282, lead 277, Shaheen 276, Harika 276, accounts 36) following every link from every page.
* **0 server errors, 0 broken links, 0 pages with an error trace.**
* Refusals were exactly the intended ones: team members → admin pages (and import for non-leads); Finance → all CRM.
* **Finding (fixed): the Finance user could open match and deal tasks** in *Tasks → All open*, which carry buyer and
  salon names. The earlier rule only hid seller-linked tasks. Now any CRM-linked task needs CRM access unless it is
  assigned to you. Regression test: `test_finance_user_cannot_see_match_or_deal_tasks`.

## 2. Forged requests (insider bypassing the buttons)
Each of Shaheen, Harika, Ravi (lead) and Accounts POSTed directly — with a valid security token of their own session —
to: approve listing, record funding review, approve a match to approach, close a deal as won, record a fee agreement,
create a Super Admin, grant a capability, approve a teaser, approve an introduction; and opened Admin Finance, the audit
log, user admin and restricted documents.
* **56 attempts, 56 refused, database unchanged in every case.** Anonymous visitors were always sent to sign in; a
  POST without a security token was rejected (400).

## 3. Background jobs, backup and restore
* `worker --once`: SLA sweep, form chase/expiry and match re-scoring all completed (no failures, no dead jobs).
* Backup → restore check: row counts match the manifest, integrity and foreign keys pass, 6 users can sign in.

## 4. Real browser, end to end (on a copy of the demo data)
Deal DL-00001: accepted the seller's ₹21 L counter → deal moved to due diligence → ticked all 9 diligence items →
closing was correctly blocked until the handover date was entered → closed as won by the Founder →
**Admin Finance showed the 5% success fee as earned: ₹1.05 L**, with the ₹21 L shown as the parties' money, not revenue.
* **Finding (fixed): after an offer was accepted the deal had no next step.** A "Complete due diligence" task is now
  created for the deal owner.
* **Finding (fixed): a won deal's match showed "facts changed since scoring"** (closing the records looked like a data
  change). Closed records are no longer flagged stale.

## 5. Phone screens (375 px)
21 main pages checked. **Findings (fixed):** seller intake, the deal room, match detail and the forms inbox were wider
than the screen (long dropdowns, a wide checklist table, long badges and headers). Layout now never scrolls sideways.

## Not covered here
* PostgreSQL (production database) — no PostgreSQL on this machine; run `scripts/pg_replay.py` before deploying.
* Real WhatsApp, e-mail and deployment — not built / not connected yet.
* Load testing — the demo dataset is small; re-check list speed with a few thousand records before go-live.

## How to repeat
Both scripts use the synthetic demo logins (`seed-demo`) and never touch real data.
```powershell
.venv\Scripts\python.exe scripts\qa\crawl_all_roles.py
.venv\Scripts\python.exe scripts\qa\forged_request_probe.py . instance\qa_copy.db
```
The crawler needs the app running (default http://127.0.0.1:8000, or set `QA_BASE_URL`). The probe copies
`instance\teamos-demo.db` to the path given and attacks the copy, so the demo data is never changed.
Expected: `TOTAL PROBLEMS: 0` and `PROBLEMS: 0`.
