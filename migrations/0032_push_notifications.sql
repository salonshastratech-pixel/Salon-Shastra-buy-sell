-- Push notifications to phones and computers. One row per browser/phone that said "yes" to notifications.
-- notify_prefs: each person's choice of what to be woken for (JSON; missing = everything on).
CREATE TABLE push_subscriptions (
    id {{ID}},
    user_id INTEGER NOT NULL REFERENCES users(id),
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT,
    auth TEXT,
    device TEXT,
    created_at TEXT NOT NULL,
    last_push_at TEXT,
    last_ok_at TEXT,
    failures INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_push_subscriptions_user ON push_subscriptions (user_id);
ALTER TABLE users ADD COLUMN notify_prefs TEXT;
/*pg*/ ALTER TABLE push_subscriptions ENABLE ROW LEVEL SECURITY;
/*pg*/ REVOKE ALL ON push_subscriptions FROM anon, authenticated;
