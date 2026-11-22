#!/usr/bin/env bash
# Nightly PostgreSQL backup: pg_dump (custom format) → AES-256 encrypt → local dir → optional off-site copy.
# Env: PGDATABASE (default teamos), BACKUP_DIR (default /var/backups/teamos), BACKUP_PASSFILE (key file, chmod 600),
#      KEEP_DAYS (default 14), OFFSITE_CMD (optional, e.g. "rclone copy {} b2:salonshastra-backups/teamos/")
set -euo pipefail
DB="${PGDATABASE:-teamos}"
DIR="${BACKUP_DIR:-/var/backups/teamos}"
PASSFILE="${BACKUP_PASSFILE:?set BACKUP_PASSFILE to a root-only key file}"
KEEP="${KEEP_DAYS:-14}"
mkdir -p "$DIR"; chmod 700 "$DIR"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
OUT="$DIR/teamos-$STAMP.dump.enc"
pg_dump --format=custom --no-owner "$DB" \
  | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass "file:$PASSFILE" -out "$OUT"
# Row-count manifest so restore_check.sh can prove the copy is complete.
psql -X -At -d "$DB" -c "SELECT string_agg(format('%s=%s', table_name, (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I', table_name), false, true, '')))[1]::text), E'\n' ORDER BY table_name) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'" > "$OUT.counts"
sha256sum "$OUT" > "$OUT.sha256"
echo "backup ok: $OUT ($(du -h "$OUT" | cut -f1))"
if [ -n "${OFFSITE_CMD:-}" ]; then eval "${OFFSITE_CMD//\{\}/$OUT}"; eval "${OFFSITE_CMD//\{\}/$OUT.counts}"; eval "${OFFSITE_CMD//\{\}/$OUT.sha256}"; fi
find "$DIR" -name 'teamos-*.dump.enc*' -mtime +"$KEEP" -delete
