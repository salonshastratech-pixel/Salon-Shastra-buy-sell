-- Milestone 9: calendar subscribe links and Facebook / Instagram lead-ad intake.

-- A private link a team member adds to Google Calendar / their phone. Only the hash of the secret is stored; the
-- feed shows reference codes and the kind of task, never customer names or numbers.
CREATE TABLE calendar_feeds (
    id {{ID}},
    user_id INTEGER NOT NULL REFERENCES users(id),
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT
);
CREATE INDEX ix_calendar_feeds_user ON calendar_feeds(user_id);

-- Every lead-ad delivery (real webhook or test simulation), so nothing is lost and nothing is processed twice.
CREATE TABLE lead_ad_events (
    id {{ID}},
    leadgen_id TEXT NOT NULL UNIQUE,     -- Meta's lead id (or SIM-… for a test)
    form_name TEXT,
    source TEXT NOT NULL CHECK (source IN ('webhook','simulation')),
    payload TEXT NOT NULL,               -- JSON: the answers as received
    status TEXT NOT NULL DEFAULT 'received' CHECK (status IN ('received','imported','skipped','failed')),
    detail TEXT,                         -- what happened (new lead, existing person updated, why skipped)
    import_id INTEGER REFERENCES sheet_imports(id),
    opportunity_id INTEGER REFERENCES opportunities(id),
    received_at TEXT NOT NULL,
    processed_at TEXT
);
