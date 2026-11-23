-- 0005: Matching engine + Match Desk (PRD §8, §9.2; Milestone 5).
--
-- One row per (buyer opportunity, seller opportunity) pair. The latest evaluation is denormalised onto the
-- candidate for fast queues; every evaluation that changes the result is kept in match_snapshots so earlier
-- decisions can always be explained with the data and rules version they were made on (PRD §8.6, UAT-36).

CREATE TABLE match_candidates (
    id {{ID}},
    buyer_opp_id INTEGER NOT NULL REFERENCES opportunities(id),
    seller_opp_id INTEGER NOT NULL REFERENCES opportunities(id),
    stage TEXT NOT NULL DEFAULT 'suggested' CHECK (stage IN
        ('suggested','shortlisted','approved_to_approach','interest_confirmed',
         'not_suitable','buyer_declined','seller_declined','on_hold')),
    match_owner_id INTEGER REFERENCES users(id),     -- optional co-ordinator; buyer/seller owners come from the opps
    priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('urgent','high','normal','low')),
    -- latest evaluation (output 1–4 of PRD §8.3 kept separate)
    gate TEXT NOT NULL CHECK (gate IN ('pass','review','blocked')),
    outcome TEXT NOT NULL CHECK (outcome IN ('ready','near','blocked','insufficient')),
    fit_score INTEGER,                   -- 0–100 over known applicable criteria; NULL if nothing comparable
    coverage_pct INTEGER NOT NULL,       -- weighted share of applicable criteria known on both sides
    confidence TEXT NOT NULL CHECK (confidence IN ('low','medium','high')),
    readiness TEXT NOT NULL,
    rank_score REAL NOT NULL,
    all_in_total INTEGER,                -- seller's known all-in cash requirement at evaluation time
    all_in_complete INTEGER NOT NULL DEFAULT 0,
    buyer_ceiling INTEGER,
    headline TEXT,                       -- one-line reason ("₹4 L over ceiling", "near: 4 chairs vs 6 preferred")
    next_question TEXT,                  -- the single most valuable next step
    red_flags INTEGER NOT NULL DEFAULT 0,
    info_needed_from TEXT,               -- buyer | seller | both | NULL: whose answers the next questions need
    rules_version TEXT NOT NULL,
    buyer_fp TEXT NOT NULL,              -- fingerprints of the inputs; differ from current => stale (UAT-14)
    seller_fp TEXT NOT NULL,
    stale INTEGER NOT NULL DEFAULT 0,
    computed_at TEXT NOT NULL,
    -- suppression after "not suitable" / declined (UAT-49): resurfaces only when facts change or on revisit date
    suppressed INTEGER NOT NULL DEFAULT 0,
    suppressed_reason TEXT,
    suppressed_note TEXT,
    suppressed_buyer_version INTEGER,
    suppressed_seller_version INTEGER,
    revisit_on TEXT,
    notified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (buyer_opp_id, seller_opp_id)
);
CREATE INDEX ix_match_buyer ON match_candidates(buyer_opp_id);
CREATE INDEX ix_match_seller ON match_candidates(seller_opp_id);
CREATE INDEX ix_match_queue ON match_candidates(suppressed, outcome, rank_score);

CREATE TABLE match_snapshots (
    id {{ID}},
    candidate_id INTEGER NOT NULL REFERENCES match_candidates(id),
    rules_version TEXT NOT NULL,
    buyer_version INTEGER NOT NULL,
    seller_version INTEGER NOT NULL,
    trigger TEXT NOT NULL,               -- created | data_change | manual | resurfaced
    result TEXT NOT NULL,                -- JSON: gates, factors, scores, questions
    computed_by INTEGER REFERENCES users(id),
    computed_at TEXT NOT NULL
);
CREATE INDEX ix_match_snapshots ON match_snapshots(candidate_id, computed_at);

-- Decision timeline for a match (stage moves, rejections with reasons, interest, notes).
CREATE TABLE match_events (
    id {{ID}},
    candidate_id INTEGER NOT NULL REFERENCES match_candidates(id),
    event TEXT NOT NULL,
    from_stage TEXT,
    to_stage TEXT,
    reason TEXT,
    body TEXT,
    actor_id INTEGER REFERENCES users(id),
    at TEXT NOT NULL
);
CREATE INDEX ix_match_events ON match_events(candidate_id, at);
