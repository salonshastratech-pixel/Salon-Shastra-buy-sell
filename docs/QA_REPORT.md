# QA report — load test: 100 sellers + 100 buyers through every module (23 Nov 2026, v0.7.5)

`scripts/qa/load_and_sweep.py` copies the demo database, signs in as each role and creates 100 synthetic sellers and
100 synthetic buyers **through the real screens**, then exercises every module:

* Sellers: create (incl. a deliberate duplicate), 11 intake sections, monthly financials, SWOT, calls with next
  actions, notes, checklist → review → listing approval for 40 salons.
* Buyers: create, 6 requirement sections with importance, salon-type/floor/area preferences, ranked areas, funding
  review, criteria confirmation, calls.
* Forms: 20 personal detailed forms sent, answered by "customers" on the public link, reviewed and applied.
* Matching: full recompute (8,230 pairs in ~15 s), every Match Desk queue, match pages, shortlist, split tasks,
  "ask buyer", notes, decline with reason.
* Deals: approach → teaser (create, seller approval, approval) → share link opened → both consents → introduction →
  deal room → visit → offer → accepted → diligence → dates → closed won (and one closed lost); fee agreement, invoice,
  receipt, expense.
* WhatsApp: conversations started, quick messages, inbound simulations, replies, hand-overs, STOP, unknown number,
  batch draft → approve → send.
* Tasks (complete with next step), My Day for every role, Team, search, reports (today/week/month), digest, every
  admin page, smart import of a generated workbook (20 new + 10 existing people), background jobs.

**Result: 3,206 actions, 0 crashes, 0 pages slower than 1.5 s; crawl of 2,447 pages across 5 roles on the loaded
data: 0 errors.** The 42 remaining refusals are correct validations (an area already in the buyer's list, a form not
sent without recorded consent, a reviewer-only field ignored).

Fixed during the load test:
* Match Desk took ~1.8 s with 8,000+ pairs → 0.3–0.5 s: queue counts in one pass instead of 14 queries, open-task
  counts only for the rows shown, the staleness check at most once a minute (the background job still runs it).
  The desk also accepts `?q=` as well as `?queue=`.
* Team Command Center 1.1 s → 0.25 s (same techniques; the "Later" list shows the next 100 with a link to all tasks).
* Seller financial notes ("figures are seller-declared") were shown in red as errors → now an amber warning.
* "Ask the buyer/seller" on a match failed with "Pick at least one question" when the open questions need a call →
  it now says which questions to ask on a call.

---

# QA report — after Milestone 7 (23 Nov 2026, v0.7.0)

Re-run of every check below on the Milestone 7 build, with the new WhatsApp, batch, report and admin pages added.
* **Crawler:** 1,339 page loads (founder 335, lead 320, Shaheen 319, Harika 319, accounts 46); **0 server errors,
  0 broken links.** Refusals are the intended ones (team members to admin pages; Finance to CRM and inbox).
* **Forged requests:** now 74 attempts. Adds approving a message batch (team members), sending a WhatsApp reply and
  re-assigning a conversation (Finance), editing a message template (everyone but the founder), and opening the inbox,
  integrations and e-mail outbox. **All refused, database unchanged.**
* **Browser walkthrough:** reply in a buyer thread; unknown number, reply, then one-click seller lead with the chat
  linked; batch drafted by Harika, approved and sent by Ravi (6 simulated sends, consent re-checked); reports;
  integrations; every new page at phone width (375 px) without sideways scrolling.
* Findings fixed in M7: see `docs/STATUS.md` (private link in batches, unanswerable unknown numbers, My Day title,
  batch cancel rights, clickable inbox rows).

---

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
