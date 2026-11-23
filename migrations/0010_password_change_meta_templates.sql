-- Milestone 7.3: users change their own password (temporary passwords must be changed), and message templates are
-- cross-checked with Meta (category, approved wording, status) before they can be used live.
ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN password_changed_at TEXT;

ALTER TABLE message_templates ADD COLUMN meta_category TEXT;      -- UTILITY | MARKETING (Meta's categories)
ALTER TABLE message_templates ADD COLUMN meta_status TEXT;        -- APPROVED / PENDING / REJECTED / PAUSED / NOT_FOUND
ALTER TABLE message_templates ADD COLUMN meta_body TEXT;          -- body text exactly as Meta holds it
ALTER TABLE message_templates ADD COLUMN meta_checked_at TEXT;
ALTER TABLE message_templates ADD COLUMN meta_issue TEXT;         -- why it cannot be used live (mismatch, rejected…)
