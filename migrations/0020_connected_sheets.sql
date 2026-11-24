-- Google Sheets the team keeps filling (Google Form responses, ad lead sheets). TeamOS checks each one every hour and,
-- when it has new people, prepares an import preview for the team to approve (nothing is saved without a click).
CREATE TABLE connected_sheets (
    id {{ID}},
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    sheet_key TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1,
    last_checked_at TEXT,
    last_sha TEXT,
    last_result TEXT,
    last_import_id INTEGER REFERENCES sheet_imports(id),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);
