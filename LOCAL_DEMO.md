# Local demo environment (synthetic data only)

URL: http://localhost:8000  — dev server, bound to 127.0.0.1 only, SQLite file `instance/teamos-demo.db`.
No WhatsApp or other live integrations exist in this build; nothing is deployed.

## Start it again
From this folder (`app\salon-shastra-teamos`):
```powershell
C:\Users\user\.venvs\teamos-m2\Scripts\python.exe run_local.py
```
(`run_local.py` loads `.env` and refuses to start if APP_ENV=production.)
The virtualenv lives at `C:\Users\user\.venvs\teamos-m2` because this folder's path is too long for pip on Windows.

## Demo logins — password `Demo-Shastra-2026` (all synthetic, badged "demo")
| Email | Role |
|---|---|
| founder@demo.salonshastra.test | Super Admin |
| lead@demo.salonshastra.test | Team lead / reviewer (can import) |
| shaheen@demo.salonshastra.test | Team member (seller team) |
| harika@demo.salonshastra.test | Team member (buyer team) |
| accounts@demo.salonshastra.test | Finance specialist |

Two-step sign-in is switched off for the demo (`MFA_REQUIRED_ROLES=` empty in `.env`). Enrol any user at /mfa/setup to try it.

## Milestone 6 — deals & finance
* **Deals → DL-00001** (Arjun ↔ Glow Studio Unisex): visit done, buyer offered ₹19 L, seller countered ₹21 L. Try
  accepting the counter, ticking the diligence list, adding a handover date and closing (as `founder@`).
* Glow Studio's *Teaser* tab shows the approved anonymous summary; the match page shows the teaser share, both
  consents and the approved introduction.
* **Admin Finance** (`founder@` or `accounts@`): an illustrative 5% seller success fee that becomes earned only when
  the deal closes. Team members cannot open it and never see amounts.

## Milestone 5 — Match Desk
* **Match Desk** (left menu): queues of buyer↔salon matches; open one for the side-by-side comparison.
* Try BY-00009 Arjun ↔ SL-00001 Glow Studio Unisex: fit 99, fully known — but *awaiting consent* (the seller has not
  agreed to share details), so it cannot be approved to approach yet.
* Lakshmi ↔ Glow Studio is a near match (₹2.8 L over) with buyer- and seller-side tasks already split.
* Every seller/buyer page has a *Biggest unlock* card and a *Matches* tab.
* My Day shows matching tiles; the Team Command Center shows matches needing attention.

## Milestone 4 — forms & requests
* **Forms & Requests** (left menu): review inbox, open links, mock message log, public form links.
* Public quick forms (no login): http://localhost:8000/f/seller and http://localhost:8000/f/buyer
* On any seller or buyer page, use *Forms & documents* to send the detailed form or request specific missing info.
  The personal link is shown once; open it in the browser to fill it in as the customer would.
* WhatsApp sending is a labelled **mock** — nothing leaves this computer.
* Uploaded files are encrypted with `FILES_KEY` from `.env` and stored in `instance\uploads`.

## Milestone 3 — buyers
Open **Buyers / Investors** (left menu). Best roles to try:
* `harika@` — buyer-team member: create a buyer, edit the mandate (Requirements tab), rank localities, set Must/Prefer/Avoid.
* `lead@` — reviewer: *Qualification* tab → record funding review, confirm criteria, move a buyer to *Qualified to shortlist*.
* `shaheen@` — seller-team member opening a buyer record: sees qualification and preferences, not funding evidence.
* `accounts@` — finance: no access to buyers at all.

Records created during the review walkthrough are prefixed `DEMO` (SL-00008, a few tasks, one extra user). Records you add
through the UI are not auto-badged "synthetic" — only seeded ones are.

## Other commands
```powershell
C:\Users\user\.venvs\teamos-m2\Scripts\python.exe -m pytest -q
```
Reset demo data: stop the server, delete `instance\teamos-demo.db*`, then run `manage.py migrate` and `manage.py seed-demo`
with the `.env` values loaded.
