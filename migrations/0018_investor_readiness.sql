-- Buyers & investors: first-call screening (5 questions), the investor form, call with Vikas, office visit with the
-- registration fee, and the investor readiness report. Private links and Vikas bookings reuse the pilot tables.

CREATE TABLE investor_profiles (
    id {{ID}},
    opportunity_id INTEGER NOT NULL UNIQUE REFERENCES opportunities(id),
    screen_answers TEXT,                       -- JSON: the 5 first-call answers
    screen_score INTEGER,                      -- 0–100
    screen_verdict TEXT CHECK (screen_verdict IS NULL OR screen_verdict IN ('pursue','nurture','not_now')),
    screen_note TEXT,
    screened_by INTEGER REFERENCES users(id),
    screened_at TEXT,
    form_status TEXT NOT NULL DEFAULT 'not_sent' CHECK (form_status IN ('not_sent','sent','filled','not_filled')),
    form_sent_at TEXT,
    form_filled_at TEXT,
    office_visit_at TEXT,
    office_visit_status TEXT NOT NULL DEFAULT 'not_booked'
        CHECK (office_visit_status IN ('not_booked','booked','done','no_show','cancelled')),
    reg_fee INTEGER NOT NULL DEFAULT 5000,
    reg_status TEXT NOT NULL DEFAULT 'not_due' CHECK (reg_status IN ('not_due','due','paid','waived')),
    reg_paid_at TEXT,
    reg_paid_mode TEXT,
    reg_paid_ref TEXT,
    reg_note TEXT,
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT NOT NULL
);

CREATE TABLE investor_responses (
    id {{ID}},
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    answers TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('link','staff')),
    created_by INTEGER REFERENCES users(id),
    submitted_at TEXT NOT NULL
);
CREATE INDEX ix_investor_responses_opp ON investor_responses(opportunity_id);

CREATE TABLE investor_reports (
    id {{ID}},
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    response_id INTEGER REFERENCES investor_responses(id),
    version INTEGER NOT NULL,
    content TEXT NOT NULL,                     -- JSON: verdict, path, scores, strengths, risks, conditions, steps
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved')),
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    approved_by INTEGER REFERENCES users(id),
    approved_at TEXT,
    sent_at TEXT,
    sent_by INTEGER REFERENCES users(id)
);
CREATE INDEX ix_investor_reports_opp ON investor_reports(opportunity_id);
CREATE INDEX ix_investor_reports_response ON investor_reports(response_id);
