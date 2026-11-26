-- What the team already knows about each lead, kept on the record for sorting and filtering:
-- temperature (hot / warm / normal), category (sell, needs help, needs staff, new salon, buy a salon, investor)
-- and the team's latest remark from the sheets.
ALTER TABLE opportunities ADD COLUMN lead_temperature TEXT;
ALTER TABLE opportunities ADD COLUMN lead_category TEXT;
ALTER TABLE opportunities ADD COLUMN team_remark TEXT;
