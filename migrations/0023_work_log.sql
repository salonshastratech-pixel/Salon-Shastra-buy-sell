-- Work log for flexible / home working: every sign-in, sign-out and status change (available, busy, field visit,
-- break, off for the day) with its time. Hours on duty, breaks and field time are worked out from this.
CREATE TABLE work_log (
    id {{ID}},
    user_id INTEGER NOT NULL REFERENCES users(id),
    event TEXT NOT NULL CHECK (event IN ('login','logout','status')),
    status TEXT,
    note TEXT,
    at TEXT NOT NULL
);
CREATE INDEX ix_work_log_user_at ON work_log(user_id, at);
/*pg*/ ALTER TABLE work_log ENABLE ROW LEVEL SECURITY;
/*pg*/ REVOKE ALL ON work_log FROM anon, authenticated;
