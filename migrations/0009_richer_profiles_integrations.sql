-- Milestone 7.2: richer seller facts (premises, running costs, licences), matching buyer preferences,
-- and admin-configurable integrations.

-- Seller: when started, exact location, premises, running costs, licences
ALTER TABLE seller_listings ADD COLUMN started_year INTEGER;
ALTER TABLE seller_listings ADD COLUMN maps_link TEXT;
ALTER TABLE seller_listings ADD COLUMN floor_level TEXT;
ALTER TABLE seller_listings ADD COLUMN area_type TEXT;
ALTER TABLE seller_listings ADD COLUMN frontage_ft INTEGER;
ALTER TABLE seller_listings ADD COLUMN parking TEXT;
ALTER TABLE seller_listings ADD COLUMN signage_visible TEXT;
ALTER TABLE seller_listings ADD COLUMN washrooms INTEGER;
ALTER TABLE seller_listings ADD COLUMN ac_type TEXT;
ALTER TABLE seller_listings ADD COLUMN equipment_age_years INTEGER;
ALTER TABLE seller_listings ADD COLUMN maintenance_monthly INTEGER;
ALTER TABLE seller_listings ADD COLUMN electricity_monthly INTEGER;
ALTER TABLE seller_listings ADD COLUMN gst_on_rent TEXT;
ALTER TABLE seller_listings ADD COLUMN monthly_salaries INTEGER;
ALTER TABLE seller_listings ADD COLUMN top_services TEXT;
ALTER TABLE seller_listings ADD COLUMN google_rating TEXT;
ALTER TABLE seller_listings ADD COLUMN booking_software TEXT;
ALTER TABLE seller_listings ADD COLUMN trade_licence TEXT;
ALTER TABLE seller_listings ADD COLUMN gst_registered TEXT;
ALTER TABLE seller_listings ADD COLUMN shop_establishment TEXT;

-- Buyer: parking need (floor and type of area use buyer_preferences rows, like format)
ALTER TABLE buyer_mandates ADD COLUMN parking_need TEXT;

-- Admin-configurable integrations. Secrets are stored encrypted (never shown back); environment variables on the
-- server still override, for operators who prefer them.
CREATE TABLE integration_settings (
    id {{ID}},
    key TEXT NOT NULL UNIQUE,            -- e.g. WHATSAPP_PHONE_NUMBER_ID
    value TEXT,                          -- plain value, or Fernet token when is_secret = 1
    is_secret INTEGER NOT NULL DEFAULT 0,
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT NOT NULL
);
