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

## Demo logins — password `Demo@123` (all synthetic, badged "demo")
| Email | Role |
|---|---|
| founder@demo.salonshastra.test | Super Admin |
| lead@demo.salonshastra.test | Team lead / reviewer (can import) |
| shaheen@demo.salonshastra.test | Team member (seller team) |
| harika@demo.salonshastra.test | Team member (buyer team) |
| accounts@demo.salonshastra.test | Finance specialist |

Two-step sign-in is switched off for the demo (`MFA_REQUIRED_ROLES=` empty in `.env`). Enrol any user at /mfa/setup to try it.

## Milestone 11 — pilot program, what to try
1. Menu → **Pilot program**: counts by form status, filter by help area, upcoming calls with Vikas.
2. Open **Mane Street Men's Salon** → **Pilot program** tab: needs ticked, form filled, SWOT approved and sent, call with
   Vikas held, ₹5,000 salon visit paid, video shot. **Download PDF** to see the report the owner gets.
3. Open **Lotus Women's Spa & Salon** → the SWOT is a draft: edit a line, **Approve report**, then **Send to the owner** —
   WhatsApp opens with the message and private link. Open the link in another browser to see the owner's page and book
   one of Vikas's free times (Pilot program → **Vikas's free times** to add more).
4. On any other salon: tick help areas, **Send the form link**, or **Fill it for them** to try the 45-question form.

## Milestone 10 — what to try
* **Tools & admin → Automations**: see what runs by itself; as Founder, pause one and switch it back on.
* **Sellers**: filter (e.g. search "Kondapur"), **☆ Save this search**; new matching sellers show as "N new" here and on My Day.
* Open a seller → **Customer progress link → Preview** (switch to తెలుగు at the top), then **Make a link** and open it in a private window.
* **Reports → Lead sources**: which channels turn into deals.
* On your phone, open TeamOS and use **Install app** / **Add to Home Screen** (see Tools & admin → Install on your phone).

## Milestone 9 — what to try
* Open any seller or buyer: tap the phone number or **Call** (on a phone it dials), then the page jumps to **Log a call**.
* **Calendar** in the menu; **Get my calendar link** to add your work to Google Calendar (no customer details in it).
* **Tools & admin → Lead ads → Try a lead ad** with made-up details; the new lead appears with an owner and a first call.

## Milestone 8 — what to try
* **Menu**: shorter, grouped, with counts. Admin pages are under **Tools & admin**.
* **Team** (sign in as the team lead): Workload table → **More** → *Mark away* or *Preview hand-over*; **Not moving** list.
* **A seller or buyer → Overview → Summary → Draft for me**, edit, **Save summary**.
* **Reports → Match learning**: decline reasons, current weights, suggestions to approve (founder).
* **Customer forms in Telugu**: open `/f/seller?lang=te` or `/f/buyer?lang=te`, or tap తెలుగు at the top of any form.

## Load test (any time)
`python -I scripts/qa/load_and_sweep.py . instance/qa-load.db` — copies the demo database, creates 100 synthetic
sellers and buyers through the screens, exercises every module and prints crashes, refused actions and slow pages.

## Milestone 7.4 — smart import from sheets
* **Imports → Smart import**: upload an Excel/CSV file or paste a Google Sheets link; check the preview (tabs, what was
  understood, what was left out and why), then **Import now**.
* Real data is kept apart from the demo: a separate local pilot database (`instance/teamos-pilot.db`, never committed)
  can be run on port 8001:
  `DATABASE_URL=sqlite:///instance/teamos-pilot.db PORT=8001 DATA_NOTICE="PILOT — real customer data" python run_local.py`

## Milestone 7.3 — templates and staff accounts
* Real staff logins (Super Admin, seller team, buyer team) were created on the local demo database only — their
  details are not stored in this repository. Each is asked to choose their own password at first sign-in.
* **Admin → Message templates**: Meta category, problems found, "For Meta" copy block and **Check with Meta**
  (needs the WhatsApp account ID and token in Integrations).
* **Admin → Users → Reset password** for anyone who forgot theirs; click your own name at the top to change yours.

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
