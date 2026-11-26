-- Speed: remember that a record was already compared with every record on the other side at a given fingerprint
-- (what we knew about it then), even when no pair was worth saving. Opening its profile then shows the stored
-- matches without re-comparing it against hundreds of records on every visit.
CREATE TABLE match_scored (
    opportunity_id INTEGER PRIMARY KEY REFERENCES opportunities(id),
    fp TEXT NOT NULL,
    scored_at TEXT NOT NULL
);
/*pg*/ ALTER TABLE match_scored ENABLE ROW LEVEL SECURITY;
/*pg*/ REVOKE ALL ON match_scored FROM anon, authenticated;
