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

## Milestone 7.2 — easy forms, sync and integrations
* Open any seller (e.g. **SL-00004**): the **Still to collect** card lists what is missing; choose *Create a link to
  share myself* and **Send these as a short form**, then open the link on a phone-sized window: one step at a time,
  tap-buttons, "I don't know" in one tap, rupees shown in words, photo slots that open the camera.
* Send the full *Detailed form* to a seller or buyer from the record to see all 8 / 7 steps.
* As `founder@`, **Admin → Integrations**: WhatsApp, e-mail and customer-link settings, team numbers, the GO LIVE
  switch (needs typing GO LIVE) and the planned connectors. **Admin → Users → Add team member** can take a WhatsApp
  number straight away.

## Milestone 7.1 — one-click messages after a call
* Sign in as `shaheen@`, open **SL-00004 Urban Trim**, log a call with outcome *No answer*: the page jumps to a ready
  call-back message. Press **Send on WhatsApp**. Try *Connected* on another new lead: a thank-you and the personal
  detailed form are ready.
* As `founder@`, **Admin → Integrations → Team WhatsApp numbers**: Shaheen has a synthetic test number, so his
  messages show "From Shaheen's WhatsApp". In the inbox simulator, choose *Written to: Shaheen's number* to see a
  new customer land in his queue.

## Milestone 7 — WhatsApp inbox & reports (everything is a labelled TEST-ONLY simulation)
* **WhatsApp Inbox** (left menu, as `harika@`): Arjun's thread. Reply to him (allowed because he wrote in the last
  24 h). The *Unknown numbers* queue has an enquiry from a number not in the CRM: reply, then press
  **+ New seller lead**. The mobile is filled in and the chat links itself.
* At the bottom of the inbox, *Simulate an incoming message*: try a known number, a new number, or the word STOP
  (then try to message that person: it is blocked).
* **Message batches**: a draft "gentle follow-up" batch. Sign in as `lead@` to approve and send it; Harika cannot
  approve her own.
* **Reports** (left menu): today / week / month with CSV export. **My Day**, bottom: turn on the morning e-mail and
  preview it. As `founder@`, **Admin → E-mail outbox** shows it held (no mail server) and **Admin → Integrations**
  shows what is connected (nothing live) and the go-live checklist.

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
