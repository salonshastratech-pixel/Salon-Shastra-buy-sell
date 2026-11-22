-- 0002: spreadsheet imports (FR-004), MFA for privileged users (PRD §14.1), job run history.

CREATE TABLE import_batches (
    id {{ID}},
    kind TEXT NOT NULL CHECK (kind IN ('seller')),
    filename TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'preview' CHECK (status IN ('preview','committed','discarded')),
    mapping TEXT NOT NULL,            -- JSON {column header: canonical field}
    headers TEXT NOT NULL,            -- JSON list, original order
    total_rows INTEGER NOT NULL,
    created_count INTEGER,
    owner_id INTEGER REFERENCES users(id),
    uploaded_by INTEGER NOT NULL REFERENCES users(id),
    committed_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    committed_at TEXT
);

-- Raw values are preserved forever alongside the mapped result (PRD §13).
CREATE TABLE import_rows (
    id {{ID}},
    batch_id INTEGER NOT NULL REFERENCES import_batches(id),
    row_no INTEGER NOT NULL,
    raw TEXT NOT NULL,                -- JSON {header: original cell text}
    status TEXT NOT NULL CHECK (status IN ('new','duplicate_crm','duplicate_file','invalid')),
    errors TEXT,                      -- JSON {field: message}
    duplicate_contact_id INTEGER REFERENCES contacts(id),
    duplicate_of_row INTEGER,
    decision TEXT,                    -- create | link | flag | skip
    outcome TEXT,                     -- created | skipped | changed_since_preview
    opportunity_id INTEGER REFERENCES opportunities(id)
);
CREATE INDEX ix_import_rows_batch ON import_rows(batch_id, row_no);

ALTER TABLE users ADD COLUMN mfa_secret TEXT;
ALTER TABLE users ADD COLUMN mfa_enabled_at TEXT;
ALTER TABLE users ADD COLUMN mfa_last_step INTEGER;
ALTER TABLE sessions ADD COLUMN mfa_passed INTEGER NOT NULL DEFAULT 0;

CREATE TABLE mfa_recovery_codes (
    id {{ID}},
    user_id INTEGER NOT NULL REFERENCES users(id),
    code_hash TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE job_runs (
    id {{ID}},
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    outcome TEXT,                     -- done | error
    message TEXT
);
