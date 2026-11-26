-- Which tabs of a connected Google Sheet to bring in (JSON list of tab names). Empty = every lead tab.
ALTER TABLE connected_sheets ADD COLUMN tabs TEXT;
