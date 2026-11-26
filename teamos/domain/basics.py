"""Editing a record's basics from its profile: name, mobile numbers, e-mail, salon name, city and area, and the team's
marks (hot / warm / normal / cold, category, latest remark). Every change is checked, written to the timeline and the
audit log; a mobile number already used by someone else is refused (no silent duplicates)."""
from __future__ import annotations

from .. import audit
from ..permissions import user_can
from ..timeutil import now_iso
from . import tasks as T
from .common import normalize_indian_mobile
from .errors import PermissionDenied, ValidationError
from .lead_marks import CATEGORIES, TEMP_LABELS

LABELS = {"full_name": "Name", "phone_normalized": "Mobile", "alt_phone_normalized": "Alternate mobile",
          "email": "E-mail", "salon_name": "Salon name", "city": "City", "locality": "Area",
          "lead_temperature": "Hot / warm / cold", "lead_category": "Category", "team_remark": "Team remark"}


def update(db, actor: dict, opp: dict, data) -> list[str]:
    """Returns the labels of what changed."""
    if not user_can(db, actor, "crm.edit"):
        raise PermissionDenied("crm.edit")
    kind = opp["kind"]
    errors, contact, business, mark = {}, {}, {}, {}
    name = (data.get("full_name") or "").strip()
    if not name:
        errors["full_name"] = "Enter a name."
    else:
        contact["full_name"] = name[:120]
    phone, perr = normalize_indian_mobile(data.get("phone"))
    if perr:
        errors["phone"] = perr
    else:
        other = db.scalar("SELECT id FROM contacts WHERE (phone_normalized = ? OR alt_phone_normalized = ?) AND id <> ?",
                          (phone, phone, opp["contact_id"]))
        if other:
            errors["phone"] = "This mobile number belongs to another record — search for it before changing."
        contact["phone_normalized"] = phone
    alt_raw = (data.get("alt_phone") or "").strip()
    if alt_raw:
        alt, aerr = normalize_indian_mobile(alt_raw)
        if aerr:
            errors["alt_phone"] = aerr
        else:
            contact["alt_phone_normalized"] = alt
    else:
        contact["alt_phone_normalized"] = None
    email = (data.get("email") or "").strip()
    if email and ("@" not in email or "." not in email.split("@")[-1]):
        errors["email"] = "Enter a valid e-mail (or leave it empty)."
    contact["email"] = email[:200] or None
    city, area = (data.get("city") or "").strip()[:80], (data.get("locality") or "").strip()[:80]
    if kind == "seller":
        salon = (data.get("salon_name") or "").strip()
        if not salon:
            errors["salon_name"] = "Enter the salon name."
        business = {"name": salon[:120], "city": city or None, "locality": area or None}
    else:
        contact.update({"city": city or None, "locality": area or None})
    temp = data.get("lead_temperature") or "normal"
    if temp not in TEMP_LABELS:
        errors["lead_temperature"] = "Choose hot, warm, normal or cold."
    cats = [c for c in (data.getlist("lead_category") if hasattr(data, "getlist") else data.get("lead_category") or [])
            if c in dict(CATEGORIES[kind])]
    mark = {"lead_temperature": temp, "lead_category": ",".join(cats) or None,
            "team_remark": (data.get("team_remark") or "").strip()[:400] or None}
    if errors:
        raise ValidationError(errors)
    before_c = db.one("SELECT * FROM contacts WHERE id = ?", (opp["contact_id"],))
    before_b = db.one("SELECT * FROM salon_businesses WHERE id = ?", (opp["business_id"],)) if opp.get("business_id") else {}
    before_o = db.one("SELECT * FROM opportunities WHERE id = ?", (opp["id"],))
    changed = []
    with db:
        c_set = {k: v for k, v in contact.items() if before_c.get(k) != v}
        if c_set:
            db.execute("UPDATE contacts SET " + ", ".join(f"{k} = ?" for k in c_set) + " WHERE id = ?",
                       (*c_set.values(), opp["contact_id"]))
            changed += [LABELS[k] for k in c_set]
        b_set = {k: v for k, v in business.items() if before_b and before_b.get(k) != v}
        if b_set:
            db.execute("UPDATE salon_businesses SET " + ", ".join(f"{k} = ?" for k in b_set) + " WHERE id = ?",
                       (*b_set.values(), opp["business_id"]))
            changed += [{"name": "Salon name", "city": "City", "locality": "Area"}[k] for k in b_set]
        o_set = {k: v for k, v in mark.items() if before_o.get(k) != v}
        if o_set:
            db.execute("UPDATE opportunities SET " + ", ".join(f"{k} = ?" for k in o_set) + ", updated_at = ? WHERE id = ?",
                       (*o_set.values(), now_iso(), opp["id"]))
            changed += [LABELS[k] for k in o_set]
        if changed:
            T.add_activity(db, actor["id"], "opportunity", opp["id"], "data_update", "Details edited: " + ", ".join(changed))
            audit.record(db, actor["id"], "record.basics_edited", "opportunity", opp["id"],
                         {"fields": changed, "before": {k: before_c.get(k) for k in c_set}})
    return changed
