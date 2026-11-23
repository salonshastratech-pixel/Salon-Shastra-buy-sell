-- Milestone 7.1: each team member can send from their own WhatsApp Business number (same Meta business account).
-- users.wa_phone_number_id = Meta's "phone number ID" for that person's number; NULL = use the company number.
ALTER TABLE users ADD COLUMN wa_number TEXT;
ALTER TABLE users ADD COLUMN wa_phone_number_id TEXT;
-- The business number the customer last wrote to / was written from (WhatsApp's 24-hour window belongs to it).
ALTER TABLE conversations ADD COLUMN business_number_id TEXT;
ALTER TABLE messages ADD COLUMN from_number_id TEXT;
