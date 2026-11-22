-- 0001_foundation.sql
-- Salon Shastra TeamOS: identity, access control, audit, contacts, businesses,
-- opportunities, seller listings, provenance, tasks, activities, notifications, jobs.
--
-- Portability: written for SQLite (dev/test) and PostgreSQL (production).
-- {{ID}} is replaced by the migration runner with the dialect's identity PK.
-- Timestamps are ISO-8601 UTC text ("2026-11-22T17:03:58Z"); money is whole INR (INTEGER).
-- NULL money/quantity means NOT PROVIDED. The answer state lives in field_provenance.

CREATE TABLE users (
    id {{ID}},
    email TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('super_admin','team_lead','team_member','finance')),
    team TEXT NOT NULL DEFAULT 'general' CHECK (team IN ('seller','buyer','general','finance','admin')),
    is_active INTEGER NOT NULL DEFAULT 1,
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE user_grants (
    id {{ID}},
    user_id INTEGER NOT NULL REFERENCES users(id),
    capability TEXT NOT NULL,
    scope_type TEXT,            -- NULL = global; e.g. 'opportunity'
    scope_id INTEGER,
    granted_by INTEGER NOT NULL REFERENCES users(id),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT
);
CREATE INDEX ix_user_grants_user ON user_grants(user_id);

CREATE TABLE sessions (
    id {{ID}},
    token_hash TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    ip TEXT,
    user_agent TEXT
);
CREATE INDEX ix_sessions_user ON sessions(user_id);

CREATE TABLE login_attempts (
    id {{ID}},
    email TEXT NOT NULL,
    ip TEXT,
    success INTEGER NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX ix_login_attempts_email ON login_attempts(email, at);

-- Append-only. Never updated or deleted by the application.
CREATE TABLE audit_events (
    id {{ID}},
    at TEXT NOT NULL,
    actor_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    outcome TEXT NOT NULL DEFAULT 'allowed' CHECK (outcome IN ('allowed','denied','failed')),
    entity_type TEXT,
    entity_id INTEGER,
    detail TEXT,               -- JSON
    ip TEXT
);
CREATE INDEX ix_audit_entity ON audit_events(entity_type, entity_id);
CREATE INDEX ix_audit_at ON audit_events(at);

CREATE TABLE contacts (
    id {{ID}},
    full_name TEXT NOT NULL,
    phone_normalized TEXT,      -- E.164, e.g. +919000000001
    phone_raw TEXT,
    alt_phone_normalized TEXT,
    email TEXT,
    city TEXT,
    locality TEXT,
    preferred_language TEXT NOT NULL DEFAULT 'en',
    do_not_contact INTEGER NOT NULL DEFAULT 0,
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX ix_contacts_phone ON contacts(phone_normalized);
CREATE INDEX ix_contacts_alt_phone ON contacts(alt_phone_normalized);

-- Append-only consent ledger; latest row per (contact, purpose, channel) is effective.
CREATE TABLE contact_consents (
    id {{ID}},
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    purpose TEXT NOT NULL CHECK (purpose IN ('service_contact','marketing','retain_data','share_teaser','share_identity')),
    channel TEXT NOT NULL DEFAULT 'any' CHECK (channel IN ('any','phone','whatsapp','email')),
    granted INTEGER NOT NULL,
    source TEXT NOT NULL,       -- e.g. 'staff_call', 'public_form', 'whatsapp_optout'
    text_version TEXT,
    note TEXT,
    captured_by INTEGER REFERENCES users(id),
    captured_at TEXT NOT NULL
);
CREATE INDEX ix_consents_contact ON contact_consents(contact_id, purpose);

CREATE TABLE salon_businesses (
    id {{ID}},
    name TEXT NOT NULL,
    city TEXT,
    locality TEXT,
    owner_contact_id INTEGER REFERENCES contacts(id),
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX ix_business_owner ON salon_businesses(owner_contact_id);

CREATE TABLE opportunities (
    id {{ID}},
    ref_code TEXT UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('seller','buyer')),
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    business_id INTEGER REFERENCES salon_businesses(id),
    stage TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','holding','closed')),
    primary_owner_id INTEGER REFERENCES users(id),
    priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('urgent','high','normal','low')),
    source TEXT NOT NULL,
    source_detail TEXT,         -- JSON: campaign, form id, import batch, raw payload ref
    next_action TEXT,           -- denormalised from earliest open task
    next_action_due TEXT,
    blocker TEXT,
    last_contact_at TEXT,
    closed_reason TEXT,
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX ix_opps_contact ON opportunities(contact_id);
CREATE INDEX ix_opps_owner ON opportunities(primary_owner_id, status);
CREATE INDEX ix_opps_kind_stage ON opportunities(kind, stage);

CREATE TABLE opportunity_collaborators (
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    added_by INTEGER REFERENCES users(id),
    added_at TEXT NOT NULL,
    PRIMARY KEY (opportunity_id, user_id)
);

CREATE TABLE stage_history (
    id {{ID}},
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    from_stage TEXT,
    to_stage TEXT NOT NULL,
    reason TEXT,
    actor_id INTEGER REFERENCES users(id),
    at TEXT NOT NULL
);
CREATE INDEX ix_stage_history_opp ON stage_history(opportunity_id);

-- One row per specific salon offered. Canonical fields mirror teamos/domain/seller_fields.py.
CREATE TABLE seller_listings (
    id {{ID}},
    opportunity_id INTEGER NOT NULL UNIQUE REFERENCES opportunities(id),
    business_id INTEGER NOT NULL REFERENCES salon_businesses(id),
    approval_state TEXT NOT NULL DEFAULT 'draft'
        CHECK (approval_state IN ('draft','in_review','approved','re_review_required','rejected')),
    data_version INTEGER NOT NULL DEFAULT 1,
    approved_version INTEGER,
    -- quick screen
    operating_status TEXT, ownership_role TEXT, sale_purpose TEXT, asking_price INTEGER,
    sale_timeline TEXT, preferred_callback TEXT,
    -- ownership & decision
    legal_name TEXT, signatory_name TEXT, owners_count INTEGER, outlets_count INTEGER,
    transfer_authority TEXT, existing_broker TEXT,
    -- business profile
    operating_years INTEGER, gender_format TEXT, business_model TEXT, franchise_brand TEXT,
    positioning TEXT, address_line TEXT, landmark TEXT, pin_code TEXT,
    -- physical asset
    carpet_area_sqft INTEGER, chairs INTEGER, wash_stations INTEGER, treatment_rooms INTEGER,
    equipment_condition TEXT, inclusions_note TEXT,
    -- lease & landlord
    monthly_rent INTEGER, rent_deposit INTEGER, deposit_treatment TEXT, lease_expiry TEXT,
    lease_renewal TEXT, lease_transfer_status TEXT, rent_escalation_pct REAL, rent_arrears INTEGER,
    -- customers & demand
    avg_monthly_bills INTEGER, avg_ticket INTEGER, repeat_customer_pct INTEGER, prepaid_liabilities INTEGER,
    -- operating summary
    avg_monthly_sales INTEGER, avg_monthly_expenses INTEGER,
    -- staff
    staff_headcount INTEGER, key_staff_dependence TEXT, owner_involvement TEXT, staff_continuity TEXT,
    -- liabilities
    outstanding_loans INTEGER, supplier_dues INTEGER, employee_dues INTEGER, litigation TEXT,
    -- reason & urgency
    sale_reason TEXT, sale_reason_note TEXT, desired_exit_date TEXT, transition_support TEXT,
    -- commercial offer
    negotiable TEXT, negotiable_min_price INTEGER, sale_structure TEXT, stock_value_separate INTEGER,
    franchise_transfer_fee INTEGER, working_capital_estimate INTEGER, expected_handover_weeks INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX ix_listings_business ON seller_listings(business_id);

-- Answer state + evidence status per canonical field (missing vs unknown vs declared vs verified).
CREATE TABLE field_provenance (
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    field_key TEXT NOT NULL,
    answer_state TEXT NOT NULL CHECK (answer_state IN
        ('value','unknown','not_available','prefer_to_discuss','not_applicable')),
    evidence_status TEXT NOT NULL DEFAULT 'seller_declared' CHECK (evidence_status IN
        ('seller_declared','buyer_declared','staff_reviewed','document_supported','verified','disputed')),
    source TEXT NOT NULL,
    note TEXT,
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (entity_type, entity_id, field_key)
);

CREATE TABLE field_change_log (
    id {{ID}},
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    data_version INTEGER NOT NULL,
    field_key TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    old_state TEXT,
    new_state TEXT,
    actor_id INTEGER REFERENCES users(id),
    at TEXT NOT NULL
);
CREATE INDEX ix_field_change_entity ON field_change_log(entity_type, entity_id);

CREATE TABLE seller_monthly_financials (
    id {{ID}},
    listing_id INTEGER NOT NULL REFERENCES seller_listings(id),
    month TEXT NOT NULL,                -- 'YYYY-MM'
    revenue INTEGER,                    -- NULL = not provided (distinct from 0)
    operating_expenses INTEGER,
    owner_drawings INTEGER,
    evidence_status TEXT NOT NULL DEFAULT 'seller_declared',
    source TEXT NOT NULL,
    note TEXT,
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT NOT NULL,
    UNIQUE (listing_id, month)
);

CREATE TABLE swot_items (
    id {{ID}},
    listing_id INTEGER NOT NULL REFERENCES seller_listings(id),
    kind TEXT NOT NULL CHECK (kind IN ('strength','weakness','opportunity','threat')),
    statement TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('seller','staff_observation','document','third_party')),
    confidence TEXT NOT NULL CHECK (confidence IN ('low','medium','high')),
    open_question TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE listing_checklist (
    listing_id INTEGER NOT NULL REFERENCES seller_listings(id),
    item_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'not_requested' CHECK (status IN
        ('not_requested','pending','received','reviewed','unresolved','not_applicable')),
    note TEXT,
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (listing_id, item_key)
);

CREATE TABLE listing_approvals (
    id {{ID}},
    listing_id INTEGER NOT NULL REFERENCES seller_listings(id),
    decision TEXT NOT NULL CHECK (decision IN ('approved','rejected','re_review_flagged')),
    data_version INTEGER NOT NULL,
    conditions TEXT,
    actor_id INTEGER REFERENCES users(id),
    at TEXT NOT NULL
);

CREATE TABLE duplicate_reviews (
    id {{ID}},
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    candidate_contact_id INTEGER NOT NULL REFERENCES contacts(id),
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','distinct','same_person_linked')),
    rationale TEXT,
    resolved_by INTEGER REFERENCES users(id),
    resolved_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE tasks (
    id {{ID}},
    title TEXT NOT NULL,
    task_type TEXT NOT NULL DEFAULT 'follow_up' CHECK (task_type IN
        ('first_contact','call','follow_up','collect_info','review','approval','visit','match_review','other')),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','cancelled')),
    priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('urgent','high','normal','low')),
    due_at TEXT,
    assignee_id INTEGER REFERENCES users(id),
    creator_id INTEGER REFERENCES users(id),
    entity_type TEXT,
    entity_id INTEGER,
    parent_task_id INTEGER REFERENCES tasks(id),
    outcome TEXT,
    completed_at TEXT,
    completed_by INTEGER REFERENCES users(id),
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX ix_tasks_assignee ON tasks(assignee_id, status, due_at);
CREATE INDEX ix_tasks_entity ON tasks(entity_type, entity_id, status);

CREATE TABLE task_events (
    id {{ID}},
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    event TEXT NOT NULL,
    detail TEXT,
    actor_id INTEGER REFERENCES users(id),
    at TEXT NOT NULL
);

CREATE TABLE activities (
    id {{ID}},
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('call','note','stage_change','assignment','data_update','approval','system')),
    call_outcome TEXT,
    body TEXT,
    actor_id INTEGER REFERENCES users(id),
    at TEXT NOT NULL
);
CREATE INDEX ix_activities_entity ON activities(entity_type, entity_id, at);

CREATE TABLE notifications (
    id {{ID}},
    user_id INTEGER NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    link TEXT,
    read_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_notifications_user ON notifications(user_id, read_at);

-- DB-backed job queue (no Redis needed at this scale). dedupe_key makes enqueue idempotent.
CREATE TABLE jobs (
    id {{ID}},
    kind TEXT NOT NULL,
    payload TEXT,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed','dead')),
    dedupe_key TEXT UNIQUE,
    run_after TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX ix_jobs_status ON jobs(status, run_after);
