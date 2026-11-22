# Local demo environment (synthetic data only)

URL: http://localhost:8000  — dev server, bound to 127.0.0.1 only, SQLite file `instance/teamos-demo.db`.
No WhatsApp or other live integrations exist in this build; nothing is deployed.

## Start it again
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

Records created during the review walkthrough are prefixed `DEMO` (SL-00008, a few tasks, one extra user). Records you add
through the UI are not auto-badged "synthetic" — only seeded ones are.

## Other commands
```powershell
C:\Users\user\.venvs\teamos-m2\Scripts\python.exe -m pytest -q
```
Reset demo data: stop the server, delete `instance\teamos-demo.db*`, then run `manage.py migrate` and `manage.py seed-demo`
with the `.env` values loaded.
