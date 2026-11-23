# Milestone 7.4 — smart import from Google Sheets / Excel, tested on real pilot data (23 Nov 2026, v0.7.4)

| Area | Status | Evidence |
|---|---|---|
| Smart import (Imports → Smart import) | **Built** | Upload an Excel/CSV file (every tab) or paste a Google Sheets link shared "anyone with the link". Each tab is judged: sellers, buyers (incl. "open a new salon"), salon owners asking for help, or not relevant (staff lists, dashboards, dropdown lists, instructions, templates, tabs without phones). Header-less tabs borrow a matching sibling header only when phone/id/date columns line up. |
| Understanding columns and values | **Built** | Columns recognised by meaning in any wording; advertising columns ignored. Phones in any format (two numbers in one cell → second becomes the alternate), money ("10–20 Lakhs", "under ₹20 lakhs", "40k", "2.5Lakh", "1.8" in an expenses column = ₹1.8 L), chair bands (lowest used), "Years in operation: 2024" = started 2024, floors, salon types, timelines, cities/areas (Hyderabad localities recognised; long addresses kept privately, area shortened). Remarks typed into the Email or City column become notes. |
| Only relevant people | **Built** | Left out with a reason: test/dummy rows, invalid or foreign numbers, "not interested", "wrong number", job seekers / people asking for staff, "not looking to buy", non-owners in owner campaigns. A "not interested" in one tab applies to the same person in every tab. A buyer-sheet remark like "want to sell my salon" creates a seller lead. |
| Sync | **Built** | One person = one record across tabs (merged, notes combined). People already in TeamOS: only empty fields are filled, nothing is overwritten; a new buyer/seller record is linked to a known contact. Raw cells of every row are kept with the import. |
| Workload | **Built** | New leads go to the least-busy person in the seller / buyer team; first calls are spread over working days (25 per person per day, Mon–Sat, 10:00–18:00 IST, hottest first: form submitters, buying/selling soon, budget known, most recent), 8 high-priority a day, and one summary alert per person instead of one per lead. Form submitters get contact consent recorded (they asked to be contacted); cold-call rows do not. |
| Matching with real data | **Improved** | Unknown city/area ("not given") is treated as unknown — it asks, never blocks. Pairs with under 30% of facts known are not listed on the Match Desk until more is known (the records' "Still to collect" drives the questions). |

**Pilot test on the founder's two shared workbooks** (imported into a separate local pilot database, never into the
repository): 52 seller leads (incl. 4 found in buyer call notes), 208 buyer/investor leads, 297 repeats merged,
92 rows left out with reasons, 1 existing record updated by the second workbook. Matching: 10 near matches worth
negotiating, 677 pairs needing information, 3,840 blocked for a real reason (budget below the salon's all-in cost or a
different city). Page crawl over the pilot data: 0 errors.

Found and fixed while testing on real data: amounts with "L"/"k" attached were misread; a header-less lead-ads tab
was ignored; remarks typed into City/Email columns became cities/emails; one person's "not interested" did not reach
their other rows; "City not given" placeholders blocked matches; 208 first calls were all due the next morning with
208 separate alerts; the Match Desk would have listed ~7,000 empty pairs; decorative Unicode names.

---

# Milestone 7.3 — Meta-approved templates only, real staff logins, whole-journey check (23 Nov 2026, v0.7.3)

| Area | Status | Evidence |
|---|---|---|
| Only Meta-approved templates go live | **Built** | Live sending requires the template to be APPROVED in Salon Shastra's WhatsApp account **and** its wording to match ours exactly. "Check with Meta" reads the account's templates and marks each one approved / different wording / pending / rejected / not in Meta. An admin can no longer mark a template "approved by Meta" by hand; any wording change needs Meta's approval again. In live mode staff can only pick live-ready templates. |
| Meta rules checked | **Built** | Each template shows Meta's category (Utility / Marketing), problems Meta would reject (placeholder at start/end, placeholders side by side, too long, too many placeholders, missing opt-out, promotional words in a Utility template) and a ready-to-copy "For Meta" block ({{1}}, {{2}} … and sample values). All 18 built-in texts pass. Values sent to Meta are cleaned (no line breaks). |
| Category correction | **Changed** | The "Polite follow-up — are you still exploring?" message is re-engagement, which Meta treats as MARKETING, so it now needs marketing consent like any marketing message. |
| Staff accounts | **Built** | Change my password (click your name at the top); temporary passwords must be changed at first sign-in, with a reminder banner; other devices are signed out after a change. Admin → Users → Reset password gives a one-time temporary password for anyone who forgot theirs. A staff WhatsApp number can be saved before its Meta ID exists ("Meta ID pending"). |
| Whole journey | **Tested** | One automated journey through the real screens: seller lead → call → one-click WhatsApp → customer form with photo → review → checklist → listing approval → buyer form (floor/area/parking) → criteria + funding → match (uses rent + maintenance, floor, parking) → approach → teaser → introduction → deal → close → fee → reports. |
| Demo data | **Updated** | Demo salons have floor, area, parking, maintenance, electricity, licences (a few left blank on purpose); demo buyers have parking and floor/area preferences; matches re-scored. |

QA: all automated tests pass; page crawl 1,360 pages across 5 roles with 0 errors; security probe 90 forged attempts
(now including password reset, integration settings, GO LIVE and Check with Meta) all refused.

---

# Milestone 7.2 — easier, richer forms; buyer↔seller sync; admin-configurable integrations (23 Nov 2026, v0.7.2)

| Area | Status | Evidence |
|---|---|---|
| New seller facts | **Built** | Year started, Google Maps link (private), floor, type of area, shop-front width, parking, signboard, washrooms, AC, equipment age, monthly maintenance (CAM), GST on rent, electricity, total salaries, main services, Google rating, billing software, trade licence / GST / shop & establishment. In the staff intake (new "Location & premises" and "Licences" sections) and the customer form. |
| Guided photos & more documents | **Built** | 7 photo slots (shop front, street, reception, chairs, wash area, rooms, washroom) that open the phone camera; big photos are shrunk on the phone before upload. Documents: rent agreement, rent and maintenance receipts, electricity bill, sales report, licence, equipment list. |
| Easy customer forms | **Built** | One step at a time with a progress bar; plain-language questions; tap-buttons instead of dropdowns; "I don't know / Discuss on a call / Not applicable" as one tap; rupee amounts echoed in words (45k = ₹45,000); every question optional; save and finish later. Seller form: 8 steps + photos + documents; buyer form: 7 steps + areas + salon type. |
| Same words on both sides | **Built** | Buyers are asked floor, type of area and parking with exactly the seller's options; the buyer's rent limit is "rent + maintenance". |
| Matching v0.2 | **Built** | Adds type of area, floor and customer parking; rent compared as rent + maintenance (asks the seller for maintenance if missing). A buyer's "must" on any of these blocks a mismatch; unanswered criteria never count against a salon. |
| "Still to collect" on every record | **Built** | The most important missing answers (in consultant order), photos and documents not yet received, and answers the customer marked "don't know". One button sends just those as a short form (WhatsApp or link); answers come back for review. |
| Integrations set up in the app | **Built** | Admin → Integrations: WhatsApp (account, company number, token, app secret, verify token with a generate button, webhook address), e-mail (SMTP), public address for customer links. Secrets are encrypted and never shown again; server environment values still win. "Send me a test message" and a GO LIVE switch that needs typing GO LIVE (and can always be switched back). |
| Present and new staff | **Built** | WhatsApp number per person on the Integrations page and when creating a user; Users list shows who sends from their own number. |
| Future connectors | **Listed** | Facebook/Instagram lead forms, click-to-call & call logging, Google Calendar, Google Sheets, payment links — shown as "planned"; each plugs into the same settings screen. |

Found while testing: the step Back/Next bar showed on forms that were not in step mode (fixed for every hidden
element); "Still to collect" showed the customer's wording to staff (now staff labels).

---

# Milestone 7.1 — team WhatsApp numbers + one-click messages after a call (23 Nov 2026, v0.7.1)

| Area | Status | Evidence |
|---|---|---|
| Ready message after every call | **Built** | Saving a call jumps to "Call logged — send a WhatsApp follow-up" with up to 3 messages already filled in (name, sender, area, call-back time, visit time) for that stage and outcome. One click sends. No answer → call-back request; call-back requested → confirms the agreed time; connected first call → thank-you + personal detailed form; later stages → documents checklist, proof of funds, listing live, shortlist update, visit confirmation, next steps. Not interested / wrong number / do-not-contact → nothing offered. |
| Same on every record | **Built** | A collapsed "Quick WhatsApp messages" card on every seller and buyer shows what is ready for the current stage. A record nobody has spoken to yet gets an enquiry acknowledgement or call-back request, never "thank you for speaking with me". |
| Safety | **Built** | Every one-click message goes through the same gate as any message (consent, opt-out, caps, approved template, colleague warning). The server rebuilds the message on send, so nothing in the browser can change what goes out. |
| Each person's own WhatsApp number | **Built (test-only)** | Admin → Integrations → *Team WhatsApp numbers*: number + Meta phone number ID per person. Their messages go from their number; replies go from the number the customer wrote to; a new customer writing to Harika's number lands in Harika's queue. Without a number, the company number is used. |
| Meta template sending | **Built, not run against Meta** | Live sends now use the template's Meta name and send the values as Meta's {{1}}, {{2}} … parameters in order; a template without a recorded Meta name is refused in live mode. Before this, live template sends would have gone out as plain text and been rejected by WhatsApp outside the 24-hour window. |

Found and fixed while building this: **the core send function did not check the sender's role**, so a Finance user
could have sent a WhatsApp message through a route without its own check (the security probe had only passed because
that customer had hit the daily cap). Sending now requires CRM rights at the core; regression test added.

---

# Feature status — Milestone 7 (23 Nov 2026, v0.7.0)

Legend: **Built** = implemented, database-backed and covered by automated tests · **Partial** · **Not run** · **Planned**

## Milestone 7 — WhatsApp inbox, consent, batches, digests & reports (awaiting founder review)

| Area | Status | Evidence |
|---|---|---|
| WhatsApp provider | **Built (test-only)** | Labelled mock provider is active. A Meta Cloud API adapter is written but switches on only with all four `WHATSAPP_*` values **and** `WHATSAPP_LIVE_APPROVED=1`; it has never been run against Meta (UAT-48). Every screen says "Test-only". |
| Shared team inbox | **Built** | Queues: assigned to me, unassigned, unknown numbers, all open, closed. Each conversation has one owner (hand-over with a note), links to buyer/seller records, close/reopen, unread counts on My Day. "WhatsApp conversation" button on every seller and buyer contact card. |
| Unknown numbers (UAT-43) | **Built** | A number not in the CRM (or matching two contacts) is never guessed onto a record. The team can reply inside the 24-hour window the customer opened, then press **+ New seller lead / + New buyer**: the mobile is prefilled and the chat links itself to the new record. |
| One gate for every send (UAT-23, 29, 30) | **Built** | Template must be approved and every placeholder filled; contact reachable, not do-not-contact, not opted out; service consent for service messages, separate marketing consent for marketing; max 3 messages per person per 24 h and 1 marketing message per 7 days (D-43). Free text only within 24 h of the customer's last message. |
| Opt-out | **Built** | "STOP" (and variants) records the opt-out, sends one confirmation, cancels pending batch messages, creates a task for the owner. "START" opts back in. |
| Collision guard (UAT-44) | **Built** | If a colleague messaged the same person in the last 30 minutes you see "Harika messaged this person 12 min ago" and must tick "send anyway". |
| Delivery statuses & webhooks (UAT-24, 25) | **Built** | Signature-checked webhook; each provider event stored once (repeats ignored); statuses only move forward (accepted, sent, delivered, read); failures visible. A provider timeout becomes "Uncertain — needs review" and is never resent automatically; a person marks it arrived / failed. |
| Message templates | **Built** | 10 approved-wording templates (Admin → Message templates), service vs marketing, versioned, opt-out line required. Templates with a personal link (forms, teasers) can only be sent one person at a time. |
| Reviewed batches (UAT-45) | **Built** | Anyone drafts (audience + template); a team lead / admin who did not draft it approves; eligibility is re-checked for every person at the moment of sending, so anyone who opted out after approval is suppressed. Personal-link and system templates are refused for batches. |
| Morning e-mail digest (FR-028) | **Built (held)** | Opt-in per person on My Day; one per IST day with only that person's overdue/today tasks, waiting forms and matches; preview page. No mail server is configured, so digests are kept in Admin → E-mail outbox ("held"). SMTP adapter ready. |
| Leadership reports | **Built** | Today / yesterday / week / month / custom with IST boundaries (UAT-22): leads by source, reach and time to first conversation, forms, matching & deal funnel, rejection reasons, WhatsApp volumes, inventory & demand, and a team table that values real conversations, reviewed forms and on-time follow-ups over call counts. CSV export matches the screen. |
| Integrations page | **Built** | Admin → Integrations shows exactly what is connected (all test-only now), the go-live checklist and stored webhook events. |
| Real WhatsApp / e-mail delivery | **Not run** | Needs Salon Shastra's verified Meta Business account, approved templates, a public HTTPS address and SMTP details. Not connected, by instruction. |
| PostgreSQL replay for the new SQL | **Not run** | No PostgreSQL here; SQL kept portable. |

Found and fixed during the M7 browser walkthrough and QA:
* A message from an unknown number could not be answered at all, so a new enquiry would sit until someone created the
  lead by hand and retyped the number. Now: reply within 24 h, then one-click lead creation that links the chat.
* A batch could have used the "Detailed form link" or "Confidential salon summary" template, which would have sent
  **one person's private link to everyone** in the group. Those templates are now refused for batches (test added).
* My Day's browser-tab title contained the raw HTML of the morning-summary toggle (the toggle sat inside the title
  block and was missing from the page). Fixed, plus a test that page titles are plain text.
* Any team member could cancel another person's approved batch; now only its author or a lead/admin.
* Inbox rows are clickable across the whole row (before, only the name was a link); the batch page no longer says
  "0 of 6 eligible" after sending.

Tests: 151 automated tests pass (all M1–M6 plus 17 for M7 covering UAT-22, 23, 24, 25, 29, 30, 43, 44, 45, 48).
Crawler: 1,339 pages across 5 roles, 0 errors. Forged-request probe: 74 attempts, all refused. Walked through in a real
browser as Harika (team member), Ravi (team lead) and the founder, including phone width.

---

# Milestone 6 status (23 Nov 2026, v0.6.0)

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
