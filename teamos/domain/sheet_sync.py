"""Turn a workbook into a reviewed import plan, then sync it into TeamOS (see smart_import for the reading rules).

Plan → preview (nothing written to sellers/buyers yet) → commit:
* create: a new seller or buyer lead (owner = least-busy person in that team, first call due next working morning),
* update: the person is already in TeamOS → only EMPTY fields are filled; differences are added as a note,
* merged: the same person appears again in this workbook → folded into one row,
* skip: not relevant, with the reason (test row, not interested, wrong number, job seeker, invalid number…).
"""
from __future__ import annotations

import json
import re
from datetime import timedelta

from .. import audit
from ..permissions import user_can
from ..timeutil import IST, now_iso, now_utc, to_iso
from . import buyer_fields as BF
from . import buyers as B
from . import seller_fields as SFD
from . import sellers as S
from . import smart_import as SI
from . import tasks as T
from .errors import PermissionDenied, ValidationError

SELLER_ONLY = {"salon_name", "filled_by", "business_type", "years", "branches", "sale_type", "chairs", "wash_stations",
               "treatment_rooms", "area_sqft", "floor", "property_type", "rent", "deposit", "lease_till",
               "lease_remaining", "lease_transferable", "sales", "expenses", "profit_status", "staff_count",
               "staff_continuity", "owner_dependency", "reason_text", "reason_category", "asking_price", "inclusions",
               "liabilities", "litigation", "gst", "google_rating", "services", "prepaid", "hairdressers", "beauticians",
               "pilot_interest"}
BUYER_ONLY = {"budget", "timeline", "intent", "experience", "visit_willing", "looking_to_buy", "new_salon_kind"}


# -- Tab judgement -------------------------------------------------------------------------------------------------
def classify_tab(book: str, tab: str, fields: set[str]) -> dict:
    """{'kind': seller|buyer|skip, 'flavour': None|'new_salon'|'seek_help', 'why': str}"""
    if SI.HELPER_TABS.search(tab.strip()):
        return {"kind": "skip", "flavour": None, "why": "Helper tab (lists, instructions, dashboard or summary)"}
    if "phone" not in fields and "whatsapp" not in fields:
        return {"kind": "skip", "flavour": None, "why": "No phone number column — nothing to contact"}
    for title in (tab, book):
        if SI.OFF_TOPIC_TABS.search(title) and not (SI.BUYER_TITLE.search(tab) or SI.SELLER_TITLE.search(tab)):
            return {"kind": "skip", "flavour": None,
                    "why": f"'{title}' is about staff, training, products or properties — not salon buyers/sellers"}
    for title in (tab, book):
        if re.search(r"struggling|pilot", title, re.I):
            return {"kind": "seller", "flavour": "seek_help",
                    "why": f"'{title}': salon owners asking for help — kept as owners to nurture, not confirmed sellers"}
        if SI.NEW_SALON_TITLE.search(title) or "new_salon_kind" in fields:
            return {"kind": "buyer", "flavour": "new_salon", "why": f"'{title}': people who want to open a NEW salon"}
        if SI.BUYER_TITLE.search(title):
            return {"kind": "buyer", "flavour": None, "why": f"'{title}' is a buyer / investor list"}
        if SI.SELLER_TITLE.search(title):
            return {"kind": "seller", "flavour": None, "why": f"'{title}' is a seller list"}
    s, b = len(fields & SELLER_ONLY), len(fields & BUYER_ONLY)
    if "describes_you" in fields:
        return {"kind": "seller", "flavour": "seek_help", "why": "Salon owners describing their situation"}
    if s >= 3 and s > b:
        return {"kind": "seller", "flavour": None, "why": f"{s} salon-detail columns (rent, chairs, sales…)"}
    if b >= 1 and b >= s:
        return {"kind": "buyer", "flavour": None, "why": f"{b} buyer columns (budget, timeline…)"}
    return {"kind": "skip", "flavour": None, "why": "Can't tell whether these people are buyers or sellers"}


# -- Row understanding ---------------------------------------------------------------------------------------------
def read_row(headers: list[str], mapping: dict[int, str], row: list[str]) -> tuple[dict, list[str]]:
    canon, notes = {}, []
    for i, field in mapping.items():
        v = (row[i] if i < len(row) else "").strip()
        if not v or v in ("-", "--", "NA", "N/A", "na", "nil", "Nil", ".", ".."):
            continue
        if field in ("notes", "call_status", "lead_type", "pilot_interest"):
            label = re.sub(r"\s+", " ", headers[i]).strip()[:40]
            notes.append(f"{label}: {v}" if field != "notes" or len(label) > 0 else v)
            if field in ("call_status", "lead_type"):
                canon.setdefault(field, v)
            continue
        if field.startswith("help_"):
            if field == "help_staff" and re.fullmatch(r"\d+(\.0)?", v):
                canon.setdefault("staff_count", v)  # a "Staff" column holding a number is a head-count
            elif not re.fullmatch(r"(?i)no|n|false|0|-|x", v):
                canon[field] = v                    # ticked (or a short note) → they want help with this
            continue
        if field in ("hairdressers", "beauticians"):
            canon["staff_count"] = str((SI.first_int(canon.get("staff_count")) or 0) + (SI.first_int(v) or 0))
            continue
        if field == "email" and "@" not in v:
            notes.append(v)            # a remark typed into the email column
            continue
        if field in ("city", "locality", "city_area") and NOT_A_PLACE.search(v):
            notes.append(v)            # "monday come to office", "not intrested" typed into the city column
            continue
        canon.setdefault(field, v)
    return canon, notes


NOT_A_PLACE = re.compile(r"\b(call|calling|meet|office|interest|intrest|sale|sell|staff|looking|respond|busy|"
                         r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|today|will|want|"
                         r"not|no response|switch|wrong|number|later|come|coll|respo\w*|noo)\b", re.I)


def _name(canon) -> str:
    import unicodedata
    n = unicodedata.normalize("NFKC", canon.get("full_name") or "").strip()
    n = re.sub(r"^[\d\W_]+", "", n)
    if "@" in n:
        n = n.split("@")[0]
    n = re.sub(r"\d{6,}", "", n)
    n = re.sub(r"[_\.]+", " ", n).strip()
    return n.title() if n and n.islower() or n.isupper() else (n or "Name not given")


def understand(kind: str, flavour: str | None, canon: dict) -> dict:
    """Canonical sheet values → TeamOS fields for that side. Returns {'fields': {...}, 'facts': [...] (for notes)}."""
    f, facts = {}, []
    city, area = SI.split_place(canon.get("city") or "", canon.get("locality") or canon.get("city_area") or "")
    if canon.get("city_area") and not canon.get("city"):
        city, area = SI.split_place(canon["city_area"], "")
        city2, area2 = SI.split_place("", canon["city_area"])
        area = area or area2
        city = city or city2
    if area and re.fullmatch(r"(unisex|family|men'?s?|women'?s?|ladies|gents|salon|parlou?r|na|no|yes)", area.strip(), re.I):
        area = None  # a salon type typed into the area column
    if area and (len(area) > 30 or re.search(r"\b(near|beside|opp|opposite|behind|floor|road)\b", area, re.I)):
        if kind == "seller":
            f["address_line"] = area[:200]  # the full address is kept privately; the area is the place name
        short = re.split(r"\b(near|beside|opp|opposite|behind|above|below|next to|\d+(st|nd|rd|th)? floor)\b", area,
                         flags=re.I)[0]
        area = " ".join(short.replace(",", " ").split()[:3]) or None
    if kind == "seller":
        sn = (canon.get("salon_name") or "").strip()
        if sn and (len(sn) < 3 or sn.isdigit()):
            canon["salon_name"] = ""
        fmt, pos = SI.salon_format(canon.get("business_type") or canon.get("salon_name") or "")
        f.update({k: v for k, v in {"gender_format": fmt, "positioning": pos}.items() if v})
        if flavour == "seek_help":
            f["sale_purpose"] = "seek_help"
        elif re.search(r"partner", canon.get("sale_type") or "", re.I):
            f["sale_purpose"] = "partnership"
        else:
            f["sale_purpose"] = "sell_entire"
        fb = (canon.get("filled_by") or "").lower()
        role = "owner" if "owner" in fb else "partner" if "partner" in fb else "manager" if "manager" in fb else None
        if role:
            f["ownership_role"] = role
        dm = (canon.get("decision_maker") or "").lower()
        if dm.startswith("yes"):
            f["transfer_authority"] = "yes"
        elif "partner" in dm:
            f.setdefault("ownership_role", "partner")
            facts.append("Sale is a partnership decision")
        lo, hi = SI.money_range(canon.get("asking_price"), small_means_lakh=True)  # "40" for a salon price = ₹40 L
        if hi or lo:
            f["asking_price"] = hi or lo
            if lo and hi and lo != hi:
                facts.append(f"Expected price given as a range: {canon['asking_price']}")
        tl = SI.sale_timeline(canon.get("urgency") or "")
        if tl:
            f["sale_timeline"] = tl
        y = SI.first_int(canon.get("years"))
        if y and 1950 <= y <= SI.THIS_YEAR:
            f["started_year"], f["operating_years"] = y, max(0, SI.THIS_YEAR - y)
        elif y is not None and y < 80:
            f["operating_years"] = y
        for src, dst in (("chairs", "chairs"), ("wash_stations", "wash_stations"), ("treatment_rooms", "treatment_rooms")):
            n = SI.band_low(canon.get(src))
            if n is not None and n < 200:
                f[dst] = n
                if re.search(r"\d\s*[-–]\s*\d|\+", canon.get(src) or ""):
                    facts.append(f"{dst.replace('_', ' ').title()}: {canon[src]} (lowest of the band used)")
        a = SI.first_int(canon.get("area_sqft"))
        if a and 50 <= a <= 50000:
            f["carpet_area_sqft"] = a
        fl = SI.floor_level(canon.get("floor"))
        if fl:
            f["floor_level"] = fl
        for src, dst, small in (("rent", "monthly_rent", False), ("deposit", "rent_deposit", False),
                                ("sales", "avg_monthly_sales", True), ("expenses", "avg_monthly_expenses", True)):
            lo, hi = SI.money_range(canon.get(src), small_means_lakh=small)
            if lo or hi:
                f[dst] = (lo + hi) // 2 if lo and hi else (hi or lo)
                if lo and hi and lo != hi:
                    facts.append(f"{dst.replace('_', ' ').replace('avg ', 'average ')}: {canon[src]} (middle of the range used)")
        d = SI.parse_date(canon.get("lease_till"))
        if d:
            f["lease_expiry"] = d
        lt = SI.yes_no(canon.get("lease_transferable"))
        if lt:
            f["lease_transfer_status"] = "confirmed_transferable" if lt == "yes" else "not_permitted"
        n = SI.first_int(canon.get("staff_count"))
        if n is not None and n < 500:
            f["staff_headcount"] = n
        sc = SI.yes_no(canon.get("staff_continuity"))
        if sc:
            f["staff_continuity"] = sc
        rc = SI.reason_category(canon.get("reason_category") or canon.get("reason_text"))
        if rc:
            f["sale_reason"] = rc
        if canon.get("reason_text"):
            f["sale_reason_note"] = canon["reason_text"][:1000]
        lit = SI.yes_no(canon.get("litigation"))
        if lit:
            f["litigation"] = lit
        if SI.yes_no(canon.get("liabilities")) == "no":
            f["outstanding_loans"] = 0
        elif canon.get("liabilities"):
            facts.append(f"Liabilities: {canon['liabilities']}")
        g = SI.yes_no(canon.get("gst"))
        if g:
            f["gst_registered"] = g
        if SI.yes_no(canon.get("prepaid")) == "no":
            f["prepaid_liabilities"] = 0
        for src, dst in (("google_rating", "google_rating"), ("services", "top_services"),
                         ("billing_software", "booking_software"), ("inclusions", "inclusions_note"),
                         ("preferred_callback", "preferred_callback")):
            if canon.get(src):
                f[dst] = canon[src][:300]
        ps = (canon.get("profit_status") or "").lower()
        if "loss" in ps:
            facts.append("Owner says the salon is running at a loss")
        if canon.get("sales") or canon.get("rent") or "profit" in ps:
            f["operating_status"] = "non_operating" if re.search(r"closed|not operating|shut", ps) else "operating"
    else:
        f["need_type"] = "new_salon" if flavour == "new_salon" else (
            "passive_invest" if re.search(r"passive|silent", canon.get("intent") or "", re.I) else "acquire_operating")
        lo, hi = SI.money_range(canon.get("budget") or canon.get("new_salon_kind") or canon.get("asking_price")
                                or canon.get("business_type"), small_means_lakh=True)
        if hi or lo:
            f["all_in_max"] = hi or lo
            if lo and hi and lo != hi:
                f["budget_min"] = lo
        pw = SI.purchase_window(canon.get("timeline") or canon.get("urgency") or "")
        if pw:
            f["purchase_window"] = pw
        it = (canon.get("intent") or "").lower()
        if "myself" in it or "operate" in it:
            f["involvement"] = "owner_operator"
        elif "manager" in it:
            f["involvement"] = "partly"
        elif "passive" in it or "silent" in it:
            f["involvement"] = "absentee"
        ex = SI.yes_no(canon.get("experience"))
        if ex:
            f["salon_background"] = "industry_other" if ex == "yes" else "none"
        if (canon.get("decision_maker") or "").lower().startswith("yes"):
            f["decision_maker"] = "yes"
        if SI.yes_no(canon.get("visit_willing")) == "yes":
            f["visit_availability"] = "yes"
        kind_txt = canon.get("new_salon_kind") or canon.get("business_type") or ""
        fmt, _ = SI.salon_format(kind_txt)
        if fmt:
            facts.append(f"Wants a {fmt} salon")
        if city:
            f["preferred_city"] = city
    return {"fields": f, "facts": facts, "city": city, "area": area}


# -- Plan -------------------------------------------------------------------------------------------------------------
def plan(db, filename: str, data: bytes) -> dict:
    book = re.sub(r"\.(xlsx|csv)$", "", filename or "", flags=re.I)
    tabs = SI.read_workbook(filename, data)
    headers_by_tab = {}
    for name, rows in tabs:
        hi, hdr = SI.find_header(rows)
        if hi is not None:
            headers_by_tab[name] = hdr
    tab_info, rows_out = [], []
    total = 0
    for name, rows in tabs:
        nonempty = [r for r in rows if any(c for c in r)]
        hi, hdr = SI.find_header(rows, siblings=[h for n, h in headers_by_tab.items() if n != name])
        if not nonempty:
            tab_info.append({"name": name, "kind": "skip", "why": "Empty tab", "rows": 0, "mapping": {}, "ignored": []})
            continue
        mapping = {}
        for i, h in enumerate(hdr):
            fld, _side = SI.recognise(h)
            if fld and fld != "ignore":
                mapping[i] = fld
        fields = set(mapping.values())
        verdict = classify_tab(book, name, fields)
        body = rows[hi + 1:] if hi is not None else [r for r in rows if any(r)]
        body = [r for r in body if any(c for c in r)]
        info = {"name": name, "kind": verdict["kind"], "flavour": verdict["flavour"], "why": verdict["why"],
                "rows": len(body), "header_borrowed": hi is None and bool(hdr),
                "mapping": {hdr[i]: f for i, f in mapping.items()},
                "ignored": [h for i, h in enumerate(hdr) if h and i not in mapping][:40]}
        tab_info.append(info)
        if verdict["kind"] == "skip":
            continue
        is_form = bool(fields & {"timestamp"}) and not re.search(r"call", name, re.I)
        for n, r in enumerate(body, start=(hi + 2) if hi is not None else 1):
            total += 1
            if total > SI.MAX_ROWS:
                raise ValidationError({"file": f"More than {SI.MAX_ROWS} rows — split the workbook."})
            canon, notes = read_row(hdr, mapping, r)
            if hdr and sum(1 for i, v in enumerate(r) if v and i < len(hdr) and v.strip() == (hdr[i] or "").strip()) >= 3:
                continue  # the header row pasted again inside the data
            phones = SI.clean_phones(canon.get("phone")) + [p for p in SI.clean_phones(canon.get("whatsapp"))]
            phones = list(dict.fromkeys(phones))
            phone = phones[0] if phones else None
            if len(phones) > 1:
                canon["alt_phone"] = phones[1]
            alltext = " ".join([canon.get("full_name", ""), canon.get("looking_to_buy", ""),
                                canon.get("describes_you", ""), canon.get("call_status", ""), *notes,
                                canon.get("phone", ""), *[c for c in r if c and len(c) < 120]])
            kind, reason = verdict["kind"], None
            why_not = SI.judge_row(alltext)
            if re.search(r"^\s*(test|example|sample|dummy)\b", canon.get("full_name", ""), re.I):
                why_not = "Test / sample entry"
            if not why_not and kind == "buyer" and re.match(r"^no", canon.get("looking_to_buy", ""), re.I):
                why_not = "Said they are not looking to buy"
            dy = (canon.get("describes_you") or "").lower()
            if not why_not and dy and not re.search(r"own|owner|partner", dy):
                why_not = f"Not a salon owner ({canon['describes_you'].replace('_', ' ').strip()})"
            if not why_not and kind == "buyer" and SI.SELL_SIGNAL.search(" ".join(notes).lower()):
                kind, reason = "seller", "Remarks say they want to SELL — imported as a seller"
            if why_not:
                action, reason = "skip", why_not
            elif not phone:
                action, reason = "skip", f"No valid Indian mobile number ({canon.get('phone') or 'blank'})"
            else:
                action = "create"
            u = understand(kind, verdict["flavour"] if kind == verdict["kind"] else None, canon)
            rows_out.append({"tab": name, "row_no": n, "raw": dict(zip([h or f"Column {i + 1}" for i, h in enumerate(hdr)]
                                                                       if hdr else [], r)) if hdr else {"cells": r},
                             "kind": kind, "phone": phone, "canon": canon, "understood": u, "notes": notes,
                             "action": action, "reason": reason, "is_form": is_form, "book": book,
                             "flavour": verdict["flavour"] if kind == verdict["kind"] else None})
    _merge_and_sync(db, rows_out)
    return {"book": book, "tabs": tab_info, "rows": rows_out}


def _richness(r) -> int:
    return len(r["understood"]["fields"]) + len(r["notes"]) + (2 if r["canon"].get("salon_name") else 0)


NEGATIVE = ("Not interested", "Wrong number", "Looking for staff")


def _merge_and_sync(db, rows: list[dict]) -> None:
    # A clear "not interested" / "wrong number" / job-seeker verdict on one row applies to the same person elsewhere
    verdict = {}
    for r in rows:
        if r["action"] == "skip" and r["phone"] is None:
            continue
        if r["action"] == "skip" and any((r["reason"] or "").startswith(n) for n in NEGATIVE):
            verdict.setdefault(r["phone"], r)
    for r in rows:
        v = verdict.get(r["phone"])
        if v and r is not v and r["action"] != "skip":
            r["action"] = "skip"
            r["reason"] = f"{v['reason']} (said so in {v['tab']} row {v['row_no']})"
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if r["action"] != "skip":
            groups.setdefault((r["phone"], r["kind"]), []).append(r)
    for (phone, kind), grp in groups.items():
        grp.sort(key=_richness, reverse=True)
        primary = grp[0]
        for other in grp[1:]:
            other["action"], other["merged_into"] = "merged", primary
            other["reason"] = f"Same person as {primary['tab']} row {primary['row_no']} — merged into one record"
            for k, v in other["understood"]["fields"].items():
                primary["understood"]["fields"].setdefault(k, v)
            for k in ("city", "area"):
                primary["understood"][k] = primary["understood"][k] or other["understood"][k]
            for k, v in other["canon"].items():
                primary["canon"].setdefault(k, v)
            primary["notes"] += [n for n in other["notes"] if n not in primary["notes"]]
            primary["is_form"] = primary["is_form"] or other["is_form"]
        # already in TeamOS?
        c = db.one("SELECT * FROM contacts WHERE phone_normalized = ? OR alt_phone_normalized = ? ORDER BY id LIMIT 1",
                   (phone, phone))
        if c:
            primary["contact_id"] = c["id"]
            opp = db.one("SELECT id, ref_code FROM opportunities WHERE contact_id = ? AND kind = ? ORDER BY id DESC "
                         "LIMIT 1", (c["id"], kind))
            if opp:
                primary["action"], primary["opportunity_id"] = "update", opp["id"]
                primary["reason"] = f"Already in TeamOS as {opp['ref_code']} — empty fields will be filled, nothing overwritten"
            else:
                primary["reason"] = f"Known contact ({c['full_name']}) — a new {kind} record will be linked to them"


# -- Persist + commit -----------------------------------------------------------------------------------------------
def create_import(db, actor, filename: str, data: bytes) -> int:
    if not user_can(db, actor, "crm.import"):
        raise PermissionDenied("crm.import")
    p = plan(db, filename, data)
    if not any(t["kind"] != "skip" for t in p["tabs"]):
        raise ValidationError({"file": "No buyer or seller lists found in this file. "
                                       + "; ".join(f"{t['name']}: {t['why']}" for t in p["tabs"])[:600]})
    now = now_iso()
    with db:
        iid = db.insert("sheet_imports", {"filename": filename[:200], "file_sha256": SI.file_sha(data),
                                          "status": "preview", "tabs": json.dumps(p["tabs"]), "created_by": actor["id"],
                                          "created_at": now})
        ids = {}
        for r in p["rows"]:
            rid = db.insert("sheet_import_rows", {
                "import_id": iid, "tab": r["tab"][:100], "row_no": r["row_no"], "raw": json.dumps(r["raw"])[:20000],
                "kind": r["kind"], "phone": r["phone"],
                "data": json.dumps({"canon": r["canon"], "understood": r["understood"], "is_form": r["is_form"],
                                    "flavour": r["flavour"], "book": r["book"]}),
                "notes": json.dumps(r["notes"]), "action": r["action"], "reason": r["reason"],
                "contact_id": r.get("contact_id"), "opportunity_id": r.get("opportunity_id")})
            ids[id(r)] = rid
        for r in p["rows"]:
            if r.get("merged_into") is not None:
                db.update("sheet_import_rows", ids[id(r)], {"merged_into": ids[id(r["merged_into"])]})
        audit.record(db, actor["id"], "import.sheet_upload", "sheet_import", iid,
                     {"filename": filename, "rows": len(p["rows"]), "tabs": len(p["tabs"])})
    return iid


def summary(db, import_id: int) -> dict:
    rows = db.all("SELECT kind, action, COUNT(*) n FROM sheet_import_rows WHERE import_id = ? GROUP BY kind, action",
                  (import_id,))
    out = {"new": {"seller": 0, "buyer": 0}, "existing": {"seller": 0, "buyer": 0}, "merged": 0, "skip": 0}
    for r in rows:
        if r["action"] in ("create", "update"):
            key = "new" if r["action"] == "create" else "existing"
            out[key][r["kind"]] = out[key].get(r["kind"], 0) + r["n"]
        else:
            out[r["action"]] += r["n"]
    out["skip_reasons"] = db.all("SELECT reason, COUNT(*) n FROM sheet_import_rows WHERE import_id = ? AND action = "
                                 "'skip' GROUP BY reason ORDER BY n DESC", (import_id,))
    return out


def _owner(db, team: str, load: dict) -> int | None:
    from .progress import pick_owner  # least-busy available person; people marked away are skipped
    return pick_owner(db, team, load)


def _next_morning() -> str:
    from datetime import datetime, time
    local = now_utc().astimezone(IST)
    day = local.date() + timedelta(days=1)
    while day.weekday() == 6:  # Sunday → Monday
        day += timedelta(days=1)
    return to_iso(datetime.combine(day, time(10, 0), tzinfo=IST))


def _raw(v) -> str:
    return str(v) if v is not None else ""


def commit(db, actor, import_id: int) -> dict:
    if not user_can(db, actor, "crm.import"):
        raise PermissionDenied("crm.import")
    imp = db.one("SELECT * FROM sheet_imports WHERE id = ?", (import_id,))
    if not imp or imp["status"] != "preview":
        raise ValidationError("This import has already been committed or discarded.")
    counts = {"created_seller": 0, "created_buyer": 0, "updated": 0, "merged": 0, "skipped": 0, "failed": 0}
    to_understand = []
    load, due = {}, _next_morning()
    src_tag = f"sheet:{import_id}"
    for r in db.all("SELECT * FROM sheet_import_rows WHERE import_id = ? ORDER BY id", (import_id,)):
        if r["outcome"] in ("created", "updated"):  # resumed after an interruption: already saved
            counts[f"created_{r['kind']}" if r["outcome"] == "created" else "updated"] += 1
            if r["outcome"] == "created" and r["opportunity_id"]:
                d = json.loads(r["data"])
                to_understand.append((r["kind"], r["opportunity_id"], d["canon"], json.loads(r["notes"] or "[]"),
                                      json.loads(r["raw"] or "{}")))
            continue
        if r["action"] in ("skip", "merged"):
            counts["skipped" if r["action"] == "skip" else "merged"] += 1
            db.update("sheet_import_rows", r["id"], {"outcome": "skipped" if r["action"] == "skip" else "merged"})
            continue
        d = json.loads(r["data"])
        u, canon = d["understood"], d["canon"]
        notes = json.loads(r["notes"] or "[]") + u["facts"]
        src_note = f"{d['book']} › {r['tab']}" + (f" · {canon['source_campaign']}" if canon.get("source_campaign") else "")
        try:
            with db:
                if r["action"] == "create":
                    opp_id = _create(db, actor, r, d, u, canon, src_note, load, due)
                    from .timeline import lead_time
                    lt = lead_time(canon.get("timestamp"))
                    if lt:
                        db.execute("UPDATE opportunities SET lead_created_at = ? WHERE id = ?", (lt, opp_id))
                    counts[f"created_{r['kind']}"] += 1
                    db.update("sheet_import_rows", r["id"], {"outcome": "created", "opportunity_id": opp_id})
                else:
                    opp_id = r["opportunity_id"]
                    filled = _fill_gaps(db, actor, r["kind"], opp_id, u, src_tag)
                    if filled:
                        notes.insert(0, "Filled from sheet: " + ", ".join(filled))
                    counts["updated"] += 1
                    db.update("sheet_import_rows", r["id"], {"outcome": "updated"})
                if notes:
                    T.add_activity(db, actor["id"], "opportunity", opp_id, "note",
                                   f"From sheet ({src_note}, row {r['row_no']}):\n" + "\n".join(notes)[:3000])
                to_understand.append((r["kind"], opp_id, canon, json.loads(r["notes"] or "[]"),
                                      json.loads(r["raw"] or "{}")))
        except (ValidationError, PermissionDenied) as e:
            counts["failed"] += 1
            msg = " ".join(str(v) for v in e.errors.values()) if isinstance(e, ValidationError) else str(e)
            db.update("sheet_import_rows", r["id"], {"outcome": "failed", "reason": (r["reason"] or "") + f" — {msg}"[:400]})
    _schedule_first_calls(db, import_id)
    # after the first calls are planned: the team's remarks decide the next call (and what they need)
    for kind, opp_id, canon, notes, raw in to_understand:
        try:
            with db:
                if apply_understanding(db, actor, kind, opp_id, canon, notes, raw):
                    counts["understood"] = counts.get("understood", 0) + 1
        except (ValidationError, PermissionDenied):
            pass  # understanding is a bonus; the record itself is already imported
    with db:
        db.update("sheet_imports", import_id, {"status": "committed", "counts": json.dumps(counts),
                                               "committed_by": actor["id"], "committed_at": now_iso()})
        audit.record(db, actor["id"], "import.sheet_commit", "sheet_import", import_id, counts)
    return counts


def claim(db, import_id: int) -> bool:
    """Mark an import as being saved (so it is not started twice). A run that stopped part-way can be resumed."""
    cur = db.execute("UPDATE sheet_imports SET counts = ? WHERE id = ? AND status = 'preview' AND "
                     "(counts IS NULL OR counts LIKE ?)", (json.dumps({"importing": now_iso()}), import_id, '%"error"%'))
    return (cur.rowcount or 0) == 1


def progress(db, import_id: int) -> dict:
    row = db.one("SELECT SUM(CASE WHEN outcome IS NOT NULL THEN 1 ELSE 0 END) AS done, COUNT(*) AS total "
                 "FROM sheet_import_rows WHERE import_id = ?", (import_id,))
    return {"done": row["done"] or 0, "total": row["total"] or 0}


def commit_in_background(app, import_id: int, actor_id: int) -> None:
    """Saving many rows to a hosted database takes minutes (one network round trip per query), longer than a web
    request may last — so the import runs on its own thread with its own connection; the page shows progress."""
    import logging
    import threading

    from ..db import Database

    def run():
        with app.app_context():
            db = Database(app.config["DATABASE_URL"])
            try:
                actor = db.one("SELECT * FROM users WHERE id = ?", (actor_id,))
                commit(db, actor, import_id)
            except Exception as e:  # noqa: BLE001 — shown on the page; the import can be resumed
                logging.getLogger(__name__).exception("sheet import %s failed", import_id)
                try:
                    db.execute("UPDATE sheet_imports SET counts = ? WHERE id = ? AND status = 'preview'",
                               (json.dumps({"error": str(e)[:300]}), import_id))
                except Exception:  # noqa: BLE001
                    pass
            finally:
                db.close()
    if app.config.get("TESTING"):
        run()
    else:
        threading.Thread(target=run, name=f"sheet-import-{import_id}", daemon=True).start()


SELLER_QUICK = {f.key for f in SFD.FIELDS if f.quick}
FIRST_CALLS_PER_DAY = 25     # per person; proposal D-57
HIGH_PRIORITY_PER_DAY = 8


def _hotness(d: dict) -> tuple:
    f = d["understood"]["fields"]
    soon = f.get("purchase_window") == "30d" or f.get("sale_timeline") == "immediate"
    ts = SI.parse_date(d["canon"].get("timestamp")) or ""
    return (bool(d.get("is_form")), soon, bool(f.get("all_in_max") or f.get("asking_price")), ts)


def _schedule_first_calls(db, import_id: int) -> None:
    """Spread the new leads' first calls over working days (Mon–Sat, 10:00–18:00 IST), hottest first, so nobody
    starts the week with hundreds of overdue tasks."""
    from datetime import datetime, time
    rows = db.all("SELECT r.data, r.opportunity_id, o.primary_owner_id FROM sheet_import_rows r JOIN opportunities o "
                  "ON o.id = r.opportunity_id WHERE r.import_id = ? AND r.outcome = 'created'", (import_id,))
    by_owner: dict[int, list] = {}
    for r in rows:
        by_owner.setdefault(r["primary_owner_id"], []).append((_hotness(json.loads(r["data"])), r["opportunity_id"]))
    start = now_utc().astimezone(IST).date() + timedelta(days=1)
    with db:
        for owner, items in by_owner.items():
            items.sort(key=lambda x: x[0], reverse=True)
            already = db.scalar("SELECT COUNT(*) FROM tasks WHERE assignee_id = ? AND status = 'open' AND "
                                "task_type = 'first_contact'", (owner,)) - len(items)
            for i, (_h, opp_id) in enumerate(items):
                slot = max(0, already) + i
                day = start
                while day.weekday() == 6:          # no calls on Sunday
                    day += timedelta(days=1)
                for _ in range(slot // FIRST_CALLS_PER_DAY):
                    day += timedelta(days=1)
                    while day.weekday() == 6:
                        day += timedelta(days=1)
                in_day = slot % FIRST_CALLS_PER_DAY
                due = datetime.combine(day, time(10, 0), tzinfo=IST) + timedelta(minutes=in_day * 19)
                db.execute("UPDATE tasks SET due_at = ?, priority = ? WHERE entity_type = 'opportunity' AND "
                           "entity_id = ? AND task_type = 'first_contact' AND status = 'open'",
                           (to_iso(due), "high" if in_day < HIGH_PRIORITY_PER_DAY else "normal", opp_id))
            # one summary alert instead of one alert per imported lead
            ids = [o for _h, o in items]
            tids = [t["id"] for t in db.all(
                "SELECT id FROM tasks WHERE entity_type = 'opportunity' AND task_type = 'first_contact' AND entity_id IN "
                "(%s)" % ",".join("?" * len(ids)), ids)] if ids else []
            for k in range(0, len(tids), 500):
                chunk = tids[k:k + 500]
                db.execute("DELETE FROM notifications WHERE user_id = ? AND kind = 'task_assigned' AND link IN (%s)"
                           % ",".join("?" * len(chunk)), [owner] + [f"/tasks/{t}" for t in chunk])
            span = db.one("SELECT MIN(due_at) a, MAX(due_at) b FROM tasks WHERE id IN (%s)" % ",".join("?" * len(tids)),
                          tids) if tids else None
            if span:
                from ..timeutil import fmt_ist
                T.notify(db, owner, "task_assigned",
                         f"{len(items)} new leads from a sheet import — first calls planned "
                         f"{fmt_ist(span['a'], '%a %d %b')} to {fmt_ist(span['b'], '%a %d %b')}, hottest first",
                         "/tasks/")


def _create(db, actor, r, d, u, canon, src_note, load, due) -> int:
    f = u["fields"]
    owner = _owner(db, r["kind"], load) or actor["id"]
    base = {"full_name": _name(canon), "phone": r["phone"], "email": canon.get("email", ""),
            "alt_phone": canon.get("alt_phone", ""),
            "source": "import", "source_note": src_note[:200], "owner_id": str(owner),
            "consent_contact": "1" if d["is_form"] else ""}
    dd = f"link:{r['contact_id']}" if r["contact_id"] else None
    if r["kind"] == "seller":
        base.update({"salon_name": (canon.get("salon_name") or "").strip()[:120] or "Salon name not given",
                     "city": u["city"] or "City not given", "locality": u["area"] or u["city"] or "Area not given"})
        base.update({k: _raw(v) for k, v in f.items() if k in SELLER_QUICK})
        res = S.create_lead(db, actor, base, dedupe_decision=dd or "new_flagged", first_task_due=due,
                            first_task_priority="normal")
        opp_id = res["opportunity_id"]
        rest = {k: (v, "value") for k, v in f.items() if k not in SELLER_QUICK and k in SFD.BY_KEY if v is not None}
        if rest:
            lid = db.scalar("SELECT id FROM seller_listings WHERE opportunity_id = ?", (opp_id,))
            S._apply_values(db, actor, lid, rest, source=f"import:{src_note}"[:120], bump_version=False)
        return opp_id
    base.update({"city": u["city"] or "City not given", "locality": u["area"] or ""})
    quick = {fd.key for fd in BF.FIELDS if fd.quick}
    base.update({k: _raw(v) for k, v in f.items() if k in quick})
    res = B.create_lead(db, actor, base, dedupe_decision=dd or "new_flagged", first_task_due=due)
    opp_id = res["opportunity_id"]
    _buyer_fields(db, actor, opp_id, {k: v for k, v in f.items() if k not in quick})
    if u["area"]:
        try:
            B.add_locality(db, actor, B.load(db, opp_id), "preferred", u["city"] or "", u["area"])
        except ValidationError:
            pass
    return opp_id


def _buyer_fields(db, actor, opp_id, values: dict) -> list[str]:
    by_section: dict[str, dict] = {}
    for k, v in values.items():
        fd = BF.BY_KEY.get(k)
        if fd and v is not None:
            by_section.setdefault(fd.section, {})[k] = _raw(v)
    changed = []
    for sec, data in by_section.items():
        changed += B.update_fields(db, actor, B.load(db, opp_id), data, sec) or []
    return changed


def _fill_gaps(db, actor, kind, opp_id, u, src_tag) -> list[str]:
    """Only fields that are still empty in TeamOS; returns labels of what was filled."""
    f = u["fields"]
    if kind == "seller":
        lid = db.scalar("SELECT id FROM seller_listings WHERE opportunity_id = ?", (opp_id,))
        listing = S.load_listing(db, lid)
        prov = S.provenance(db, lid)
        todo = {k: (v, "value") for k, v in f.items() if k in SFD.BY_KEY and listing.get(k) is None
                and not (prov.get(k) and prov[k]["answer_state"] != "value") and v is not None}
        if todo:
            S._apply_values(db, actor, lid, todo, source=src_tag, bump_version=False)
        return [SFD.BY_KEY[k].label for k in todo]
    m = db.one("SELECT * FROM buyer_mandates WHERE opportunity_id = ?", (opp_id,))
    todo = {k: v for k, v in f.items() if k in BF.BY_KEY and m.get(k) is None and v is not None}
    if todo:
        _buyer_fields(db, actor, opp_id, todo)
    return [BF.BY_KEY[k].label for k in todo]


def discard(db, actor, import_id: int) -> None:
    if not user_can(db, actor, "crm.import"):
        raise PermissionDenied("crm.import")
    with db:
        db.update("sheet_imports", import_id, {"status": "discarded"})
        audit.record(db, actor["id"], "import.sheet_discard", "sheet_import", import_id)


# -- Acting on the team's remarks and tick columns --------------------------------------------------------------------
HELP_COLUMNS = {"help_software": "chairpe", "help_consulting": "business_consulting",
                "help_marketing": "digital_marketing", "help_staff": "staff_hiring", "help_training": "staff_training"}


def remark_text(notes: list[str]) -> str:
    """The remarks themselves, without the column labels ("Remarks: busy" → "busy")."""
    out = []
    for n in notes or []:
        out.append(n.split(": ", 1)[1] if ": " in n[:42] else n)
    return " | ".join(x.strip() for x in out if x and x.strip())


def apply_understanding(db, actor, kind: str, opp_id: int, canon: dict, notes: list[str], raw: dict,
                        plan_calls: bool = True) -> list[str]:
    """Read the remarks (English / Telugu / Telugu in English letters) and the tick columns, then: plan the next call
    at the time they asked for, record what they need, mark the pilot form, and keep a plain-English summary.
    Returns what was done (empty if nothing was understood)."""
    import json as _json

    from . import pilot as P
    from . import remarks as RM
    done: list[str] = []
    text = remark_text(notes)
    res = RM.read(text, db) if text else {"understood": False, "meanings": {}, "needs": []}
    # 1. Salon owners: help areas from tick columns + remarks; pilot form answers from a Google-Form sheet
    if kind == "seller":
        areas = [HELP_COLUMNS[k] for k in HELP_COLUMNS if canon.get(k)]
        areas += [RM.NEED_TO_HELP[n] for n in res["needs"] if n in RM.NEED_TO_HELP]
        if areas:
            prof = P.profile(db, opp_id)
            merged = list(dict.fromkeys(prof["help_areas"] + areas))
            if merged != prof["help_areas"]:
                P._upsert(db, opp_id, actor["id"], {"help_areas": _json.dumps(merged)})
                done.append("help areas: " + ", ".join(P.HELP_LABELS[a] for a in merged if a in P.HELP_LABELS))
        answers = P.answers_from_sheet(raw)
        if answers and not db.scalar("SELECT id FROM pilot_responses WHERE opportunity_id = ?", (opp_id,)):
            opp = db.one("SELECT o.*, c.full_name AS contact_name, b.name AS salon_name FROM opportunities o JOIN "
                         "contacts c ON c.id = o.contact_id LEFT JOIN salon_businesses b ON b.id = o.business_id "
                         "WHERE o.id = ?", (opp_id,))
            P.save_response(db, opp, answers, "import", actor["id"], quiet=True)
            done.append(f"pilot form answers ({len(answers)}) → SWOT draft")
        elif "form_sent" in res["meanings"] and P.profile(db, opp_id)["form_status"] == "not_sent":
            P._upsert(db, opp_id, actor["id"], {"form_status": "sent", "form_sent_at": now_iso()})
            done.append("pilot form marked sent")
    if not res["understood"]:
        return done
    # 2. The next call, at the time they asked for
    task = db.one("SELECT * FROM tasks WHERE entity_type = 'opportunity' AND entity_id = ? AND status = 'open' "
                  "ORDER BY due_at LIMIT 1", (opp_id,)) if plan_calls else None  # re-reads keep existing call times
    out = res["outcome"]
    title, due, prio = None, None, None
    if res["callback_at"] and res["callback_at"] > now_iso() and out in ("callback", "busy", "they_will_call", None):
        title = f"Call back — they asked ({res['callback_text']})"
        due, prio = res["callback_at"], "high"
    elif "office_visit" in res["meanings"]:
        title = "Confirm the office visit" + (f" ({res['callback_text']})" if res["callback_text"] else "")
        due, prio = res["callback_at"] if res["callback_at"] and res["callback_at"] > now_iso() else None, "high"
    elif out in ("no_answer", "busy", "switched_off"):
        title = f"Try again — last time: {RM.MEANINGS[out][0].lower()}"
    elif out == "they_will_call":
        title = "They said they'd call — call them if they haven't"
    elif out == "not_interested":
        title, prio = "Remarks say not interested — confirm and close", "low"
    elif out == "wrong_number":
        title, prio = "Wrong number in the sheet — find the right number", "low"
    if task and title:
        vals = {"title": title[:200]}
        if due:
            vals["due_at"] = due
        if prio:
            vals["priority"] = prio
        sets = ", ".join(f"{k} = ?" for k in vals)
        db.execute(f"UPDATE tasks SET {sets} WHERE id = ?", (*vals.values(), task["id"]))
        done.append("next call: " + title)
    # 3. A plain-English summary on the record, with the words it came from
    ev = "; ".join(f"{label} ← “{', '.join(words)[:60]}”" for label, words in res["evidence"])
    T.add_activity(db, actor["id"], "opportunity", opp_id, "note",
                   f"TeamOS read the remarks ({res['language']}): {res['summary']}\nFrom: {ev}"[:1500])
    done.append(res["summary"])
    return done
