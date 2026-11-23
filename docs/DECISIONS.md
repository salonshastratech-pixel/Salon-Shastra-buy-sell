# Decision register

Unapproved proposals are implemented as **configurable defaults**, never as hard business rules (PRD §17).

| ID | Decision | Current implementation / proposal | Owner | Status |
|---|---|---|---|---|
| D-01 | Scope | Seller + buyer exchange first; other verticals parked | Founder | Assumed per PRD |
| D-02 | Legal entity for fees | Not implemented | Founder / accountant | **Open** |
| D-03 | Seller fee rules | Not implemented (no defaults) | Founder | **Open** |
| D-04 | Buyer fee rules | Not implemented | Founder | **Open** |
| D-05 | GST / invoicing | Not implemented | Accountant | **Open** |
| D-06 | Approval authority | Listing approval = Super Admin; can be granted to a team lead via `listing.approve` grant | Founder | Proposed |
| D-07 | Role matrix | PRD §3.2 defaults in `permissions.py` (team members cannot reassign others' work; can create tasks for colleagues) | Founder | Proposed |
| D-08 | Qualification scores | Seller readiness rubric §7.4 (20/15/25/20/10/10), bands 80/60 | Founder / PM | Proposed |
| D-09 | Match scoring | Families A–J weights §8.2 as starting hypothesis | Founder / PM | Proposed (M5) |
| D-10 | SLAs | First contact due 15 min after creation (business hours not yet modelled) | Founder / team lead | Proposed |
| D-11 | Consent & disclosure | Consent ledger built; teaser field list not yet defined | Founder / legal | **Open** |
| D-12 | Restricted seller fields | Private sale-reason note, exact address, franchise brand, lowest acceptable price | Founder | Proposed |
| D-13 | Hosting | Hostinger KVM 2 VPS + PostgreSQL + off-site encrypted backups | PM / founder | Proposed |
| D-14 | WhatsApp | Meta Cloud API, Salon Shastra-owned WABA and number(s) | Founder | **Blocked on Meta assets** |
| D-15 | Brand | Tokens taken from salonshastra.com CSS (`#c04000`, `#893815`, `#ffe3d5`, Montserrat/Open Sans, 16px radius); logo from site | Founder / marketing | Adopted from website — confirm |
| D-16 | Retention / DPDP | Not implemented; needs counsel review before launch | Legal | **Open** |
| D-17 | Legacy data import | Import tool built; imported contacts get **no** contact consent until confirmed. Which spreadsheets may be imported? | Founder / ops | **Open** |
| D-18 | MFA scope | Mandatory for Super Admin + Finance; optional for others | Founder | Proposed |
| D-19 | Backup targets | Nightly encrypted, 14 days local + off-site; RPO 24 h, RTO 4 h; monthly restore drill | Founder / PM | Proposed |
| D-20 | Overdue escalation | Assignee alerted once when overdue; team leads after 24 h | Team lead | Proposed |

## Assumptions made in M1

* "Unsure" and "seek help" sellers can be stored and worked but cannot reach *Basic qualified* (PRD §6.2).
* The primary owner may see restricted fields they collected; nobody else without a grant.
* Calls with outcome *no answer / busy / switched off* auto-advance *New → Contact attempted*; *connected* advances to *Connected*. Later stages are always manual.
* Duplicate resolution records a decision only; true merge tooling arrives with imports (M2).

## Added in Milestone 3 (proposals — configurable, pending founder approval)

| ID | Decision | Current implementation / proposal | Owner | Status |
|---|---|---|---|---|
| D-21 | Buyer seriousness score | PRD §7.3 rubric 15/20/25/15/15/10, bands 80/60. Missing answers score 0; funding-evidence points (10/15) only after a reviewer marks funding staff-reviewed/verified. Advisory only. | Founder / PM | Proposed |
| D-22 | Who sees buyer funding proof | **The whole team sees it by default**: funding evidence notes and uploaded proof of funds are visible to everyone with access to buyer records. Finance specialists (no CRM access) only via a per-case `buyer.view_funding` grant. *Recording* the funding review stays reviewer-only (team lead / admin). Deliberately broader than PRD §3.2's default. | Founder | **Decided 23 Nov 2026** |
| D-23 | Who qualifies a buyer to shortlist | Requires `evidence.review` (team lead / admin), funding staff-reviewed or verified, reviewer-confirmed criteria that have not changed since, ≥1 ranked locality, service-contact consent. | Founder | Proposed |
| D-24 | "Open a new salon" buyers | Separate need type; can be worked and nurtured but never reaches *Qualified to shortlist* (so never matched to resale listings) unless the need type is changed with the buyer's confirmation. | Founder | Proposed |
| D-25 | Default buyer routing | Respondent submissions go to the first active buyer-team member, else a Super Admin, with a review task. Staff-created leads default to the creator. | Team lead | Proposed |
| D-26 | Buyer stages after shortlisting | *Shortlist shared*, *Viewing / offer*, *Transaction*, *Closed won* are shown but blocked with "arrives in M5/M6" — not simulated. | PM | Adopted |

## Assumptions made in M3

* UAT-05 ("buyer and consulting lead") is exercised as **one contact with a seller opportunity and two independent buyer mandates**: consulting is a parked vertical (D-01) with no opportunity type yet. The model already keeps any number of opportunities per contact.
* UAT-02 ("new buyer from public form") is implemented as the server-side submission path (`buyers.submit_respondent_form`) with routing, review task, buyer-declared provenance and privileged-field stripping. The public link/page itself is Milestone 4 (forms).
* Opportunity reference numbers share one sequence: sellers `SL-xxxxx`, buyers `BY-xxxxx`.

## Added in Milestone 4 (proposals — configurable, pending founder approval)

| ID | Decision | Current implementation / proposal | Owner | Status |
|---|---|---|---|---|
| D-27 | Link expiry | Detailed form links 14 days, targeted requests 7 days; one live detailed link per record (a new one revokes the old). | Founder / team lead | Proposed |
| D-28 | Chasing | One internal chase task 48 h after sending if not submitted. No automatic customer reminders until the real WhatsApp provider (M7). | Team lead | Proposed |
| D-29 | Who reviews form answers | Any team member with edit rights (PRD §3.2 "Review form submissions: Team member Yes"). Funding/qualification stay reviewer-only. | Founder | Proposed |
| D-30 | Public form consent | Submitting a quick form requires ticking contact consent (`public-form-consent-v0.1`). Criteria-sharing consent is optional and separate. Current wording adopted as final — **no legal review** (founder decision). | Founder | **Decided 23 Nov 2026** |
| D-31 | Public form abuse limits | Signed 2-hour form token, honeypot, max 5 submissions per IP per hour. | PM | Proposed |
| D-32 | Documents | PDF/PNG/JPG up to 5 MB, encrypted at rest. Seller documents: owner, approvers, grants. Buyer documents: whole team (D-22), finance by grant. Retention period not set (D-16). | Founder / legal | Proposed |
| D-33 | Prefilling personal forms | Detailed forms show the record's current answers so the customer corrects rather than retypes — except confidential seller fields (address, lowest price, private reason). | Founder | Proposed |

## Added in Milestone 5 (proposals — configurable, pending founder approval)

| ID | Decision | Current implementation / proposal | Owner | Status |
|---|---|---|---|---|
| D-09 | Match scoring | Implemented as `match-rules v0.1`: PRD §8.2 family weights; 23 criteria (see `teamos/domain/matching.py`). Calibrate after the first real outcomes. | Founder / PM | Proposed (built) |
| D-34 | Near-match budget gap | A salon over the buyer's all-in ceiling is a *near match* only if the seller said the price is negotiable and the gap is ≤ ₹3 L or ≤ 10% of the ceiling — or it fits within the seller's volunteered lowest price. Otherwise it is blocked. | Founder | Proposed |
| D-35 | Who approves "approach" | Team lead / admin (`evidence.review`), only when: eligible, listing approved, criteria confirmed, funding reviewed, contact + sharing consent from both sides. | Founder | Proposed |
| D-36 | Suppression | Declined / not suitable needs a reason; resurfaces only on changed facts or a revisit date. | Team lead | Proposed |
| D-37 | Strong-match alert | One internal alert to the buyer and salon owners when a match first reaches fit ≥ 70 with ≥ 60% known (ready or near). Never sent to customers. | Team lead | Proposed |
| D-38 | City mismatch | Different city is a hard conflict unless the buyer said they would relocate (then a partial fit). | Founder | Proposed |
