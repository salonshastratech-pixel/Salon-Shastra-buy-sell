-- Standard appointment lengths for every user: phone / video call with Vikas 10 minutes, meeting at our office
-- 3 hours (salon or place visits, 5 hours, are worked out from their type). Existing free times and booked calls follow.
UPDATE vikas_slots SET minutes = CASE WHEN mode = 'office' THEN 180 ELSE 10 END WHERE status <> 'cancelled';
