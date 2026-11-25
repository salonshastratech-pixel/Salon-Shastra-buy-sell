-- New-salon investors who would also consider taking over a running salon (often faster and cheaper) are
-- matched with owners who are selling. 'yes' is their explicit confirmation; 'no' keeps them out.
ALTER TABLE buyer_mandates ADD COLUMN consider_takeover TEXT;
