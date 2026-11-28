"""Move a lead to the right side: a salon owner who answered a buyer ad (or a buyer filed under Salon Owners).

Nothing is deleted. A new record is made on the right side for the same person, and everything the team did comes
with it: calls and notes, open tasks, WhatsApp chats, Vikas bookings, hot/warm/cold, team remark, when the lead came in
and when they were first contacted. The old record is closed as "Moved" with a link to the new one — it stays so the
team's sheets keep matching it (no duplicate is made on the next sync), and so the history of what happened is clear.
If the person really is both (owns a salon AND wants to buy one), "keep both" leaves the old record open instead.
"""
from __future__ import annotations

from .. import audit
from ..permissions import user_can
from ..timeutil import now_iso
from . import buyers as B
from . import sellers as S
from . import tasks as T
from .errors import PermissionDenied, ValidationError
from .lead_marks import CATEGORIES

SIDE_LABEL = {"seller": "Salon Owners", "buyer": "Buyers / Investors"}
MOVED_STAGE = "moved"


def load_any(db, opp_id: int) -> dict | None:
    kind = db.scalar("SELECT kind FROM opportunities WHERE id = ?", (opp_id,))
    return (S.load(db, opp_id) if kind == "seller" else B.load(db, opp_id)) if kind else None


def defaults(db, opp: dict) -> dict:
    """What the move form starts with."""
    c = db.one("SELECT * FROM contacts WHERE id = ?", (opp["contact_id"],)) or {}
    salon = opp.get("salon_name") or ""
    return {"salon_name": "" if opp["kind"] == "seller" else salon,
            "city": opp.get("city") or c.get("city") or "", "locality": opp.get("locality") or c.get("locality") or "",
            "categories": []}


def move(db, actor: dict, opp_id: int, to_kind: str, data, keep_old: bool = False) -> int:
    """Make the record on the other side and carry everything over. Returns the new record's id."""
    if not user_can(db, actor, "crm.edit") or not user_can(db, actor, "crm.create"):
        raise PermissionDenied("crm.edit")
    opp = load_any(db, opp_id)
    if not opp:
        raise ValidationError({"record": "Record not found."})
    if to_kind not in ("seller", "buyer") or to_kind == opp["kind"]:
        raise ValidationError({"to": "Choose the other side."})
    if opp.get("stage") == MOVED_STAGE:
        raise ValidationError({"to": "This record was already moved — open the new one instead."})
    c = db.one("SELECT * FROM contacts WHERE id = ?", (opp["contact_id"],))
    if not c.get("phone_normalized"):
        raise ValidationError({"phone": "Add their mobile number first (Edit details), then move the lead."})
    get = (lambda k: data.get(k)) if hasattr(data, "get") else (lambda k: None)
    cats_in = data.getlist("lead_category") if hasattr(data, "getlist") else (data.get("lead_category") or [])
    cats = [k for k in cats_in if k in dict(CATEGORIES[to_kind])]
    source = opp["source"] if opp["source"] in S.SOURCE_LABELS else "manual_call"
    form = {"full_name": c["full_name"], "phone": c["phone_normalized"], "email": c.get("email") or "",
            "city": (get("city") or "").strip() or c.get("city") or "",
            "locality": (get("locality") or "").strip() or c.get("locality") or "",
            "source": source, "owner_id": str(opp["primary_owner_id"] or actor["id"]),
            "consent_contact": "1", "source_note": f"Moved from {opp['ref_code']}"}
    if to_kind == "seller":
        form["salon_name"] = (get("salon_name") or "").strip()
    now = now_iso()
    with db:
        if opp["primary_owner_id"] and opp["primary_owner_id"] != actor["id"] and not user_can(db, actor, "crm.assign"):
            form["owner_id"] = str(actor["id"])          # cannot create for someone else: the mover keeps it
        maker = S.create_lead if to_kind == "seller" else B.create_lead
        r = maker(db, actor, form, dedupe_decision=f"link:{c['id']}")
        if "opportunity_id" not in r:
            raise ValidationError({"to": "Could not link to this person's contact."})
        new_id = r["opportunity_id"]
        new_ref = db.scalar("SELECT ref_code FROM opportunities WHERE id = ?", (new_id,))
        # what the team already knows about the lead
        carry = {"lead_created_at": opp.get("lead_created_at") or opp.get("created_at"),
                 "first_contact_at": opp.get("first_contact_at"), "last_contact_at": opp.get("last_contact_at"),
                 "lead_temperature": opp.get("lead_temperature"), "team_remark": opp.get("team_remark"),
                 "lead_category": ",".join(cats) or None, "priority": opp.get("priority") or "normal",
                 "summary": opp.get("summary"), "summary_at": opp.get("summary_at"),
                 "summary_by": opp.get("summary_by"), "summary_source": opp.get("summary_source")}
        stages = S.STAGE_LABELS if to_kind == "seller" else B.STAGE_LABELS
        old_stage = opp.get("stage")
        carry["stage"] = old_stage if old_stage in ("contact_attempted", "connected") and old_stage in stages else (
            "connected" if opp.get("first_contact_at") else "new")
        db.execute("UPDATE opportunities SET " + ", ".join(f"{k} = ?" for k in carry) + ", updated_at = ? WHERE id = ?",
                   (*carry.values(), now, new_id))
        if carry["stage"] != "new":
            db.insert("stage_history", {"opportunity_id": new_id, "from_stage": "new", "to_stage": carry["stage"],
                                        "reason": f"Carried over from {opp['ref_code']}", "actor_id": actor["id"],
                                        "at": now})
        if not keep_old:
            # history and open work travel with the person (counted once, on the record the team now uses)
            db.execute("UPDATE activities SET entity_id = ? WHERE entity_type = 'opportunity' AND entity_id = ?",
                       (new_id, opp_id))
            open_tasks = db.all("SELECT id, task_type FROM tasks WHERE entity_type = 'opportunity' AND entity_id = ? "
                                "AND status = 'open'", (opp_id,))
            if opp.get("first_contact_at") or any(t["task_type"] != "first_contact" for t in open_tasks):
                # already spoken to, or a next step is planned: no new 'First call' on the new record
                db.execute("UPDATE tasks SET status = 'cancelled', outcome = ?, updated_at = ? WHERE "
                           "entity_type = 'opportunity' AND entity_id = ? AND status = 'open' AND "
                           "task_type = 'first_contact'", (f"Carried over from {opp['ref_code']}", now, new_id))
            for t in open_tasks:
                if t["task_type"] == "first_contact":   # the new record has its own (or needs none: already called)
                    db.execute("UPDATE tasks SET status = 'cancelled', outcome = ?, updated_at = ? WHERE id = ?",
                               (f"Lead moved to {SIDE_LABEL[to_kind]}", now, t["id"]))
                    continue
                db.execute("UPDATE tasks SET entity_id = ?, updated_at = ? WHERE id = ?", (new_id, now, t["id"]))
            db.execute("UPDATE communications SET opportunity_id = ? WHERE opportunity_id = ?", (new_id, opp_id))
            db.execute("UPDATE vikas_bookings SET opportunity_id = ? WHERE opportunity_id = ?", (new_id, opp_id))
            for cl in db.all("SELECT conversation_id FROM conversation_links WHERE opportunity_id = ?", (opp_id,)):
                if not db.scalar("SELECT 1 FROM conversation_links WHERE conversation_id = ? AND opportunity_id = ?",
                                 (cl["conversation_id"], new_id)):
                    db.execute("UPDATE conversation_links SET opportunity_id = ? WHERE conversation_id = ? AND "
                               "opportunity_id = ?", (new_id, cl["conversation_id"], opp_id))
            db.execute("UPDATE opportunities SET stage = ?, status = 'closed', closed_reason = ?, next_action = NULL, "
                       "next_action_due = NULL, updated_at = ? WHERE id = ?",
                       (MOVED_STAGE, f"Moved to {SIDE_LABEL[to_kind]} as {new_ref}", now, opp_id))
            db.insert("stage_history", {"opportunity_id": opp_id, "from_stage": old_stage, "to_stage": MOVED_STAGE,
                                        "reason": f"Moved to {SIDE_LABEL[to_kind]} as {new_ref}",
                                        "actor_id": actor["id"], "at": now})
        why = (get("reason") or "").strip()[:200]
        T.add_activity(db, actor["id"], "opportunity", new_id, "system",
                       f"Moved here from {SIDE_LABEL[opp['kind']]} ({opp['ref_code']}) by {actor['full_name']}"
                       + (f" — {why}" if why else "")
                       + ("; the old record stays open too" if keep_old else "; calls, notes and tasks came along"))
        T.add_activity(db, actor["id"], "opportunity", opp_id, "system",
                       f"{'Also added' if keep_old else 'Moved'} to {SIDE_LABEL[to_kind]} as {new_ref}"
                       + (f" — {why}" if why else ""))
        if to_kind == "seller" and cats:
            purpose = "sell_entire" if "sell" in cats else "seek_help"
            lid = db.scalar("SELECT id FROM seller_listings WHERE opportunity_id = ?", (new_id,))
            if lid:
                db.execute("UPDATE seller_listings SET sale_purpose = ? WHERE id = ? AND sale_purpose IS NULL",
                           (purpose, lid))
        T.refresh_next_action(db, "opportunity", new_id)
        audit.record(db, actor["id"], "record.moved_side", "opportunity", opp_id,
                     {"to": to_kind, "new_id": new_id, "keep_old": keep_old})
    return new_id


def moved_to(db, opp: dict) -> dict | None:
    """For a record closed as moved: the record it became (to link to it)."""
    if opp.get("stage") != MOVED_STAGE:
        return None
    return db.one("SELECT id, kind, ref_code FROM opportunities WHERE contact_id = ? AND kind != ? AND id > ? "
                  "ORDER BY id LIMIT 1", (opp["contact_id"], opp["kind"], opp["id"]))


# -- Which form to send --------------------------------------------------------------------------------------------
# (key, side the form belongs to, button label, one-line "when to use it")
FORMS = [
    ("owner_checkup", "seller", "Salon check-up form (free SWOT report)",
     "Owner who needs help growing — clients, staff, marketing. They get a free SWOT report."),
    ("owner_sell", "seller", "Sell my salon — full details",
     "Owner who wants to sell: price, rent, staff, equipment, photos."),
    ("investor", "buyer", "Investor form (readiness report)",
     "Wants to invest or open a salon: budget, time, involvement. They get a readiness report."),
    ("buyer_full", "buyer", "Buy a salon — full requirements",
     "Wants to buy a running salon: areas, size, budget, timeline — used for matching."),
]
FORM_SIDE = {k: side for k, side, _l, _w in FORMS}
FORM_LABEL = {k: label for k, _s, label, _w in FORMS}


def deep_form_message(opp: dict, link: str, key: str) -> str:
    first = (opp.get("contact_name") or "").split(" ")[0] or "Sir/Madam"
    what = ("the details of the salon you want to sell" if key == "owner_sell"
            else "what you are looking for in a salon")
    return (f"Hello {first}, thank you for speaking with Salon Shastra.\n\nPlease share {what} here:\n{link}\n\n"
            "Your answers are saved as you go — you can stop and continue later from the same link.\n\n"
            "— Team Salon Shastra")


# -- Same person filed on both sides by an import: fold the extra record into the right one ------------------------
def merge_into(db, actor: dict, from_id: int, into_id: int, why: str) -> None:
    """Fold record `from_id` into `into_id` (same person): calls, notes, open tasks, chats and bookings move over,
    empty facts are filled from it, and it is closed as Moved (kept, never deleted)."""
    src, dst = load_any(db, from_id), load_any(db, into_id)
    if not src or not dst or src["id"] == dst["id"]:
        raise ValidationError({"record": "Both records must exist."})
    if src.get("stage") == MOVED_STAGE:
        return
    now = now_iso()
    with db:
        db.execute("UPDATE activities SET entity_id = ? WHERE entity_type = 'opportunity' AND entity_id = ?",
                   (into_id, from_id))
        for t in db.all("SELECT id, task_type FROM tasks WHERE entity_type = 'opportunity' AND entity_id = ? AND "
                        "status = 'open'", (from_id,)):
            if t["task_type"] == "first_contact":       # the owner record has its own first call / next step
                db.execute("UPDATE tasks SET status = 'cancelled', outcome = ?, updated_at = ? WHERE id = ?",
                           (f"Same person as {dst['ref_code']}", now, t["id"]))
            else:
                db.execute("UPDATE tasks SET entity_id = ?, updated_at = ? WHERE id = ?", (into_id, now, t["id"]))
        db.execute("UPDATE communications SET opportunity_id = ? WHERE opportunity_id = ?", (into_id, from_id))
        db.execute("UPDATE vikas_bookings SET opportunity_id = ? WHERE opportunity_id = ?", (into_id, from_id))
        for cl in db.all("SELECT conversation_id FROM conversation_links WHERE opportunity_id = ?", (from_id,)):
            if db.scalar("SELECT 1 FROM conversation_links WHERE conversation_id = ? AND opportunity_id = ?",
                         (cl["conversation_id"], into_id)):
                db.execute("DELETE FROM conversation_links WHERE conversation_id = ? AND opportunity_id = ?",
                           (cl["conversation_id"], from_id))
            else:
                db.execute("UPDATE conversation_links SET opportunity_id = ? WHERE conversation_id = ? AND "
                           "opportunity_id = ?", (into_id, cl["conversation_id"], from_id))
        heat = {"hot": 3, "warm": 2, "normal": 1, "cold": 0}
        fill = {}
        if heat.get(src.get("lead_temperature") or "normal", 1) > heat.get(dst.get("lead_temperature") or "normal", 1):
            fill["lead_temperature"] = src["lead_temperature"]
        if src.get("team_remark") and src["team_remark"] not in (dst.get("team_remark") or ""):
            fill["team_remark"] = ((dst.get("team_remark") + " · ") if dst.get("team_remark") else "") + src["team_remark"]
        for k, pick in (("first_contact_at", min), ("lead_created_at", min), ("last_contact_at", max)):
            vals = [v for v in (src.get(k), dst.get(k)) if v]
            if vals and pick(vals) != dst.get(k):
                fill[k] = pick(vals)
        if fill:
            db.execute("UPDATE opportunities SET " + ", ".join(f"{k} = ?" for k in fill) + ", updated_at = ? WHERE id = ?",
                       (*fill.values(), now, into_id))
        db.execute("UPDATE opportunities SET stage = ?, status = 'closed', closed_reason = ?, next_action = NULL, "
                   "next_action_due = NULL, updated_at = ? WHERE id = ?",
                   (MOVED_STAGE, f"Same person as {dst['ref_code']} — {why}"[:300], now, from_id))
        db.insert("stage_history", {"opportunity_id": from_id, "from_stage": src.get("stage"), "to_stage": MOVED_STAGE,
                                    "reason": f"Same person as {dst['ref_code']}", "actor_id": actor["id"], "at": now})
        T.add_activity(db, actor["id"], "opportunity", into_id, "system",
                       f"{src['ref_code']} ({SIDE_LABEL[src['kind']]}) was the same person — {why}; its calls, notes "
                       "and tasks are here now")
        T.add_activity(db, actor["id"], "opportunity", from_id, "system", f"Merged into {dst['ref_code']} — {why}")
        T.refresh_next_action(db, "opportunity", into_id)
        audit.record(db, actor["id"], "record.merged_side", "opportunity", from_id, {"into": into_id, "why": why})


def fix_side_duplicates(db, actor: dict, phones: list[str], keep: str = "seller", dry: bool = True,
                        why: str = "a salon owner (pilot Tracker)") -> dict:
    """For people known to be on one side (`keep`), fold the other-side records an import made for them. A record on
    the other side that holds real answers of that side (budget, a filled investor form, a deal) is left alone and
    reported instead."""
    other = "buyer" if keep == "seller" else "seller"
    out = {"merged": [], "kept_both": [], "not_found": []}
    for ph in phones:
        rows = db.all("SELECT o.* FROM opportunities o JOIN contacts c ON c.id = o.contact_id WHERE "
                      "(c.phone_normalized = ? OR c.alt_phone_normalized = ?) AND o.stage <> ? ORDER BY o.id",
                      (ph, ph, MOVED_STAGE))
        main = next((r for r in rows if r["kind"] == keep), None)
        extras = [r for r in rows if r["kind"] == other]
        if not main or not extras:
            out["not_found"].append(ph)
            continue
        for x in extras:
            real = False
            if other == "buyer":
                m = db.one("SELECT * FROM buyer_mandates WHERE opportunity_id = ?", (x["id"],)) or {}
                real = any(m.get(k) is not None for k in ("all_in_max", "budget_max", "budget_min")) or bool(
                    db.scalar("SELECT 1 FROM investor_profiles WHERE opportunity_id = ? AND form_status = 'filled'",
                              (x["id"],))) or bool(db.scalar("SELECT 1 FROM deals WHERE buyer_opp_id = ?", (x["id"],)))
            else:
                lst = db.one("SELECT * FROM seller_listings WHERE opportunity_id = ?", (x["id"],)) or {}
                real = lst.get("asking_price") is not None or bool(db.scalar(
                    "SELECT 1 FROM pilot_profiles WHERE opportunity_id = ? AND form_status = 'filled'", (x["id"],))) or bool(
                    db.scalar("SELECT 1 FROM swot_reports WHERE opportunity_id = ?", (x["id"],))) or bool(
                    db.scalar("SELECT 1 FROM deals WHERE seller_opp_id = ?", (x["id"],)))
            if real or x["source"] != "import":
                out["kept_both"].append(f"{x['ref_code']} (has its own {other} details — please check)")
                continue
            if not dry:
                merge_into(db, actor, x["id"], main["id"], why)
            out["merged"].append(f"{x['ref_code']} → {main['ref_code']}")
    return out


# -- Same person, same side, two records (older imports / repeated form sends) -------------------------------------
MOVE_TABLES = ("form_invitations", "form_submissions", "documents", "communications", "messages", "pilot_links",
               "pilot_responses", "swot_reports", "vikas_bookings", "consultations", "investor_responses",
               "investor_reports", "status_links", "fee_agreements", "import_rows", "sheet_import_rows",
               "lead_ad_events", "batch_recipients")
SKIP_COLS = {"id", "opportunity_id", "business_id", "created_at", "updated_at", "data_version", "approval_state",
             "criteria_reviewed_version", "funding_status"}


def _fill_details(db, table: str, src_id: int, dst_id: int, prov_entity: str | None) -> list[str]:
    """Copy into the kept record's details (listing / requirements / profile) whatever it is missing; report clashes."""
    s = db.one(f"SELECT * FROM {table} WHERE opportunity_id = ?", (src_id,))
    if not s:
        return []
    d = db.one(f"SELECT * FROM {table} WHERE opportunity_id = ?", (dst_id,))
    if not d:
        db.execute(f"UPDATE {table} SET opportunity_id = ? WHERE opportunity_id = ?", (dst_id, src_id))
        return []
    fill, clash = {}, []
    for k, v in s.items():
        if k in SKIP_COLS or v in (None, ""):
            continue
        if d.get(k) in (None, ""):
            fill[k] = v
        elif str(d.get(k)) != str(v):
            clash.append(f"{k.replace('_', ' ')}: {str(v)[:60]}")
    if fill:
        db.execute(f"UPDATE {table} SET " + ", ".join(f"{k} = ?" for k in fill) + " WHERE id = ?",
                   (*fill.values(), d["id"]))
        if prov_entity:   # keep the source of each copied answer
            for k in fill:
                row = db.one("SELECT * FROM field_provenance WHERE entity_type = ? AND entity_id = ? AND field_key = ?",
                             (prov_entity, s["id"], k))
                if row and not db.scalar("SELECT 1 FROM field_provenance WHERE entity_type = ? AND entity_id = ? AND "
                                         "field_key = ?", (prov_entity, d["id"], k)):
                    db.execute("UPDATE field_provenance SET entity_id = ? WHERE entity_type = ? AND entity_id = ? AND "
                               "field_key = ?", (d["id"], prov_entity, s["id"], k))
    return clash


def merge_same_side(db, actor: dict, from_id: int, into_id: int, why: str) -> None:
    """Two records of the same person on the same side: everything of `from_id` moves to `into_id` (calls, notes,
    tasks, forms, answers, reports, documents, messages, bookings), missing details are filled in, differing values
    are written into a note, and `from_id` is closed as Moved (kept, never deleted)."""
    src, dst = load_any(db, from_id), load_any(db, into_id)
    if not src or not dst or src["kind"] != dst["kind"]:
        raise ValidationError({"record": "Both records must exist and be on the same side."})
    with db:
        clashes = []
        if src["kind"] == "seller":
            clashes += _fill_details(db, "seller_listings", from_id, into_id, "seller_listing")
            clashes += _fill_details(db, "pilot_profiles", from_id, into_id, None)
        else:
            clashes += _fill_details(db, "buyer_mandates", from_id, into_id, "buyer_mandate")
            clashes += _fill_details(db, "investor_profiles", from_id, into_id, None)
        for t in MOVE_TABLES:
            db.execute(f"UPDATE {t} SET opportunity_id = ? WHERE opportunity_id = ?", (into_id, from_id))
        for u in db.all("SELECT user_id, added_by, added_at FROM opportunity_collaborators WHERE opportunity_id = ?",
                        (from_id,)):
            if not db.scalar("SELECT 1 FROM opportunity_collaborators WHERE opportunity_id = ? AND user_id = ?",
                             (into_id, u["user_id"])):
                db.insert("opportunity_collaborators", {"opportunity_id": into_id, **u})
        db.execute("DELETE FROM opportunity_collaborators WHERE opportunity_id = ?", (from_id,))
        db.execute("DELETE FROM sheet_field_state WHERE opportunity_id = ?", (from_id,))
        if clashes:
            T.add_activity(db, actor["id"], "opportunity", into_id, "note",
                           f"From the duplicate record {src['ref_code']} (different from what is here): "
                           + "; ".join(clashes)[:1800])
    merge_into(db, actor, from_id, into_id, why)          # calls, notes, tasks, chats, marks; closes it as Moved


def _weight(db, o: dict) -> tuple:
    work = db.scalar("SELECT COUNT(*) FROM activities WHERE entity_type = 'opportunity' AND entity_id = ? AND "
                     "kind IN ('call', 'note')", (o["id"],)) or 0
    forms = db.scalar("SELECT COUNT(*) FROM pilot_responses WHERE opportunity_id = ?", (o["id"],)) or 0
    forms += db.scalar("SELECT COUNT(*) FROM investor_responses WHERE opportunity_id = ?", (o["id"],)) or 0
    return (o["status"] != "closed", forms, work, -o["id"])      # open, with forms, most worked on, then oldest


def merge_duplicates(db, actor: dict, dry: bool = True) -> dict:
    """Everyone with two or more records on the same side (same mobile number): keep the best one, fold the rest in.
    Records linked to a deal or teaser are left for a person to decide."""
    out = {"people": 0, "merged": [], "left_for_review": []}
    groups = db.all("SELECT c.phone_normalized AS ph, o.kind FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
                    "WHERE o.stage <> ? AND c.phone_normalized IS NOT NULL GROUP BY c.phone_normalized, o.kind "
                    "HAVING COUNT(*) > 1", (MOVED_STAGE,))
    for g in groups:
        recs = db.all("SELECT o.* FROM opportunities o JOIN contacts c ON c.id = o.contact_id WHERE o.kind = ? AND "
                      "o.stage <> ? AND (c.phone_normalized = ? OR c.alt_phone_normalized = ?)",
                      (g["kind"], MOVED_STAGE, g["ph"], g["ph"]))
        if len(recs) < 2:
            continue
        out["people"] += 1
        keep = max(recs, key=lambda o: _weight(db, o))
        for o in recs:
            if o["id"] == keep["id"]:
                continue
            if db.scalar("SELECT 1 FROM deals WHERE buyer_opp_id = ? OR seller_opp_id = ?", (o["id"], o["id"])) or \
                    db.scalar("SELECT 1 FROM teasers WHERE seller_opp_id = ?", (o["id"],)):
                out["left_for_review"].append(f"{o['ref_code']} (in a deal) - same person as {keep['ref_code']}")
                continue
            if not dry:
                merge_same_side(db, actor, o["id"], keep["id"], "same person, same mobile number")
                db.execute("UPDATE duplicate_reviews SET status = 'same_person_linked', resolved_by = ?, resolved_at = ?, "
                           "rationale = ? WHERE status = 'open' AND ((contact_id = ? AND candidate_contact_id = ?) OR "
                           "(contact_id = ? AND candidate_contact_id = ?))",
                           (actor["id"], now_iso(), f"Merged {o['ref_code']} into {keep['ref_code']}",
                            o["contact_id"], keep["contact_id"], keep["contact_id"], o["contact_id"]))
            out["merged"].append(f"{o['ref_code']} -> {keep['ref_code']}")
    return out
