"""Permanently delete a salon-owner / buyer record and everything that belongs to it (admins only, audited).

Built from the database's own links (foreign keys), so every child row — tasks, calls, form answers, reports,
bookings, matches, import rows, consents — goes with it and nothing unrelated is touched. The contact and salon are
removed too when no other record uses them. The audit log keeps a line saying who deleted what and when.
"""
from __future__ import annotations

from .. import audit
from ..permissions import user_can
from .errors import PermissionDenied, ValidationError

# rows that point at a record by (entity_type, entity_id) instead of a foreign key
POLY = {"opportunities": "opportunity", "seller_listings": "seller_listing", "buyer_mandates": "buyer_mandate",
        "match_candidates": "match", "deals": "deal"}
POLY_TABLES = ("tasks", "activities", "field_provenance", "field_change_log")
# history logs keep their rows; only the link to the deleted record is cleared
UNLINK = {("sheet_import_rows", "opportunity_id"), ("sheet_import_rows", "contact_id"),
          ("lead_ad_events", "opportunity_id"), ("import_rows", "opportunity_id"), ("import_rows", "duplicate_contact_id")}


def _children(db) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    if db.dialect == "postgres":
        rows = db.all("SELECT c.conrelid::regclass::text AS child, a.attname AS col, c.confrelid::regclass::text AS parent "
                      "FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
                      "WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace")
        for r in rows:
            out.setdefault(r["parent"].split(".")[-1], []).append((r["child"].split(".")[-1], r["col"]))
        return out
    for t in [r["name"] for r in db.all("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]:
        for fk in db.conn.execute(f"PRAGMA foreign_key_list({t})").fetchall():
            out.setdefault(fk[2], []).append((t, fk[3]))
    return out


def _has_id(db, table: str, cache: dict) -> bool:
    if table not in cache:
        if db.dialect == "postgres":
            cache[table] = bool(db.scalar("SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND "
                                          "table_name = ? AND column_name = 'id'", (table,)))
        else:
            cache[table] = any(c[1] == "id" for c in db.conn.execute(f"PRAGMA table_info({table})").fetchall())
    return cache[table]


def _delete(db, table: str, ids: list, kids: dict, cache: dict, seen: set, counts: dict) -> None:
    ids = [i for i in ids if (table, i) not in seen]
    if not ids:
        return
    seen.update((table, i) for i in ids)
    marks = ",".join("?" * len(ids))
    ent = POLY.get(table)
    if ent:  # tasks / calls / field history that point at this record by type + id
        for pt in POLY_TABLES:
            if _has_id(db, pt, cache):
                child = [r["id"] for r in db.all(f"SELECT id FROM {pt} WHERE entity_type = ? AND entity_id IN ({marks})",
                                                 (ent, *ids))]
                _delete(db, pt, child, kids, cache, seen, counts)
            else:
                db.execute(f"DELETE FROM {pt} WHERE entity_type = ? AND entity_id IN ({marks})", (ent, *ids))
    for child, col in kids.get(table, []):
        if (child, col) in UNLINK:
            db.execute(f"UPDATE {child} SET {col} = NULL WHERE {col} IN ({marks})", ids)
            continue
        if _has_id(db, child, cache):
            rows = [r["id"] for r in db.all(f"SELECT id FROM {child} WHERE {col} IN ({marks})", ids)]
            if child == table:  # a row pointing at itself's kind (sub-tasks): handled by the recursion
                rows = [r for r in rows if (child, r) not in seen]
            _delete(db, child, rows, kids, cache, seen, counts)
        else:
            db.execute(f"DELETE FROM {child} WHERE {col} IN ({marks})", ids)
    db.execute(f"DELETE FROM {table} WHERE id IN ({marks})", ids)
    counts[table] = counts.get(table, 0) + len(ids)


def delete_records(db, actor: dict, opp_ids: list[int], reason: str, remember: bool = True) -> dict:
    """Delete these records for good. Returns {records, rows}."""
    if not user_can(db, actor, "users.manage"):
        raise PermissionDenied("users.manage")
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ValidationError({"reason": "Say why (for example: test data)."})
    opps = db.all(f"SELECT o.id, o.ref_code, o.contact_id, o.business_id, c.full_name FROM opportunities o JOIN contacts c "
                  f"ON c.id = o.contact_id WHERE o.id IN ({','.join('?' * len(opp_ids))})", opp_ids) if opp_ids else []
    if not opps:
        raise ValidationError({"records": "Nothing to delete."})
    kids, cache, seen, counts = _children(db), {}, set(), {}
    phones = [p for o in opps for p in db.one("SELECT phone_normalized, alt_phone_normalized FROM contacts WHERE id = ?",
                                              (o["contact_id"],)).values() if p] if remember else []
    with db:
        for ph in phones:   # a removed lead must not come back from a sheet import (a new form still comes in)
            exclude(db, ph, f"Removed by {actor.get('full_name') or 'an admin'}: {reason.strip()[:150]}")
        for o in opps:
            _delete(db, "opportunities", [o["id"]], kids, cache, seen, counts)
        for o in opps:  # the person / salon go too when nothing else uses them
            if o["contact_id"] and not db.scalar("SELECT COUNT(*) FROM opportunities WHERE contact_id = ?",
                                                 (o["contact_id"],)):
                _delete(db, "contacts", [o["contact_id"]], kids, cache, seen, counts)
            if o["business_id"] and not db.scalar("SELECT COUNT(*) FROM opportunities WHERE business_id = ?",
                                                  (o["business_id"],)):
                _delete(db, "salon_businesses", [o["business_id"]], kids, cache, seen, counts)
        for o in opps:
            audit.record(db, actor["id"], "record.deleted", "opportunity", o["id"],
                         {"ref": o["ref_code"], "name": o["full_name"], "reason": reason[:200]})
    return {"records": len(opps), "rows": sum(counts.values()), "refs": [o["ref_code"] for o in opps]}


# -- Start fresh: every lead at once ---------------------------------------------------------------------------------
CLEAR_KEY = "clear_all_records"
CONFIRM_WORD = "DELETE ALL"


def clear_status(db) -> dict | None:
    import json
    raw = db.scalar("SELECT note FROM automation_settings WHERE key = ?", (CLEAR_KEY,))
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _set_status(db, st: dict) -> None:
    import json

    from ..timeutil import now_iso
    if db.scalar("SELECT 1 FROM automation_settings WHERE key = ?", (CLEAR_KEY,)):
        db.execute("UPDATE automation_settings SET note = ?, updated_at = ? WHERE key = ?", (json.dumps(st), now_iso(),
                                                                                           CLEAR_KEY))
    else:
        db.insert("automation_settings", {"key": CLEAR_KEY, "enabled": 1, "note": json.dumps(st), "updated_at": now_iso()})


def check_clear_all(db, actor: dict, confirm: str, reason: str) -> list[int]:
    """Validates the request and returns every record id. Admin only; the confirmation words must be typed."""
    if not user_can(db, actor, "users.manage"):
        raise PermissionDenied("users.manage")
    if (confirm or "").strip().upper() != CONFIRM_WORD:
        raise ValidationError({"confirm": f"Type {CONFIRM_WORD} to confirm."})
    if len((reason or "").strip()) < 3:
        raise ValidationError({"reason": "Say why (for example: clearing test data before going live)."})
    st = clear_status(db)
    if st and st.get("state") == "running":
        raise ValidationError({"confirm": "A clear-out is already running."})
    ids = [r["id"] for r in db.all("SELECT id FROM opportunities ORDER BY id")]
    if not ids:
        raise ValidationError({"records": "There are no records to remove."})
    return ids


def clear_all(db, actor: dict, ids: list[int], reason: str) -> dict:
    """Removes the given records (and everything linked) in batches, recording progress so the page can show it."""
    from ..timeutil import now_iso
    done = rows = 0
    _set_status(db, {"state": "running", "total": len(ids), "done": 0, "by": actor["full_name"], "started": now_iso()})
    try:
        for i in range(0, len(ids), 40):
            chunk = [x for x in ids[i:i + 40] if db.scalar("SELECT 1 FROM opportunities WHERE id = ?", (x,))]
            if chunk:
                r = delete_records(db, actor, chunk, reason, remember=False)   # start fresh: may be re-imported
                rows += r["rows"]
            done += len(ids[i:i + 40])
            _set_status(db, {"state": "running", "total": len(ids), "done": done, "by": actor["full_name"],
                             "started": now_iso()})
        audit.record(db, actor["id"], "records.cleared_all", "opportunity", None, {"records": len(ids),
                                                                                 "reason": reason[:200]})
        st = {"state": "done", "total": len(ids), "done": len(ids), "rows": rows, "by": actor["full_name"],
              "finished": now_iso()}
    except Exception as e:  # noqa: BLE001 — shown on the page; whatever was removed stays removed
        st = {"state": "failed", "total": len(ids), "done": done, "error": str(e)[:200], "by": actor["full_name"],
              "finished": now_iso()}
    _set_status(db, st)
    return st



# -- Numbers not to import again --------------------------------------------------------------------------------------
def exclude(db, phone: str, reason: str) -> None:
    from ..timeutil import now_iso
    if phone and not db.scalar("SELECT 1 FROM lead_exclusions WHERE phone = ?", (phone,)):
        db.insert("lead_exclusions", {"phone": phone, "reason": (reason or "")[:300], "created_at": now_iso()})


def exclusions(db) -> dict[str, str]:
    return {r["phone"]: r["reason"] or "" for r in db.all("SELECT phone, reason FROM lead_exclusions")}
