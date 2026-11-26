"""Consultant helpers for the founder: a one-page morning desk, and a price guide for a running salon."""
from __future__ import annotations

from datetime import datetime, time, timedelta

from ..timeutil import IST, now_iso, now_utc, to_iso


# -- Price guide --------------------------------------------------------------------------------------------------
def price_guide(listing: dict) -> dict | None:
    """Rule of thumb used when small salons change hands: about 12–24 months of the owner's monthly profit, plus the
    rent deposit that moves to the buyer. A starting point for negotiation, never a valuation report."""
    sales, costs = listing.get("avg_monthly_sales"), listing.get("avg_monthly_expenses")
    deposit = listing.get("rent_deposit") or 0
    asking = listing.get("asking_price")
    if not sales or costs is None:
        return None
    profit = sales - costs
    if profit <= 0:
        return {"profit": profit, "low": None, "high": None, "asking": asking, "verdict": "loss",
                "text": "Running at a loss: a buyer pays mainly for the set-up (interiors, equipment) and the deposit. "
                        "Check what it would cost to build the same salon new — that is the ceiling."}
    low, high = profit * 12 + deposit, profit * 24 + deposit
    out = {"profit": profit, "low": low, "high": high, "asking": asking, "deposit": deposit}
    if not asking:
        out.update(verdict="no_ask", text="No asking price yet — this range helps set a realistic one.")
    elif asking > high * 1.15:
        out.update(verdict="high", text=f"Asking is about {round(100 * (asking / high - 1))}% above the top of the "
                                        "guide — expect a long sale unless the owner comes down or shows more profit.")
    elif asking < low * 0.9:
        out.update(verdict="low", text="Asking is below the guide — attractive for buyers; check for hidden problems "
                                       "(lease, dues, staff leaving).")
    else:
        out.update(verdict="fair", text="Asking is within the guide — a fair, sellable price.")
    return out


# -- Founder desk -------------------------------------------------------------------------------------------------
def desk(db) -> dict:
    local = now_utc().astimezone(IST)
    day_start = to_iso(datetime.combine(local.date(), time(0, 0), tzinfo=IST))
    day_end = to_iso(datetime.combine(local.date() + timedelta(days=1), time(0, 0), tzinfo=IST))
    base = ("SELECT o.id, o.kind, o.ref_code, c.full_name AS contact_name, b.name AS salon_name, u.full_name AS owner_name "
            "FROM opportunities o JOIN contacts c ON c.id = o.contact_id LEFT JOIN salon_businesses b ON "
            "b.id = o.business_id LEFT JOIN users u ON u.id = o.primary_owner_id ")
    calls = db.all("SELECT vb.*, o.kind, o.ref_code, c.full_name AS contact_name, b.name AS salon_name FROM vikas_bookings vb "
                   "JOIN opportunities o ON o.id = vb.opportunity_id JOIN contacts c ON c.id = o.contact_id "
                   "LEFT JOIN salon_businesses b ON b.id = o.business_id WHERE (vb.status = 'booked' AND vb.starts_at >= ? "
                   "AND vb.starts_at < ?) OR vb.status = 'requested' ORDER BY CASE WHEN vb.starts_at IS NULL THEN 1 ELSE 0 "
                   "END, vb.starts_at LIMIT 20", (day_start, day_end))
    swot = db.all(base + "JOIN swot_reports r ON r.opportunity_id = o.id WHERE r.status = 'draft' AND r.id = "
                  "(SELECT MAX(id) FROM swot_reports x WHERE x.opportunity_id = o.id) AND o.status = 'active' "
                  "ORDER BY r.created_at LIMIT 15")
    inv_reports = db.all(base + "JOIN investor_reports r ON r.opportunity_id = o.id WHERE r.status = 'draft' AND r.id = "
                         "(SELECT MAX(id) FROM investor_reports x WHERE x.opportunity_id = o.id) AND o.status = 'active' "
                         "ORDER BY r.created_at LIMIT 15")
    hot = db.all(base + "JOIN investor_profiles p ON p.opportunity_id = o.id WHERE p.screen_verdict = 'pursue' AND "
                 "o.status = 'active' AND NOT EXISTS (SELECT 1 FROM vikas_bookings vb WHERE vb.opportunity_id = o.id AND "
                 "vb.status IN ('requested', 'booked', 'done')) ORDER BY p.screened_at DESC LIMIT 10")
    sellers = db.all(base + "JOIN seller_listings l ON l.opportunity_id = o.id WHERE l.sale_purpose = 'sell_entire' AND "
                     "o.status = 'active' ORDER BY o.updated_at DESC LIMIT 10")
    ready = db.scalar("SELECT COUNT(*) FROM match_candidates mc JOIN opportunities bo ON bo.id = mc.buyer_opp_id "
                      "JOIN opportunities so ON so.id = mc.seller_opp_id WHERE bo.status = 'active' AND so.status = "
                      "'active' AND mc.suppressed = 0 AND mc.readiness = 'ready_to_approach'") or 0
    intros = db.scalar("SELECT COUNT(*) FROM introductions WHERE status = 'requested'") or 0
    fees_consult = db.all(base + "JOIN consultations k ON k.opportunity_id = o.id WHERE k.status = 'done' AND k.paid = 0 "
                          "AND k.fee > 0 ORDER BY k.scheduled_at LIMIT 10")
    fees_reg = db.all(base + "JOIN investor_profiles p ON p.opportunity_id = o.id WHERE p.reg_status = 'due' LIMIT 10")
    day_ago = to_iso(now_utc() - timedelta(hours=24))
    waiting = db.all("SELECT u.full_name AS owner_name, COUNT(*) AS n FROM opportunities o LEFT JOIN users u ON "
                     "u.id = o.primary_owner_id WHERE o.status = 'active' AND o.first_contact_at IS NULL AND "
                     "COALESCE(o.lead_created_at, o.created_at) < ? GROUP BY u.full_name ORDER BY n DESC", (day_ago,))
    team = db.all("SELECT id, full_name, work_status, work_status_at FROM users WHERE is_active = 1 AND role IN "
                  "('team_member', 'team_lead') ORDER BY full_name")
    from .lead_marks import LEAD_AT
    new_today = db.scalar(f"SELECT COUNT(*) FROM opportunities o WHERE {LEAD_AT} >= ?",
                          (day_start,)) or 0
    return {"calls": calls, "swot": swot, "inv_reports": inv_reports, "hot": hot, "sellers": sellers,
            "ready": ready, "intros": intros, "fees_consult": fees_consult, "fees_reg": fees_reg,
            "waiting": waiting, "waiting_total": sum(w["n"] for w in waiting), "team": team, "new_today": new_today,
            "now": now_iso()}
