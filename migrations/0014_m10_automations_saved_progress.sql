-- Milestone 10: automations you can see and pause, saved searches, customer progress links.

-- On/off for each automatic job (key = job kind) plus '_all' (pause everything) and 'quiet_hours'.
-- A missing row means "on" (the default), so new automations start working without a data change.
CREATE TABLE automation_settings (
    key TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    note TEXT,
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT
);

-- A person's saved list filters ("Hyderabad buyers under 40 lakh"), with when they last looked so the app can
-- say how many records are new since then.
CREATE TABLE saved_searches (
    id {{ID}},
    user_id INTEGER NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL CHECK (kind IN ('seller','buyer')),
    name TEXT NOT NULL,
    query TEXT NOT NULL,                 -- URL query string of the list filters
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_saved_searches_user ON saved_searches(user_id, kind);

-- A private link a customer opens to see where things stand (no login). Only the hash is stored; one live link
-- per record; links expire and can be switched off.
CREATE TABLE status_links (
    id {{ID}},
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    token_hash TEXT NOT NULL UNIQUE,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    last_opened_at TEXT,
    opens INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_status_links_opp ON status_links(opportunity_id);
