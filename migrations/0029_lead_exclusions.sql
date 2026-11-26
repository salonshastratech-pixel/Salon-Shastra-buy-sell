-- Numbers that must not be imported again from sheets: leads the team marked cold / not interested, and records an
-- admin removed. A person who fills in a NEW form still comes in (that is fresh interest).
CREATE TABLE lead_exclusions (
    id {{ID}},
    phone TEXT NOT NULL UNIQUE,
    reason TEXT,
    created_at TEXT NOT NULL
);
/*pg*/ ALTER TABLE lead_exclusions ENABLE ROW LEVEL SECURITY;
/*pg*/ REVOKE ALL ON lead_exclusions FROM anon, authenticated;
