# Decision register

Unapproved proposals are implemented as **configurable defaults**, never as hard business rules (PRD §17).

| ID | Decision | Current implementation / proposal | Owner | Status |
|---|---|---|---|---|
| D-01 | Scope | Seller + buyer exchange first; other verticals parked | Founder | Assumed per PRD |
| D-02 | Legal entity for fees | Not implemented | Founder / accountant | **Open** |
| D-03 | Seller fee rules | Not implemented (no defaults) | Founder | **Open** |
| D-04 | Buyer fee rules | Not implemented | Founder | **Open** |
| D-05 | GST / invoicing | Not implemented | Accountant | **Open** |
| D-06 | Approval authority | Listing approval = Super Admin; can be granted to a team lead via `listing.approve` grant | Founder | **Approved 24 Nov 2026** |
| D-07 | Role matrix | PRD §3.2 defaults in `permissions.py` (team members cannot reassign others' work; can create tasks for colleagues) | Founder | **Approved 24 Nov 2026** |
| D-08 | Qualification scores | Seller readiness rubric §7.4 (20/15/25/20/10/10), bands 80/60 | Founder / PM | **Approved 24 Nov 2026** |
| D-09 | Match scoring | Families A–J weights §8.2 as starting hypothesis | Founder / PM | **Approved 23 Nov 2026** (see M5 section) |
| D-10 | SLAs | First contact due 15 min after creation (business hours not yet modelled) | Founder / team lead | **Approved 24 Nov 2026** |
| D-11 | Consent & disclosure | Consent ledger built; teaser field list not yet defined | Founder / legal | **Open** |
| D-12 | Restricted seller fields | Private sale-reason note, exact address, franchise brand, lowest acceptable price | Founder | **Approved 24 Nov 2026** |
| D-13 | Hosting | Hostinger KVM 2 VPS + PostgreSQL + off-site encrypted backups | PM / founder | **Approved 24 Nov 2026** |
| D-14 | WhatsApp | Meta Cloud API, Salon Shastra-owned WABA and number(s) | Founder | **Blocked on Meta assets** |
| D-15 | Brand | Tokens taken from salonshastra.com CSS (`#c04000`, `#893815`, `#ffe3d5`, Montserrat/Open Sans, 16px radius); logo from site | Founder / marketing | Adopted from website — confirm |
| D-16 | Retention / DPDP | Not implemented; needs counsel review before launch | Legal | **Open** |
| D-17 | Legacy data import | Import tool built; imported contacts get **no** contact consent until confirmed. Which spreadsheets may be imported? | Founder / ops | **Open** |
| D-18 | MFA scope | Mandatory for Super Admin + Finance; optional for others | Founder | **Approved 24 Nov 2026** |
| D-19 | Backup targets | Nightly encrypted, 14 days local + off-site; RPO 24 h, RTO 4 h; monthly restore drill | Founder / PM | **Approved 24 Nov 2026** |
| D-20 | Overdue escalation | Assignee alerted once when overdue; team leads after 24 h | Team lead | **Approved 24 Nov 2026** |
## Assumptions made in M1

* "Unsure" and "seek help" sellers can be stored and worked but cannot reach *Basic qualified* (PRD §6.2).
* The primary owner may see restricted fields they collected; nobody else without a grant.
* Calls with outcome *no answer / busy / switched off* auto-advance *New → Contact attempted*; *connected* advances to *Connected*. Later stages are always manual.
* Duplicate resolution records a decision only; true merge tooling arrives with imports (M2).

## Added in Milestone 3 (proposals — configurable, pending founder approval)

| ID | Decision | Current implementation / proposal | Owner | Status |
|---|---|---|---|---|
| D-21 | Buyer seriousness score | PRD §7.3 rubric 15/20/25/15/15/10, bands 80/60. Missing answers score 0; funding-evidence points (10/15) only after a reviewer marks funding staff-reviewed/verified. Advisory only. | Founder / PM | **Approved 24 Nov 2026** |
| D-22 | Who sees buyer funding proof | **The whole team sees it by default**: funding evidence notes and uploaded proof of funds are visible to everyone with access to buyer records. Finance specialists (no CRM access) only via a per-case `buyer.view_funding` grant. *Recording* the funding review stays reviewer-only (team lead / admin). Deliberately broader than PRD §3.2's default. | Founder | **Decided 23 Nov 2026** |
| D-23 | Who qualifies a buyer to shortlist | Requires `evidence.review` (team lead / admin), funding staff-reviewed or verified, reviewer-confirmed criteria that have not changed since, ≥1 ranked locality, service-contact consent. | Founder | **Approved 24 Nov 2026** |
| D-24 | "Open a new salon" buyers | Separate need type; can be worked and nurtured but never reaches *Qualified to shortlist* (so never matched to resale listings) unless the need type is changed with the buyer's confirmation. | Founder | **Approved 24 Nov 2026** |
| D-25 | Default buyer routing | Respondent submissions go to the first active buyer-team member, else a Super Admin, with a review task. Staff-created leads default to the creator. | Team lead | **Approved 24 Nov 2026** |
| D-26 | Buyer stages after shortlisting | *Shortlist shared*, *Viewing / offer*, *Transaction*, *Closed won* are shown but blocked with "arrives in M5/M6" — not simulated. | PM | Adopted |

## Assumptions made in M3

* UAT-05 ("buyer and consulting lead") is exercised as **one contact with a seller opportunity and two independent buyer mandates**: consulting is a parked vertical (D-01) with no opportunity type yet. The model already keeps any number of opportunities per contact.
* UAT-02 ("new buyer from public form") is implemented as the server-side submission path (`buyers.submit_respondent_form`) with routing, review task, buyer-declared provenance and privileged-field stripping. The public link/page itself is Milestone 4 (forms).
* Opportunity reference numbers share one sequence: sellers `SL-xxxxx`, buyers `BY-xxxxx`.

## Added in Milestone 4 (proposals — configurable, pending founder approval)

| ID | Decision | Current implementation / proposal | Owner | Status |
|---|---|---|---|---|
| D-27 | Link expiry | Detailed form links 14 days, targeted requests 7 days; one live detailed link per record (a new one revokes the old). | Founder / team lead | **Approved 24 Nov 2026** |
| D-28 | Chasing | One internal chase task 48 h after sending if not submitted. No automatic customer reminders until the real WhatsApp provider (M7). | Team lead | **Approved 24 Nov 2026** |
| D-29 | Who reviews form answers | Any team member with edit rights (PRD §3.2 "Review form submissions: Team member Yes"). Funding/qualification stay reviewer-only. | Founder | **Approved 24 Nov 2026** |
| D-30 | Public form consent | Submitting a quick form requires ticking contact consent (`public-form-consent-v0.1`). Criteria-sharing consent is optional and separate. Current wording adopted as final — **no legal review** (founder decision). | Founder | **Decided 23 Nov 2026** |
| D-31 | Public form abuse limits | Signed 2-hour form token, honeypot, max 5 submissions per IP per hour. | PM | **Approved 24 Nov 2026** |
| D-32 | Documents | PDF/PNG/JPG up to 5 MB, encrypted at rest. Seller documents: owner, approvers, grants. Buyer documents: whole team (D-22), finance by grant. Retention period not set (D-16). | Founder / legal | **Approved 24 Nov 2026** |
| D-33 | Prefilling personal forms | Detailed forms show the record's current answers so the customer corrects rather than retypes — except confidential seller fields (address, lowest price, private reason). | Founder | **Approved 24 Nov 2026** |
## Added in Milestone 5 (proposals — configurable, pending founder approval)

| ID | Decision | Current implementation / proposal | Owner | Status |
|---|---|---|---|---|
| D-09 | Match scoring | Implemented as `match-rules v0.1`: PRD §8.2 family weights; 23 criteria (see `teamos/domain/matching.py`). Calibrate after the first real outcomes. | Founder | **Approved 23 Nov 2026** |
| D-34 | Near-match budget gap | A salon over the buyer's all-in ceiling is a *near match* only if the seller said the price is negotiable and the gap is ≤ ₹3 L or ≤ 10% of the ceiling — or it fits within the seller's volunteered lowest price. Otherwise it is blocked. Founder guidance: show the *nearest* chances so the team is never stuck, but never assume a concession. | Founder | **Approved 23 Nov 2026** |
| D-35 | Who approves "approach" | Team lead / admin (`evidence.review`), only when: eligible, listing approved, criteria confirmed, funding reviewed, contact + sharing consent from both sides. | Founder | **Approved 24 Nov 2026** |
| D-36 | Suppression | Declined / not suitable needs a reason; resurfaces only on changed facts or a revisit date. | Team lead | **Approved 24 Nov 2026** |
| D-37 | Strong-match alert | One internal alert to the buyer and salon owners when a match first reaches fit ≥ 70 with ≥ 60% known (ready or near). Never sent to customers. | Team lead | **Approved 24 Nov 2026** |
| D-38 | City mismatch | Different city is a hard conflict unless the buyer said they would relocate (then a partial fit). | Founder | **Approved 23 Nov 2026** |

## Added in Milestone 6 (proposals — configurable, pending founder approval)

| ID | Decision | Current implementation / proposal | Owner | Status |
|---|---|---|---|---|
| D-11 | Teaser fields before NDA | Whitelist in `teamos/domain/teasers.py` (`TEASER_FIELDS`); money and size shown as bands; the seller approves each version's items. No NDA step is built — say if one is required before identity release. | Founder | **Needs decision** (proposal built) |
| D-39 | Who approves introductions | Super Admin (or anyone granted `listing.approve`), only with both sides' identity consent and a shared teaser. | Founder | **Approved 24 Nov 2026** |
| D-40 | Finance specialist rights | View and edit fee agreements, invoices, receipts and expenses; no access to the CRM deal room (sees deal references only). | Founder | **Approved 24 Nov 2026** |
| D-41 | Closing a deal as won | Approver only; accepted offer, all diligence items reviewed / not applicable, handover date recorded. | Founder | **Approved 24 Nov 2026** |
| D-42 | Fee earning | Each agreement states when it is earned (signing / agreement / handover / closing / manual). The demo's "5% seller success fee on closing" is an illustration only, not a policy (D-03, D-04 still open). | Founder / accountant | **Approved 24 Nov 2026** |
## Added in Milestone 7 (proposals — configurable, pending founder approval)

| ID | Decision | Current implementation / proposal | Owner | Status |
|---|---|---|---|---|
| D-43 | Message frequency caps | Max 3 WhatsApp messages per person per 24 h; max 1 marketing message per person per 7 days. Constants in `teamos/domain/inbox.py`. | Founder | **Approved 24 Nov 2026** |
| D-44 | Collision guard | Warn (not block) if a colleague messaged the same person in the last 30 minutes; the sender must tick "send anyway". | Team lead | **Approved 24 Nov 2026** |
| D-45 | Batch approval | Anyone may draft; a team lead / admin who did not draft it approves and sends; consent re-checked per person at send time. Personal-link templates never go in batches. | Founder | **Approved 24 Nov 2026** |
| D-46 | Replying to unknown numbers | Free-text reply allowed within the 24-hour window the person opened (never after STOP); templates need a linked record with consent. | Founder | **Approved 24 Nov 2026** |
| D-47 | Morning digest | Opt-in per person, 08:00 IST, only the recipient's own work; no phone numbers or documents in e-mail. | Founder | **Approved 24 Nov 2026** |
| D-48 | Going live on WhatsApp | Requires a Salon Shastra-owned verified Meta Business account, each template approved by Meta, a public HTTPS webhook, and `WHATSAPP_LIVE_APPROVED=1` set after a staff-phone test. | Founder | **Needs decision** (not connected) |
| D-49 | Team WhatsApp numbers | Each person may send from their own WhatsApp Business number, registered in Salon Shastra's own Meta business account (never a personal account). Customers writing to that number go to that person's queue. | Founder | **Approved 24 Nov 2026** |
| D-50 | One-click messages after calls | Wording per stage and call outcome as listed in `teamos/domain/quick_messages.py` and Admin → Message templates; all 8 new texts need Meta approval before live use. | Founder | Proposed — please review the wording |
| D-51 | Matching v0.2 | Adds type of area (location family), floor and customer parking (property family); rent compared as rent + maintenance. Small weights; unanswered criteria are left out of the score. | Founder | **Approved 24 Nov 2026** |
| D-52 | What the team must collect | "Still to collect" order in `teamos/domain/collect.py` (price, rent, maintenance, deposit, lease, floor, area, parking, size, chairs, format, sales, costs, staff, authority …) + 7 photos + 4 documents. | Founder / team lead | Proposed — please review the list |
| D-53 | Integration settings | Admins enter WhatsApp / e-mail settings in the app; secrets encrypted with the server key; server environment values override. Only Super Admin can change them or switch WhatsApp live. | Founder | **Approved 24 Nov 2026** |
| D-54 | Meta-approved templates only | Live WhatsApp uses only templates Meta has approved with identical wording, confirmed by "Check with Meta"; no manual override. | Founder | **Decided 23 Nov 2026** (founder instruction) |
| D-55 | Re-engagement message category | "Polite follow-up" is MARKETING (Meta's rules) and needs marketing consent. | Founder | **Approved 24 Nov 2026** |
| D-56 | Staff passwords | Temporary passwords (set by an admin) must be changed at first sign-in; minimum 10 characters with upper/lower case and a number. | Founder | **Approved 24 Nov 2026** |
| D-57 | First calls after an import | Spread at 25 per person per working day (Mon–Sat), hottest first, 8 high-priority per day; one summary alert. | Team lead | **Approved 24 Nov 2026** |
| D-58 | Match Desk noise | A buyer–salon pair is listed only once ≥ 30% of the facts are known on both sides (or it is a ready/near match). | Founder | **Approved 24 Nov 2026** |
| D-59 | Consent for imported leads | People who submitted a form or lead-ad (timestamped responses) get service-contact consent recorded with the sheet as source; call-log rows get none until consent is captured on a call. | Founder | **Approved 24 Nov 2026** |
| D-60 | Pilot owners vs sellers | Salon owners from growth-program / "struggling salons" campaigns are imported as owners seeking help (not confirmed sellers) and are not matched to buyers. | Founder | **Approved 24 Nov 2026** |
| D-61 | When is a record "not moving"? | Usual days per stage (e.g. new 2, form sent 7, active matching 30, due diligence 21) and no customer contact for that long; tune from pilot data. | Team lead | **Approved 24 Nov 2026** |
| D-62 | Changing match weights | System proposes from declined good-fit matches (≥5 and ≥25%), +25% per step, never above 2× the start; founder approves; reset any time. | Founder | **Approved 24 Nov 2026** |
| D-63 | Telugu wording | First-draft translation of all customer form text; a Telugu-speaking team member reviews `teamos/i18n.py` before customers see it. | Founder | **Approved 24 Nov 2026** |
| D-64 | AI-drafted summaries | Off by default. When on: Claude receives only non-identifying facts (no names, phones, e-mails, addresses, map links, private notes); a person always edits and saves. | Founder | **Approved 24 Nov 2026** |
| D-65 | What a calendar subscribe link may show | Kind of task, reference code and area only; Google / phone calendars keep copies outside TeamOS, so no names, numbers or task titles. | Founder | **Approved 24 Nov 2026** |
| D-66 | Going live with lead ads | Stays test-only until the founder approves; then the Facebook page token is added and "yes" typed in Integrations. | Founder | **Approved 24 Nov 2026** |

## Added in Milestone 10 (proposals — pending founder approval)

| ID | Decision | Current implementation / proposal | Owner | Status |
|---|---|---|---|---|
| D-67 | Pausing automations | Any staff can see every automation; only admins pause. Paused checks are skipped; incoming lead ads wait and are brought in when resumed; retrying failed WhatsApp updates can never be paused. "Pause everything" also holds bulk sends. Every change is audited. | Founder | Proposed |
| D-68 | Quiet hours | No bulk WhatsApp 9 pm – 8 am IST (`QUIET_HOURS`, default `21-8`); one-to-one messages are not affected; admins can switch it off. | Founder | Proposed |
| D-69 | Customer progress link | Shows first name, reference, journey step, what we still need (plain wording), contact's first name and next contact date. Never prices, notes, scores, other parties, documents. 60 days; one live link per record; closed / do-not-contact records show nothing. | Founder | Proposed |
| D-70 | TeamOS on phones | Installable from the browser (no app store). Only the app's look and an offline notice are stored on the phone; customer pages are never cached. | Founder | Proposed |
| D-71 | Database hosting (Supabase) | Supabase project in Mumbai on the free plan; TeamOS connects directly (public Supabase API locked off). Only synthetic demo data until the founder approves moving pilot data with `scripts/copy_to_postgres.py --allow-real`. | Founder | Proposed |

## Added in Milestone 11 — pilot program (proposals — pending founder approval)

| ID | Decision | Current implementation / proposal | Owner | Status |
|---|---|---|---|---|
| D-72 | Naming | "Sellers" is shown as **Salon Owners**; "wants to sell" is one of the help areas. Investors stay under Buyers & investors. | Founder | Proposed |
| D-73 | SWOT report wording | Drafted by fixed rules from the answers (version `pilot-swot rules v1`); staff must approve before it can be sent. Scores are a guide, not an audit. | Founder | Proposed |
| D-74 | Consultation fees | Office ₹3,000 / salon visit ₹5,000 within Hyderabad; ₹10,000 outside Hyderabad; free video by Vikas with every paid consultation. Recorded in TeamOS; no online payment yet. | Founder | Proposed |
| D-75 | Booking alerts | Bookings alert the chosen consultant user (in-app + e-mail) and the record owner. Times within the next 2 hours can't be booked online. | Founder | Proposed |
| D-76 | Demo password | All synthetic demo logins use `Demo@123` (founder's request). Real staff accounts keep their own passwords. | Founder | Proposed |
| D-77 | ChairPe in the pilot program | Prices and trial terms copied from chairpe.com on 24 Nov 2026 (TeamOS does not take ChairPe payments; the team records plan and months). Plan suggestion: Basic for 1-2 chairs and 2 or fewer staff, else Elite. Trial reminder 2 days before the 14-day trial ends. | Founder | Proposed |

## Added in Milestone 12 (proposals — pending founder approval)

| ID | Decision | Current implementation / proposal | Owner | Status |
|---|---|---|---|---|
| D-78 | Default working hours | Mon–Sat 10:00–19:00 IST, Sunday off, until each person sets their own. | Founder | Proposed |
| D-79 | Leave and new leads | Leave, sick leave and public holidays stop new leads that day; half day, field work and training do not. Anyone sets their own; team leads can set anyone's. | Founder | Proposed |
| D-80 | Activity tracking | Team activity shows each person's logged calls, messages, tasks, new records and stage moves to everyone with CRM access (no pay or finance data). | Founder | Proposed |

## Added in Milestone 13 — investor journey (proposals — pending founder approval)

| ID | Decision | Current implementation / proposal | Owner | Status |
|---|---|---|---|---|
| D-81 | Screening thresholds | Pursue ≥ 65/100, Nurture 40–64, Not now < 40; no funding arranged + no running money is always Not now; just exploring / support only is at most Nurture. | Founder | Proposed |
| D-82 | Registration fee | ₹5,000, recorded by staff after the office visit (or waived); never shown on the investor form or report. | Founder | Proposed |
| D-83 | Readiness verdict rules | Not yet when running money is short and the funding is fragile, money is not arranged, or they cannot afford any loss; Ready when every area is strong; otherwise Ready with support. Money split guidance: keep about 30% for 6 months of running costs. Staff can change the verdict before approving. | Founder | Proposed |
| D-84 | Acting on remarks | On import, a remark with a time ("repu evening") moves the first call to that time (high priority); didn't answer / busy / switched off → "try again"; not interested / wrong number → low-priority task to confirm and close (nothing is closed automatically). The original remark is always kept. | Founder | Proposed |
| D-85 | Simple access | Team members (and team leads) can do everything the admin can except Finance; only adding/removing people and integrations & secrets stay with the admin. Finance role sees Finance only. | Founder (24 Nov 2026) | Decided |
| D-86 | Forms fill records | Pilot and Investor form answers fill only EMPTY fields on the salon-owner / buyer record (source pilot_form / investor_form); what the team typed is never overwritten. Owners who did not ask to sell are kept out of buyer matching. | Founder | Proposed |
| D-87 | Connected sheets | Google Sheets shared "Anyone with the link → Viewer" are checked hourly; new people become an import preview that a person approves. No Google account or key is stored in TeamOS. | Founder | Proposed |
| D-88 | One-tap WhatsApp | Until the WhatsApp Business API is live, the WhatsApp button opens the phone/web WhatsApp with a greeting and notes it on the record. | Founder | Proposed |
