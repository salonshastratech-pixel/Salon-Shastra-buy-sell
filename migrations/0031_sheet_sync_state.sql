-- Sync with the team's sheets: what each sheet last said for each field of a record (the baseline of a three-way
-- merge). A sheet change is applied only if nobody changed that field in TeamOS since; otherwise TeamOS keeps its
-- value and the record gets a note. '__row__' holds a fingerprint so unchanged rows are skipped quickly.
CREATE TABLE sheet_field_state (
    id {{ID}},
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    field_key TEXT NOT NULL,
    value TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (opportunity_id, field_key)
);
/*pg*/ ALTER TABLE sheet_field_state ENABLE ROW LEVEL SECURITY;
/*pg*/ REVOKE ALL ON sheet_field_state FROM anon, authenticated;
