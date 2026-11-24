-- Team availability (like the "working hours" and "out of office" settings in leading CRMs):
-- each person's live status, weekly working hours and time off. Leave stops new leads being given to them.

ALTER TABLE users ADD COLUMN work_status TEXT;        -- available | busy | in_field | on_break | offline (NULL = available)
ALTER TABLE users ADD COLUMN work_status_note TEXT;
ALTER TABLE users ADD COLUMN work_status_at TEXT;

CREATE TABLE working_hours (
    id {{ID}},
    user_id INTEGER NOT NULL REFERENCES users(id),
    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),   -- 0 = Monday
    is_off INTEGER NOT NULL DEFAULT 0,
    start_time TEXT,                                             -- 'HH:MM' IST
    end_time TEXT,
    UNIQUE (user_id, weekday)
);

CREATE TABLE time_off (
    id {{ID}},
    user_id INTEGER NOT NULL REFERENCES users(id),
    start_date TEXT NOT NULL,                                    -- 'YYYY-MM-DD' IST, inclusive
    end_date TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('leave','sick','half_day','field','training','holiday')),
    note TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    cancelled_at TEXT
);
CREATE INDEX ix_time_off_user ON time_off(user_id, start_date);
CREATE INDEX ix_time_off_dates ON time_off(start_date, end_date);
