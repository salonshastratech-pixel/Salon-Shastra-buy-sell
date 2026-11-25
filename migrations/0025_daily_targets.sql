-- Daily targets per person (set by an admin): calls, conversations (connected calls), first contacts with new
-- leads, forms sent. Progress shows on the person's My Day and in the admin's work log.
ALTER TABLE users ADD COLUMN target_calls INTEGER;
ALTER TABLE users ADD COLUMN target_connects INTEGER;
ALTER TABLE users ADD COLUMN target_first_contacts INTEGER;
ALTER TABLE users ADD COLUMN target_forms INTEGER;
