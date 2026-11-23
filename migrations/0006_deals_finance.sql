-- 0006: Confidential teasers, introductions, deal room and admin-only company finance (PRD §9, §11.5; Milestone 6).
--
-- Sale consideration belongs to the buyer and seller. Only fees in fee_agreements can ever become Salon Shastra
-- revenue (FR-020, UAT-18). Money is whole INR; NULL means not provided.

-- Versioned anonymised teaser for a seller listing (§9.1, FR-016). Content is frozen at approval.
CREATE TABLE teasers (
    id {{ID}},
    seller_opp_id INTEGER NOT NULL REFERENCES opportunities(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','seller_approved','approved','retired')),
    headline TEXT,
    highlights TEXT,
    field_keys TEXT NOT NULL,            -- JSON list of whitelisted teaser fields included
    content TEXT,                        -- JSON snapshot of exactly what is shown (frozen when approved)
    listing_version INTEGER,             -- seller listing data version the content was taken from
    seller_consent_at TEXT,              -- seller approved these content categories
    seller_consent_method TEXT,
    seller_consent_note TEXT,
    seller_consent_by INTEGER REFERENCES users(id),
    approved_by INTEGER REFERENCES users(id),
    approved_at TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (seller_opp_id, version)
);

-- Each external share of a teaser with a specific buyer (link token stored hashed).
CREATE TABLE teaser_shares (
    id {{ID}},
    teaser_id INTEGER NOT NULL REFERENCES teasers(id),
    match_id INTEGER NOT NULL REFERENCES match_candidates(id),
    buyer_opp_id INTEGER NOT NULL REFERENCES opportunities(id),
    token_hash TEXT NOT NULL UNIQUE,
    channel TEXT NOT NULL CHECK (channel IN ('copy_link','whatsapp_mock')),
    shared_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    opened_at TEXT,
    revoked_at TEXT
);
CREATE INDEX ix_teaser_shares_match ON teaser_shares(match_id);

-- Introduction requests and approvals (§9.2, FR-018, UAT-15). details_shared records exactly what was released.
CREATE TABLE introductions (
    id {{ID}},
    match_id INTEGER NOT NULL REFERENCES match_candidates(id),
    status TEXT NOT NULL DEFAULT 'requested' CHECK (status IN ('requested','approved','rejected')),
    requested_by INTEGER REFERENCES users(id),
    requested_at TEXT NOT NULL,
    request_note TEXT,
    decided_by INTEGER REFERENCES users(id),
    decided_at TEXT,
    decision_note TEXT,
    details_shared TEXT,                 -- JSON {to_buyer: {...}, to_seller: {...}}
    consent_snapshot TEXT                -- JSON consent rows (id, purpose, text_version, captured_at) relied on
);
CREATE INDEX ix_intro_match ON introductions(match_id);

-- Deal room (§9.2, §9.3, FR-019).
CREATE TABLE deals (
    id {{ID}},
    ref_code TEXT UNIQUE,
    match_id INTEGER NOT NULL UNIQUE REFERENCES match_candidates(id),
    buyer_opp_id INTEGER NOT NULL REFERENCES opportunities(id),
    seller_opp_id INTEGER NOT NULL REFERENCES opportunities(id),
    stage TEXT NOT NULL DEFAULT 'introduced' CHECK (stage IN
        ('introduced','visit_scheduled','visit_completed','offer_received','negotiation','due_diligence',
         'agreement','handover','closed_won','closed_lost','on_hold','withdrawn')),
    owner_id INTEGER REFERENCES users(id),
    agreed_consideration INTEGER,        -- the parties' sale price — NEVER company revenue
    agreement_date TEXT,
    handover_date TEXT,
    closed_at TEXT,
    closed_reason TEXT,
    lessons TEXT,
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE deal_events (
    id {{ID}},
    deal_id INTEGER NOT NULL REFERENCES deals(id),
    event TEXT NOT NULL,
    from_stage TEXT,
    to_stage TEXT,
    body TEXT,
    actor_id INTEGER REFERENCES users(id),
    at TEXT NOT NULL
);
CREATE INDEX ix_deal_events ON deal_events(deal_id, at);

CREATE TABLE deal_visits (
    id {{ID}},
    deal_id INTEGER NOT NULL REFERENCES deals(id),
    scheduled_at TEXT NOT NULL,
    attendees TEXT,
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled','completed','cancelled','no_show')),
    outcome_note TEXT,
    buyer_feedback TEXT,
    task_id INTEGER REFERENCES tasks(id),
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Offers are versioned: a counter-offer is a new row (version + 1); earlier versions are never edited.
CREATE TABLE deal_offers (
    id {{ID}},
    deal_id INTEGER NOT NULL REFERENCES deals(id),
    version INTEGER NOT NULL,
    from_party TEXT NOT NULL CHECK (from_party IN ('buyer','seller')),
    amount INTEGER NOT NULL,
    deposit_treatment TEXT,
    stock_included TEXT,
    working_capital INTEGER,
    contingencies TEXT,
    financing TEXT,
    valid_until TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','countered','accepted','rejected','withdrawn','expired')),
    note TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    UNIQUE (deal_id, version)
);

CREATE TABLE deal_checklist (
    deal_id INTEGER NOT NULL REFERENCES deals(id),
    item_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','in_progress','reviewed','issue','not_applicable')),
    note TEXT,
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (deal_id, item_key)
);

-- ===== Admin-only company finance (§11.5, FR-020, FR-050) =====
-- Fee terms are entered per agreement (no hard-coded fee policy — decisions D-02..D-05 are open).
CREATE TABLE fee_agreements (
    id {{ID}},
    deal_id INTEGER REFERENCES deals(id),
    opportunity_id INTEGER REFERENCES opportunities(id),
    payer TEXT NOT NULL CHECK (payer IN ('seller','buyer')),
    fee_type TEXT NOT NULL CHECK (fee_type IN ('consultation','assessment','success','other')),
    basis TEXT NOT NULL CHECK (basis IN ('fixed','percent')),
    fixed_amount INTEGER,
    rate_pct REAL,
    earning_condition TEXT NOT NULL CHECK (earning_condition IN ('on_signing','on_agreement','on_closing','on_handover','manual')),
    earned_manually_at TEXT,
    agreement_ref TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','superseded','cancelled')),
    note TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE fee_invoices (
    id {{ID}},
    agreement_id INTEGER NOT NULL REFERENCES fee_agreements(id),
    invoice_no TEXT NOT NULL,
    amount INTEGER NOT NULL,             -- before tax
    tax_pct REAL,                        -- NULL = tax treatment not configured yet (D-05)
    tax_amount INTEGER,
    issued_on TEXT NOT NULL,
    note TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE fee_receipts (
    id {{ID}},
    agreement_id INTEGER NOT NULL REFERENCES fee_agreements(id),
    amount INTEGER NOT NULL,
    received_on TEXT NOT NULL,
    mode TEXT,
    reference TEXT,
    note TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE company_expenses (
    id {{ID}},
    category TEXT NOT NULL,
    amount INTEGER NOT NULL,
    spent_on TEXT NOT NULL,
    deal_id INTEGER REFERENCES deals(id),
    note TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);
