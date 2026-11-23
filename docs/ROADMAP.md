# Roadmap — milestones

Each milestone ends with: what was built, what was tested (UAT IDs), what remains, what's next.

| # | Milestone | Scope | Exit test | Status |
|---|---|---|---|---|
| M1 | **Foundation + Seller management** | App, migrations, auth/sessions, capability RBAC + grants, audit, contacts/businesses/opportunities, seller quick screen + dedupe, detailed intake with provenance, all-in cost, monthly financials, SWOT, readiness score, stage rules, checklist + approval + re-review, tasks, My Day, Team board, search, field dictionary, synthetic seed | UAT-01,03,04,06,07,08,20,21,27,28,33,34,46 (+ restricted-field masking) | **Done** |
| M2 | **Production hardening** | PostgreSQL verification, VPS runbook, backups + restore drill (UAT-26), MFA for admins, job worker + SLA sweep, structured logging, health check, CSV/XLSX import (UAT-03) | Restore drill passes; import duplicates flagged | **Done** (VPS deployment pending) |
| M3 | **Buyer / investor management** | Buyer mandate model (hard all-in ceiling, ranked localities + exclusions, must/prefer/flexible/avoid per criterion, funding status reviewer-only), buyer funnel, seriousness score (§7.3), "new salon" need type | UAT-02,05,09,31 | **Done** |
| M4 | **Forms & data requests** | Versioned form templates mapped to canonical fields, public quick links + personal expiring deep links, drafts/resume, conditional logic, consent capture, reviewer inbox, targeted missing-field requests, secure uploads | UAT-41(mock),42 (UAT-47 moved to M6 — it is about deal fees) | **Done** |
| M5 | **Matching engine + Match Desk** | Rules catalogue v0.1, gates/fit/coverage/readiness, bidirectional ranking, near-match queue, next questions, match subtasks with separate owners, suppression on rejection, incremental re-score jobs | UAT-10–14,35–39,49 + PRD §8.7 examples 1–10 | **Done** |
| M6 | **Deals, teasers & admin finance** | Teaser versions + content consent, introduction approval gate, visits/offers/diligence/handover, closed-lost reasons, fee agreements/invoices/receipts (admin-only) | UAT-15,17,18,19,47,50 (UAT-47 from M4) | **Done** |
| M7 | **WhatsApp + notifications + reporting** | Mock provider → Meta Cloud API adapter, team inbox, templates, webhooks, opt-out, batch eligibility, email digests, leadership dashboards & exports | UAT-22,23,24,25,29,30,43,44,45,48 | **Done, test-only provider** (awaiting founder review; going live needs Meta assets) |
| M8 | **Measured improvements** | Workload balancing (away + hand-over), stuck-record detection, outcome-tuned match weights (founder-approved), Telugu customer forms, drafted summaries with human confirmation, simpler menu | Pilot metrics | **Done** (awaiting founder review) |

## Ten improvements proposed beyond the PRD (ranked by value ÷ effort)

1. **Next-action enforcement at the point of completion** (built in M1) — you can't close the last task on a live record without the next one. Kills "forgotten lead" at the source.
2. **All-in cost as a first-class computed fact** (built) — buyers compared on total cash, never asking price alone; incomplete totals are labelled.
3. **"Next question" generator** (M5) — every blocked/uncertain match emits the single question that would unblock it, routed to the right side's owner as a task.
4. **Targeted micro-forms** (M4) — send a 2-question link ("lease expiry? landlord transfer?") instead of re-sending the whole form.
5. **Contact collision guard** (built in M7) — show "Harika messaged this person 40 min ago" before anyone calls/sends.
6. **Material-change re-review** (built) — approved listings automatically return to review when price/lease/format changes.
7. **Data-quality score by source and by person** (M7) — measures corrected facts and evidence gathered, not call volume.
8. **Near-match rescue queue** (M5) — gaps that are explicitly negotiable (budget within seller's volunteered range, adjacent locality accepted) surfaced as a weekly worklist.
9. **Stalled-opportunity detector** (M7) — no stage movement in N days for the stage's typical duration → team-lead queue, not a notification storm.
10. **Outcome learning loop** (M8) — structured rejection reasons feed weight-tuning proposals the founder approves; no silent model drift.
