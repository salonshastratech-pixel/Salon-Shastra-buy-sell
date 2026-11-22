# Deploying TeamOS on a Hostinger VPS

**Status of this runbook:** the backup and restore scripts were run end-to-end against PostgreSQL 16. The
gunicorn, Caddy and systemd steps follow standard practice but have **not yet been run on the real VPS**. The first
deployment should be done together and this runbook corrected as needed.

## 0. What Salon Shastra must own first
- Hostinger account with a **KVM 2 VPS** (2 vCPU / 8 GB RAM), Ubuntu 24.04, Mumbai region if offered.
- A subdomain, e.g. `teamos.salonshastra.com`, with an **A record** pointing at the VPS IP.
- A GitHub organisation repository for the code (Salon Shastra as owner).
- An off-site storage bucket for backups (e.g. Backblaze B2 or AWS S3 Mumbai), with the credentials held by the founder.

## 1. Server basics (as root)
```bash
apt update && apt -y upgrade
apt -y install python3-venv python3-dev postgresql-16 caddy ufw fail2ban unattended-upgrades git rclone
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
adduser --system --group --home /opt/teamos teamos
# Disable password SSH once your key works: PasswordAuthentication no in /etc/ssh/sshd_config
```

## 2. Database
```bash
sudo -u postgres createuser teamos --pwprompt     # strong password; store in a password manager
sudo -u postgres createdb teamos --owner teamos
```
PostgreSQL listens on localhost only (the Ubuntu default). Never open port 5432 in the firewall.

## 3. Application
```bash
sudo -u teamos git clone https://github.com/<salonshastra-org>/teamos.git /opt/teamos/app
sudo -u teamos python3 -m venv /opt/teamos/venv
sudo -u teamos /opt/teamos/venv/bin/pip install -r /opt/teamos/app/requirements.txt
mkdir -p /etc/teamos && cp /opt/teamos/app/deploy/teamos.env.example /etc/teamos/teamos.env
chown root:teamos /etc/teamos/teamos.env && chmod 640 /etc/teamos/teamos.env   # then edit real values
mkdir -p /opt/teamos/app/instance /var/lib/teamos && chown teamos /opt/teamos/app/instance /var/lib/teamos
cd /opt/teamos/app && sudo -u teamos bash -c 'set -a; . /etc/teamos/teamos.env; /opt/teamos/venv/bin/python manage.py migrate'
sudo -u teamos bash -c 'set -a; . /etc/teamos/teamos.env; /opt/teamos/venv/bin/python manage.py create-admin --email founder@salonshastra.com --name "Founder"'
```
**Never** run `seed-demo` in production. The command refuses when `APP_ENV=production`.

## 4. Services
```bash
cp deploy/teamos.service deploy/teamos-worker.service deploy/teamos-backup.service deploy/teamos-backup.timer /etc/systemd/system/
cp deploy/Caddyfile /etc/caddy/Caddyfile        # edit the domain
systemctl daemon-reload
systemctl enable --now teamos teamos-worker teamos-backup.timer
systemctl reload caddy
curl -s https://teamos.salonshastra.com/healthz   # expect {"status":"ok",...}
```
Sign in as the founder and set up two-step sign-in. It is mandatory for Super Admin and Finance roles.

## 5. Backups (FR-024)
```bash
openssl rand -base64 48 > /etc/teamos/backup.key && chown postgres /etc/teamos/backup.key && chmod 600 /etc/teamos/backup.key
# Keep a SECOND copy of this key offline (founder's password manager). Without it, backups cannot be restored.
# Off-site copy: set OFFSITE_CMD in /etc/teamos/backup.env, e.g.
#   OFFSITE_CMD=rclone copy {} b2:salonshastra-backups/teamos/
systemctl start teamos-backup && journalctl -u teamos-backup -n 20
```
**Monthly restore drill (UAT-26).** Run this, keep the output, and note the date in the decision register:
```bash
sudo -u postgres BACKUP_PASSFILE=/etc/teamos/backup.key /opt/teamos/app/deploy/restore_check.sh \
     $(ls -t /var/backups/teamos/*.dump.enc | head -1)
```
Proposed targets, pending approval: **RPO 24 h** (nightly backups) and **RTO 4 h** (restore to a fresh VPS).

## 6. Updating
```bash
cd /opt/teamos/app && sudo -u teamos git pull
sudo -u teamos /opt/teamos/venv/bin/pip install -r requirements.txt
sudo -u teamos bash -c 'set -a; . /etc/teamos/teamos.env; /opt/teamos/venv/bin/python manage.py migrate'
systemctl restart teamos teamos-worker
```
Take a backup before every migration (`systemctl start teamos-backup`). To roll back, restore the pre-deploy dump
and check out the previous git tag.

## 7. Monitoring
- **Uptime:** point an external monitor (e.g. UptimeRobot, free tier) at `/healthz`. It returns 503 when the database
  is unreachable or jobs are stuck.
- **Logs:** `journalctl -u teamos -u teamos-worker -f`. Logs are JSON, one line per request, with no customer data.
- **Failed jobs:** Admin → Background jobs.
