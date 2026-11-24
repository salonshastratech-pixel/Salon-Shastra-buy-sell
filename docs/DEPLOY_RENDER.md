# Putting TeamOS online for the team — free (Render + Supabase)

Cost: **₹0**. Render's free plan runs the app; Supabase's free plan (already set up) holds the data; your domain
stays at Hostinger. Address: **https://team.salonshastra.com**.

What free means in practice:
* The app sleeps after 15 minutes with no visitors. Step 5 keeps it awake during office hours (and runs reminders).
* Uploaded documents/photos are not kept after a restart on the free plan — keep documents on WhatsApp/Drive for now.
* Everything else (records, tasks, reports, logins) lives in Supabase and is kept.

## 1. Supabase — the database password (2 minutes)
1. supabase.com → project **salon-shastra-teamos** → **Project Settings → Database → Reset database password**.
   Choose a long password **without** the characters `@ : / ? #` (letters and numbers are safest). Save it somewhere safe.
2. Top of the project page → **Connect** → **Session pooler** → copy the URI. Replace `[YOUR-PASSWORD]` with the password.
   This whole line is your `DATABASE_URL`. Never share it in chat.

## 2. Render — create the app (5 minutes)
1. render.com → **Get started** → **Sign in with GitHub** (the account that owns `Salon-Shastra-buy-sell`).
2. **New → Blueprint** → choose the repository → Render reads `render.yaml` and shows one service,
   *salon-shastra-teamos* (Free, Singapore).
3. It asks for the values that are not stored in the code:
   | Key | What to type |
   |---|---|
   | `DATABASE_URL` | the line from step 1.2 |
   | `PUBLIC_BASE_URL` | `https://team.salonshastra.com` |
   | `BOOTSTRAP_ADMIN_EMAIL` | your own e-mail (your founder login) |
   | `BOOTSTRAP_ADMIN_PASSWORD` | a temporary password — you will be asked to change it at first sign-in |
4. **Apply**. The first build takes 3–5 minutes. When it says **Live**, open the `…onrender.com` address it shows and
   sign in. You will choose a new password and set up the authenticator app (Google Authenticator / Microsoft
   Authenticator) — required for the founder and finance roles.

## 3. Your domain — team.salonshastra.com (Claude can do this through the Hostinger connection)
1. Render → the service → **Settings → Custom Domains → Add** → `team.salonshastra.com`. Render shows a CNAME target
   like `salon-shastra-teamos.onrender.com`.
2. Hostinger DNS for salonshastra.com: add **CNAME** `team` → that target. Nothing else on salonshastra.com changes.
3. Back in Render press **Verify**; the padlock (https) appears within minutes.

## 4. Team accounts and data
* **Tools & admin → Users**: add each team member (they get a temporary password and choose their own).
* **Tools & admin → Imports**: upload the Google Sheets (pilot responses, leads). TeamOS reads the remarks and fills the
  details; check the preview before committing.

## 5. Keep it awake + run reminders (free, 3 minutes)
1. Render → service → **Environment** → copy the value of `CRON_TOKEN`.
2. cron-job.org → free account → **Create cronjob**:
   URL `https://team.salonshastra.com/internal/tick?token=<CRON_TOKEN>`, every **10 minutes**,
   Monday–Saturday 08:00–21:00 (Asia/Kolkata).
   Each visit keeps the app awake and runs overdue alerts, form chasers and match refresh.

## Updating
Every push to `main` on GitHub redeploys automatically (about 3 minutes; the database is untouched).
