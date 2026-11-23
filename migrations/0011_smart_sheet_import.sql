-- Milestone 7.4: smart import of any workbook (Google Sheets export, Excel, CSV) for sellers AND buyers.
-- Every tab is judged (sellers / buyers / not relevant), every row is cleaned, de-duplicated and either creates a lead,
-- updates the existing record (fills gaps only, never overwrites) or is skipped with a reason. Raw cells kept forever.
CREATE TABLE sheet_imports (
    id {{ID}},
    filename TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'preview' CHECK (status IN ('preview','committed','discarded')),
    tabs TEXT NOT NULL,                 -- JSON: per tab {name, kind, confidence, why, mapping, ignored, rows}
    counts TEXT,                        -- JSON after commit
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    committed_by INTEGER REFERENCES users(id),
    committed_at TEXT
);

CREATE TABLE sheet_import_rows (
    id {{ID}},
    import_id INTEGER NOT NULL REFERENCES sheet_imports(id),
    tab TEXT NOT NULL,
    row_no INTEGER NOT NULL,
    raw TEXT NOT NULL,                  -- JSON {header: cell}
    kind TEXT,                          -- seller | buyer (after row-level checks)
    phone TEXT,
    data TEXT,                          -- JSON canonical values understood from the row
    notes TEXT,                         -- remarks / call notes found in the row
    action TEXT NOT NULL CHECK (action IN ('create','update','merged','skip')),
    reason TEXT,                        -- why skipped / merged / what will be updated
    contact_id INTEGER REFERENCES contacts(id),
    opportunity_id INTEGER REFERENCES opportunities(id),
    merged_into INTEGER,                -- sheet_import_rows.id this row was merged into
    outcome TEXT                        -- created | updated | skipped | merged (after commit)
);
CREATE INDEX ix_sheet_import_rows ON sheet_import_rows(import_id, action);
