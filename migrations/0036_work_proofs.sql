-- Daily proof of work: each team member uploads screenshots of the calls and WhatsApp chats they did that day.
-- The image itself is in document_blobs (encrypted); screenshots older than the retention period are removed.
CREATE TABLE work_proofs (
    id {{ID}},
    user_id INTEGER NOT NULL REFERENCES users(id),
    day TEXT NOT NULL,                 -- IST date the work was done, YYYY-MM-DD
    kind TEXT NOT NULL CHECK (kind IN ('calls', 'whatsapp', 'other')),
    stored_name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_work_proofs_day ON work_proofs (day, user_id);
/*pg*/ ALTER TABLE work_proofs ENABLE ROW LEVEL SECURITY;
/*pg*/ REVOKE ALL ON work_proofs FROM anon, authenticated;
