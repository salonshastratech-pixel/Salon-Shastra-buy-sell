"""WhatsApp chats from Meta "Click to WhatsApp" ads become leads — directly from Meta, no third-party inbox.

When a new number writes to our WhatsApp number from an ad (Meta adds a `referral` to the message: ad headline, ad
body, source URL), TeamOS:
  * creates the lead once (the number is matched across every record — never a duplicate), as a salon owner if the
    ad or message is about selling / owning a salon / needing staff, otherwise as a buyer / investor;
  * shares it in turn (Leads & data), with a first call due in 10 minutes and the ad as the source;
  * links the chat, so the conversation is on the lead's timeline.
A number already in TeamOS is not duplicated: the existing record gets a note that they wrote in from the ad.
Chats that do not come from an ad become leads only when "new WhatsApp chats become leads" is switched on.
"""
from __future__ import annotations

import re
from datetime import timedelta

from ..timeutil import now_iso, now_utc, to_iso
from . import smart_import as SI
from . import tasks as T

ALL_CHATS_KEY = "wa_new_chats_as_leads"
SELLER_WORDS = re.compile(r"sell|sale of (my|our) salon|own a salon|my salon|i own|owner|struggling|need staff|"
                          r"hairdresser|stylist", re.I)


def all_chats_on(db) -> bool:
    return db.scalar("SELECT enabled FROM automation_settings WHERE key = ?", (ALL_CHATS_KEY,)) == 1


def on_inbound(db, phone: str, text: str, referral: dict | None, profile_name: str | None, at: str | None,
               conv_id: int | None) -> dict | None:
    """Returns {'opportunity_id', 'created': bool} when a lead was made or matched, else None."""
    ref = referral or {}
    from_ad = bool(ref) and (ref.get("source_type") in (None, "ad", "post") or ref.get("ctwa_clid"))
    contact = db.one("SELECT * FROM contacts WHERE phone_normalized = ? OR alt_phone_normalized = ? ORDER BY id LIMIT 1",
                     (phone, phone))
    headline = (ref.get("headline") or ref.get("body") or "").strip()[:150]
    if contact:
        opp = db.one("SELECT id, kind FROM opportunities WHERE contact_id = ? ORDER BY id DESC LIMIT 1", (contact["id"],))
        if opp and from_ad:
            T.add_activity(db, None, "opportunity", opp["id"], "system",
                           f"Wrote on WhatsApp from a Meta ad{(': ' + headline) if headline else ''} — “{(text or '')[:200]}”")
        return {"opportunity_id": opp["id"], "created": False} if opp else None
    if not (from_ad or all_chats_on(db)):
        return None
    if SI.judge_row((text or "").lower()) and "job" in (SI.judge_row((text or "").lower()) or "").lower():
        return None                                   # someone asking for a job, not a lead
    words = " ".join([headline, ref.get("body") or "", text or ""])
    kind = "seller" if SELLER_WORDS.search(words) else "buyer"
    from .progress import round_robin
    owner = round_robin(db, kind)
    actor = db.one("SELECT * FROM users WHERE role = 'super_admin' AND is_active = 1 ORDER BY id LIMIT 1")
    if not actor or not owner:
        return None
    name = re.sub(r"[^\w .'-]", "", (profile_name or "").strip())[:80] or "WhatsApp enquiry"
    due = to_iso(now_utc() + timedelta(minutes=10))
    detail = ("Click-to-WhatsApp ad" + (f": {headline}" if headline else "")) if from_ad else "New WhatsApp chat"
    base = {"full_name": name, "phone": phone[-10:], "source": "whatsapp", "source_note": detail[:200],
            "consent_contact": "1", "owner_id": str(owner)}
    if kind == "seller":
        from . import sellers as S
        base.update({"salon_name": "Salon name not given", "city": "City not given", "locality": "Area not given"})
        res = S.create_lead(db, actor, base, dedupe_decision="new_flagged", first_task_due=due, first_task_priority="high")
    else:
        from . import buyers as B
        base.update({"city": "City not given"})
        res = B.create_lead(db, actor, base, dedupe_decision="new_flagged", first_task_due=due)
    opp_id = res.get("opportunity_id")
    if not opp_id:
        return None
    db.execute("UPDATE opportunities SET lead_created_at = ?, source_detail = ?, lead_category = ? WHERE id = ?",
               (at or now_iso(), detail[:200], "help" if kind == "seller" else "buy_salon", opp_id))
    cid = db.scalar("SELECT contact_id FROM opportunities WHERE id = ?", (opp_id,))
    if conv_id:
        db.execute("UPDATE conversations SET contact_id = ?, assigned_to = COALESCE(assigned_to, ?) WHERE id = ?",
                   (cid, owner, conv_id))
    T.add_activity(db, actor["id"], "opportunity", opp_id, "system",
                   f"New lead from WhatsApp — {detail}. First message: “{(text or '')[:300]}”"
                   + (f"\nAd link: {ref['source_url']}" if ref.get("source_url") else ""))
    return {"opportunity_id": opp_id, "created": True}
