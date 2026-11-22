-- 0003: Buyer / investor management (PRD §7.1–7.3, §7.2A; Milestone 3).
--
-- A buyer opportunity (opportunities.kind = 'buyer') has exactly one acquisition mandate.
-- Answer state and evidence for each mandate field live in field_provenance
-- (entity_type = 'buyer_mandate'); every change is in field_change_log, like seller listings.
-- NULL money/quantity means NOT PROVIDED, never zero.

CREATE TABLE buyer_mandates (
    id {{ID}},
    opportunity_id INTEGER NOT NULL UNIQUE REFERENCES opportunities(id),
    data_version INTEGER NOT NULL DEFAULT 1,
    -- identity & intent
    need_type TEXT,                 -- acquire_operating | franchise | partnership | passive_invest | new_salon | exploring
    investor_type TEXT, decision_maker TEXT, salon_background TEXT,
    -- budget: all_in_max is the HARD ceiling on total cash outlay (§7.2A)
    budget_min INTEGER, budget_max INTEGER, all_in_max INTEGER,
    includes_deposit TEXT, includes_stock TEXT, includes_working_capital TEXT, finance_dependence TEXT,
    -- funding as declared by the buyer
    funding_source TEXT, cash_ready TEXT, cash_ready_amount INTEGER,
    -- geography (ranked localities live in buyer_localities)
    preferred_city TEXT, relocate_willing TEXT, max_commute_km INTEGER,
    -- salon & scale
    chairs_min INTEGER, chairs_max INTEGER, area_min_sqft INTEGER, staff_continuity_need TEXT, clientele_note TEXT,
    -- business, lease & risk
    min_monthly_sales INTEGER, min_operating_years INTEGER, turnaround_ok TEXT, max_monthly_rent INTEGER,
    min_lease_years_left INTEGER, renovation_budget INTEGER, liability_tolerance TEXT,
    -- timing & involvement
    purchase_window TEXT, involvement TEXT, visit_availability TEXT, transition_support_need TEXT,
    -- authority & constraints
    payment_approver TEXT, other_partners TEXT, decision_timeline TEXT, must_haves TEXT, deal_breakers TEXT,
    -- REVIEWER-ONLY. Never written from buyer- or staff-entry payloads (UAT-09).
    funding_status TEXT NOT NULL DEFAULT 'not_reviewed' CHECK (funding_status IN
        ('not_reviewed','evidence_requested','staff_reviewed','verified','insufficient')),
    funding_note TEXT,              -- restricted: evidence detail / limitations
    funding_reviewed_by INTEGER REFERENCES users(id),
    funding_reviewed_at TEXT,
    criteria_reviewed_version INTEGER,   -- data_version a reviewer confirmed as the effective criteria
    criteria_reviewed_by INTEGER REFERENCES users(id),
    criteria_reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Ranked preferred micro-markets and explicit exclusions (§7.2A Geography).
CREATE TABLE buyer_localities (
    id {{ID}},
    mandate_id INTEGER NOT NULL REFERENCES buyer_mandates(id),
    kind TEXT NOT NULL CHECK (kind IN ('preferred','excluded')),
    city TEXT NOT NULL,
    locality TEXT NOT NULL,
    rank_order INTEGER NOT NULL,    -- 1 = most preferred; exclusions keep insertion order
    note TEXT,
    added_by INTEGER REFERENCES users(id),
    added_at TEXT NOT NULL
);
CREATE INDEX ix_buyer_localities_mandate ON buyer_localities(mandate_id, kind, rank_order);

-- Must have / Prefer / Flexible / Avoid per criterion. A missing row means BLANK (not asked/answered);
-- 'not_important' is an explicit answer. choice = '' for scalar criteria (e.g. minimum chairs);
-- choice = a value for multi-choice dimensions (e.g. dimension 'gender_format', choice 'men').
CREATE TABLE buyer_preferences (
    mandate_id INTEGER NOT NULL REFERENCES buyer_mandates(id),
    dimension TEXT NOT NULL,
    choice TEXT NOT NULL DEFAULT '',
    importance TEXT NOT NULL CHECK (importance IN ('must','prefer','flexible','avoid','not_important')),
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (mandate_id, dimension, choice)
);
