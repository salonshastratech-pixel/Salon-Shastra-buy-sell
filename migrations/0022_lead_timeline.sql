-- CRM basics on every record: when the lead actually came in (the ad / form time, not the import time) and when the
-- team first reached them (speed to first contact).
ALTER TABLE opportunities ADD COLUMN lead_created_at TEXT;
ALTER TABLE opportunities ADD COLUMN first_contact_at TEXT;
UPDATE opportunities SET lead_created_at = created_at WHERE lead_created_at IS NULL;
UPDATE opportunities SET first_contact_at = (SELECT MIN(a.at) FROM activities a WHERE a.entity_type = 'opportunity'
    AND a.entity_id = opportunities.id AND a.kind = 'call') WHERE first_contact_at IS NULL;
