-- "Others": people who call Salon Shastra for a JOB at a salon (HR screening) or for ACADEMY training (counselling).
-- Common contact facts are columns (search, filters); the screening answers are JSON in `answers`, with the main ones
-- copied to columns for the lists: interest = roles / courses wanted, experience, budget_or_salary, join_when.
CREATE TABLE inquiries (
    id {{ID}},
    ref_code TEXT UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('job', 'academy')),
    full_name TEXT NOT NULL,
    phone_normalized TEXT,
    alt_phone TEXT,
    email TEXT,
    city TEXT,
    locality TEXT,
    hometown TEXT,
    source TEXT NOT NULL DEFAULT 'incoming_call',
    interest TEXT,
    experience TEXT,
    budget_or_salary TEXT,
    join_when TEXT,
    answers TEXT,
    notes TEXT,
    temperature TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    owner_id INTEGER REFERENCES users(id),
    last_contact_at TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX ix_inquiries_kind ON inquiries (kind, status, created_at);
CREATE INDEX ix_inquiries_phone ON inquiries (phone_normalized);
/*pg*/ ALTER TABLE inquiries ENABLE ROW LEVEL SECURITY;
/*pg*/ REVOKE ALL ON inquiries FROM anon, authenticated;
