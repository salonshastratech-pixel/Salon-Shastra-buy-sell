-- Connected sheets that bring new leads straight in (Meta ad leads): no preview to approve; first call within
-- minutes, shared between the team in turn.
ALTER TABLE connected_sheets ADD COLUMN auto_import INTEGER NOT NULL DEFAULT 0;
