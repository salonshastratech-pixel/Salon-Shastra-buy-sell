-- Salon Owners pilot program: what each salon owner needs help with, the pilot form, the SWOT report,
-- calls with Vikas Rachakonda and paid consultations (with the free video bonus).

-- One row per salon-owner record: lead type, help areas ticked on the first call, pilot-form status.
CREATE TABLE pilot_profiles (
    id {{ID}},
    opportunity_id INTEGER NOT NULL UNIQUE REFERENCES opportunities(id),
    lead_type TEXT NOT NULL DEFAULT 'salon_owner' CHECK (lead_type IN ('salon_owner','new_salon','investor')),
    help_areas TEXT NOT NULL DEFAULT '[]',          -- JSON list of help-area keys
    help_other TEXT,
    problem_note TEXT,                               -- the owner's problem in the team's words
    form_status TEXT NOT NULL DEFAULT 'not_sent' CHECK (form_status IN ('not_sent','sent','filled','not_filled')),
    form_sent_at TEXT,
    form_filled_at TEXT,
    -- ChairPe (salon operating system) sale: not offered → demo → 14-day free trial → paying / not interested
    chairpe_status TEXT NOT NULL DEFAULT 'not_offered'
        CHECK (chairpe_status IN ('not_offered','demo','trial','paying','not_interested')),
    chairpe_plan TEXT CHECK (chairpe_plan IS NULL OR chairpe_plan IN ('basic','standard','elite','multi_branch')),
    chairpe_months INTEGER,
    chairpe_trial_start TEXT,
    chairpe_paid_on TEXT,
    chairpe_note TEXT,
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT NOT NULL
);

-- Private customer links (only the hash is stored): the pilot form, the report download and the booking page.
CREATE TABLE pilot_links (
    id {{ID}},
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    token_hash TEXT NOT NULL UNIQUE,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    last_opened_at TEXT
);
CREATE INDEX ix_pilot_links_opp ON pilot_links(opportunity_id);

-- Pilot-form answers (JSON keyed by question). Latest row wins; earlier ones are kept as history.
CREATE TABLE pilot_responses (
    id {{ID}},
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    answers TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('link','staff','import')),
    created_by INTEGER REFERENCES users(id),
    submitted_at TEXT NOT NULL
);
CREATE INDEX ix_pilot_responses_opp ON pilot_responses(opportunity_id);

-- SWOT report versions: drafted from the answers, edited and approved by staff, then sent as a PDF.
CREATE TABLE swot_reports (
    id {{ID}},
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    response_id INTEGER REFERENCES pilot_responses(id),
    version INTEGER NOT NULL,
    content TEXT NOT NULL,                           -- JSON: summary, scores, strengths, weaknesses, opportunities, threats, steps
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved')),
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    approved_by INTEGER REFERENCES users(id),
    approved_at TEXT,
    sent_at TEXT,
    sent_by INTEGER REFERENCES users(id)
);
CREATE INDEX ix_swot_reports_opp ON swot_reports(opportunity_id);
CREATE INDEX ix_swot_reports_response ON swot_reports(response_id);

-- Vikas Rachakonda's free time slots and the calls booked into them.
CREATE TABLE vikas_slots (
    id {{ID}},
    starts_at TEXT NOT NULL,
    minutes INTEGER NOT NULL DEFAULT 30,
    mode TEXT NOT NULL DEFAULT 'phone' CHECK (mode IN ('phone','video','office')),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','booked','cancelled')),
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE INDEX ix_vikas_slots_start ON vikas_slots(starts_at);

CREATE TABLE vikas_bookings (
    id {{ID}},
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    slot_id INTEGER REFERENCES vikas_slots(id),
    starts_at TEXT,                                  -- NULL while only requested (no slot picked)
    preferred_time TEXT,                             -- owner's words when no slot was free
    status TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('requested','booked','done','cancelled','no_show')),
    source TEXT NOT NULL CHECK (source IN ('link','staff')),
    note TEXT,
    outcome_note TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX ix_vikas_bookings_opp ON vikas_bookings(opportunity_id);
CREATE INDEX ix_vikas_bookings_slot ON vikas_bookings(slot_id);

-- Paid consultations after the call with Vikas, and the free video bonus.
CREATE TABLE consultations (
    id {{ID}},
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    kind TEXT NOT NULL CHECK (kind IN ('office','salon_visit')),
    in_hyderabad INTEGER NOT NULL DEFAULT 1,
    fee INTEGER NOT NULL,
    scheduled_at TEXT,
    status TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked','done','cancelled')),
    paid INTEGER NOT NULL DEFAULT 0,
    paid_mode TEXT,
    paid_ref TEXT,
    paid_at TEXT,
    video_status TEXT NOT NULL DEFAULT 'pending' CHECK (video_status IN ('pending','shot','posted','declined')),
    video_link TEXT,
    note TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX ix_consultations_opp ON consultations(opportunity_id);
