-- Milestone 8: measured improvements — workload balancing, stuck records, learning from declined matches,
-- drafted summaries (a person always confirms), customer forms in Telugu.

-- Workload: a person who is away (leave, training) gets no new leads and can hand over work in one step.
ALTER TABLE users ADD COLUMN away_until TEXT;

-- Confirmed one-paragraph summary of a seller or buyer, shown at the top of the record.
ALTER TABLE opportunities ADD COLUMN summary TEXT;
ALTER TABLE opportunities ADD COLUMN summary_source TEXT;     -- built_in | claude (who drafted it)
ALTER TABLE opportunities ADD COLUMN summary_by INTEGER REFERENCES users(id);   -- who confirmed it
ALTER TABLE opportunities ADD COLUMN summary_at TEXT;

-- Learning loop: declined matches propose weight changes; nothing changes until the founder approves.
CREATE TABLE match_weight_changes (
    id {{ID}},
    family TEXT NOT NULL,              -- matching family letter (A = affordability, B = location, ...)
    old_weight INTEGER NOT NULL,
    new_weight INTEGER NOT NULL,
    reason TEXT NOT NULL,              -- plain-language why (evidence summary)
    evidence TEXT,                     -- JSON: decline counts behind the proposal
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','approved','rejected','superseded')),
    proposed_at TEXT NOT NULL,
    decided_by INTEGER REFERENCES users(id),
    decided_at TEXT,
    decision_note TEXT
);
CREATE INDEX ix_weight_changes ON match_weight_changes(status, family);

-- Which language the customer answered a form in (en, te).
ALTER TABLE form_submissions ADD COLUMN lang TEXT;
