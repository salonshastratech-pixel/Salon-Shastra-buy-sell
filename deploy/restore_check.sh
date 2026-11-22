#!/usr/bin/env bash
# Restore drill (UAT-26): decrypt a backup into a throw-away database and prove it is complete.
# Usage: BACKUP_PASSFILE=/root/.teamos-backup-key deploy/restore_check.sh /var/backups/teamos/teamos-....dump.enc
set -euo pipefail
FILE="${1:?backup file}"
PASSFILE="${BACKUP_PASSFILE:?set BACKUP_PASSFILE}"
SCRATCH="teamos_restore_check_$$"
sha256sum -c "$FILE.sha256" >/dev/null 2>&1 || { echo "CHECKSUM MISMATCH — backup corrupted or altered"; exit 1; }
echo "checksum ok"
createdb "$SCRATCH"
trap 'dropdb --if-exists "$SCRATCH"' EXIT
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "file:$PASSFILE" -in "$FILE" | pg_restore --no-owner -d "$SCRATCH"
psql -X -At -d "$SCRATCH" -c "SELECT string_agg(format('%s=%s', table_name, (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I', table_name), false, true, '')))[1]::text), E'\n' ORDER BY table_name) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'" > "/tmp/$SCRATCH.counts"
if diff -q "$FILE.counts" "/tmp/$SCRATCH.counts" >/dev/null; then echo "row counts match ($(wc -l < "$FILE.counts") tables)"; else echo "ROW COUNT MISMATCH"; diff "$FILE.counts" "/tmp/$SCRATCH.counts"; exit 1; fi
echo "schema: $(psql -X -At -d "$SCRATCH" -c 'select max(version) from schema_migrations')"
echo "active users: $(psql -X -At -d "$SCRATCH" -c 'select count(*) from users where is_active = 1')"
echo "RESULT: PASS"
