"""Spreadsheet import of seller leads (FR-004, PRD §5.3).

Flow: upload → parse → auto-map headers → validate every row → preview (new / duplicate in CRM /
duplicate within file / invalid) → per-row decisions → commit in one transaction. Nothing is
written to the CRM before commit; nothing existing is ever overwritten; raw cells are kept.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re

from .. import audit
from ..permissions import user_can
from ..timeutil import now_iso
from . import sellers as S
from .common import normalize_indian_mobile
from .errors import PermissionDenied, ValidationError
from .seller_fields import BY_KEY

MAX_ROWS = 2000
MAX_BYTES = 5 * 1024 * 1024

# Canonical import targets and the header spellings we recognise automatically.
TARGETS = {
    "full_name": ("Contact name", ["name", "contact name", "owner name", "full name", "owner", "contact"]),
    "phone": ("Mobile", ["mobile", "phone", "mobile number", "phone number", "contact number", "whatsapp", "mobile no"]),
    "alt_phone": ("Alternate phone", ["alternate phone", "alt phone", "alternate number", "other phone"]),
    "email": ("Email", ["email", "email id", "e-mail", "mail"]),
    "salon_name": ("Salon name", ["salon", "salon name", "business", "business name", "shop name"]),
    "city": ("City", ["city", "town"]),
    "locality": ("Locality", ["locality", "area", "location", "neighbourhood", "neighborhood"]),
    "source_note": ("Source detail", ["campaign", "source", "lead source", "ad name", "form name", "notes", "remarks"]),
    "asking_price": ("Asking price", ["asking price", "asking", "price", "expected price", "sale price"]),
    "sale_timeline": ("Sale timeline", ["timeline", "sale timeline", "when to sell"]),
    "chairs": ("Chairs", ["chairs", "stations", "no of chairs"]),
    "gender_format": ("Format", ["format", "salon type", "gender"]),
}
REQUIRED = ["full_name", "phone", "salon_name", "city", "locality"]
LISTING_TARGETS = {"asking_price", "sale_timeline", "chairs", "gender_format"}
ENUM_SYNONYMS = {
    "gender_format": {"men": "men", "male": "men", "gents": "men", "mens": "men", "women": "women", "female": "women",
                      "ladies": "women", "womens": "women", "unisex": "unisex", "family": "unisex"},
    "sale_timeline": {"immediate": "immediate", "immediately": "immediate", "asap": "immediate",
                      "1-3 months": "within_3m", "within 3 months": "within_3m", "3 months": "within_3m",
                      "3-6 months": "3_6m", "6-12 months": "6_12m", "no rush": "no_rush", "not decided": "no_rush"},
}


def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (h or "").lower()).strip()


def auto_map(headers: list[str]) -> dict:
    mapping, used = {}, set()
    for h in headers:
        n = _norm(h)
        for key, (_, synonyms) in TARGETS.items():
            if key not in used and n in synonyms:
                mapping[h] = key
                used.add(key)
                break
    return mapping


def parse_file(filename: str, data: bytes) -> tuple[list[str], list[list[str]]]:
    if len(data) > MAX_BYTES:
        raise ValidationError({"file": "File is larger than 5 MB."})
    name = filename.lower()
    if name.endswith(".csv"):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
    elif name.endswith(".xlsx"):
        try:
            from openpyxl import load_workbook
        except ImportError:  # pragma: no cover
            raise ValidationError({"file": "XLSX support needs openpyxl installed; upload CSV instead."})
        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception:
            raise ValidationError({"file": "Could not read this .xlsx file."})
        ws = wb.worksheets[0]
        rows = [["" if c is None else (str(int(c)) if isinstance(c, float) and c.is_integer() else str(c))
                 for c in r] for r in ws.iter_rows(values_only=True)]
    else:
        raise ValidationError({"file": "Upload a .csv or .xlsx file."})
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        raise ValidationError({"file": "The file is empty."})
    headers = [(h or "").strip() or f"Column {i + 1}" for i, h in enumerate(rows[0])]
    if len(set(headers)) != len(headers):
        raise ValidationError({"file": "Column headers must be unique."})
    body = [[(r[i] if i < len(r) else "") or "" for i in range(len(headers))] for r in rows[1:]]
    if len(body) > MAX_ROWS:
        raise ValidationError({"file": f"Maximum {MAX_ROWS} rows per import."})
    if not body:
        raise ValidationError({"file": "No data rows under the header."})
    return headers, body


def map_row(raw: dict, mapping: dict) -> dict:
    out = {}
    for header, key in mapping.items():
        if key and key in TARGETS:
            v = (raw.get(header) or "").strip()
            if key in ENUM_SYNONYMS and v:
                v = ENUM_SYNONYMS[key].get(_norm(v), v)
            out[key] = v
    return out


def validate(mapped: dict) -> dict:
    errors = {}
    for k in REQUIRED:
        if not mapped.get(k):
            errors[k] = "Missing"
    if mapped.get("phone"):
        _, err = normalize_indian_mobile(mapped["phone"])
        if err:
            errors["phone"] = err
    if mapped.get("alt_phone"):
        _, err = normalize_indian_mobile(mapped["alt_phone"])
        if err:
            errors["alt_phone"] = err
    for k in LISTING_TARGETS:
        if mapped.get(k):
            try:
                S._parse_one(BY_KEY[k], mapped[k])
            except ValueError as e:
                errors[k] = str(e)
    return errors


def classify(db, batch_id: int) -> None:
    """(Re)compute each row's status from the batch mapping. Safe to call repeatedly before commit."""
    batch = db.one("SELECT * FROM import_batches WHERE id = ?", (batch_id,))
    mapping = json.loads(batch["mapping"])
    seen: dict[str, int] = {}
    for r in db.all("SELECT * FROM import_rows WHERE batch_id = ? ORDER BY row_no", (batch_id,)):
        mapped = map_row(json.loads(r["raw"]), mapping)
        errors = validate(mapped)
        status, dup_contact, dup_row, decision = "new", None, None, "create"
        phone = normalize_indian_mobile(mapped.get("phone"))[0] if mapped.get("phone") else None
        if errors:
            status, decision = "invalid", "skip"
        elif phone and phone in seen:
            status, dup_row, decision = "duplicate_file", seen[phone], "skip"
        else:
            dups = S.find_duplicates(db, phone, mapped.get("salon_name", ""), mapped.get("locality", ""))
            if dups:
                status, dup_contact, decision = "duplicate_crm", dups[0]["id"], "skip"
        if phone and status != "invalid":
            seen.setdefault(phone, r["row_no"])
        db.execute("UPDATE import_rows SET status = ?, errors = ?, duplicate_contact_id = ?, duplicate_of_row = ?, "
                   "decision = ? WHERE id = ?",
                   (status, json.dumps(errors) if errors else None, dup_contact, dup_row, decision, r["id"]))


def create_batch(db, actor, filename: str, data: bytes) -> int:
    if not user_can(db, actor, "crm.import"):
        raise PermissionDenied("crm.import")
    headers, body = parse_file(filename, data)
    sha = hashlib.sha256(data).hexdigest()
    with db:
        bid = db.insert("import_batches", {
            "kind": "seller", "filename": filename[:200], "file_sha256": sha, "status": "preview",
            "mapping": json.dumps(auto_map(headers)), "headers": json.dumps(headers), "total_rows": len(body),
            "owner_id": None, "uploaded_by": actor["id"], "created_at": now_iso()})   # None = share in turn
        for i, row in enumerate(body, start=2):  # row 1 is the header, as the user sees it in Excel
            db.insert("import_rows", {"batch_id": bid, "row_no": i, "raw": json.dumps(dict(zip(headers, row))),
                                      "status": "new"})
        classify(db, bid)
        audit.record(db, actor["id"], "import.upload", "import_batch", bid,
                     {"filename": filename, "rows": len(body), "sha256": sha})
    return bid


def set_mapping(db, actor, batch_id: int, mapping: dict, owner_id) -> None:
    """owner_id: a user id, "auto" (share between the team in turn — the default) or None (keep as is)."""
    batch = _editable(db, actor, batch_id)
    headers = json.loads(batch["headers"])
    clean = {h: k for h, k in mapping.items() if h in headers and k in TARGETS}
    if len(set(clean.values())) != len(clean):
        raise ValidationError({"mapping": "Each field can be mapped from only one column."})
    with db:
        owner = None if owner_id == "auto" else (owner_id or batch["owner_id"])
        db.update("import_batches", batch_id, {"mapping": json.dumps(clean), "owner_id": owner})
        classify(db, batch_id)


def _editable(db, actor, batch_id):
    if not user_can(db, actor, "crm.import"):
        raise PermissionDenied("crm.import")
    batch = db.one("SELECT * FROM import_batches WHERE id = ?", (batch_id,))
    if not batch:
        raise ValidationError("Import not found.")
    if batch["status"] != "preview":
        raise ValidationError("This import has already been committed or discarded.")
    return batch


ALLOWED_DECISIONS = {
    "new": {"create", "skip"},
    "duplicate_crm": {"skip", "link", "flag"},
    "duplicate_file": {"skip", "flag"},
    "invalid": {"skip"},
}


def commit(db, actor, batch_id: int, decisions: dict[int, str], first_task_due: str | None) -> dict:
    batch = _editable(db, actor, batch_id)
    mapping = json.loads(batch["mapping"])
    missing = [TARGETS[k][0] for k in REQUIRED if k not in mapping.values()]
    if missing:
        raise ValidationError({"mapping": "Map these required columns first: " + ", ".join(missing)})
    counts = {"created": 0, "skipped": 0, "changed_since_preview": 0}
    with db:
        classify(db, batch_id)  # re-check against the CRM as it is *now*
        rows = db.all("SELECT * FROM import_rows WHERE batch_id = ? ORDER BY row_no", (batch_id,))
        for r in rows:
            decision = decisions.get(r["id"], r["decision"])
            if decision not in ALLOWED_DECISIONS[r["status"]]:
                decision = "skip"
            outcome, opp_id = "skipped", None
            if decision != "skip":
                mapped = map_row(json.loads(r["raw"]), mapping)
                if batch["owner_id"]:
                    owner = batch["owner_id"]
                else:
                    from .progress import round_robin
                    owner = round_robin(db, "seller") or actor["id"]
                data = {**mapped, "source": "import", "owner_id": str(owner), "consent_contact": "",
                        "source_note": f"Import #{batch_id} ({batch['filename']}) row {r['row_no']}"
                                       + (f" · {mapped['source_note']}" if mapped.get("source_note") else "")}
                dd = None
                if decision == "link" and r["duplicate_contact_id"]:
                    dd = f"link:{r['duplicate_contact_id']}"
                elif decision == "flag":
                    dd = "new_flagged"
                result = S.create_lead(db, actor, data, dedupe_decision=dd, first_task_due=first_task_due,
                                       first_task_priority="normal")
                if "duplicates" in result:
                    outcome = "changed_since_preview"  # became a duplicate after preview: never auto-create
                else:
                    outcome, opp_id = "created", result["opportunity_id"]
                    extra = {k: mapped[k] for k in LISTING_TARGETS if mapped.get(k) and not BY_KEY[k].quick}
                    if extra:
                        values, _ = S._parse_fields(extra, list(extra))
                        listing_id = db.scalar("SELECT id FROM seller_listings WHERE opportunity_id = ?", (opp_id,))
                        S._apply_values(db, actor, listing_id, values, source=f"import:{batch_id}", bump_version=False)
            counts[outcome] += 1
            db.execute("UPDATE import_rows SET decision = ?, outcome = ?, opportunity_id = ? WHERE id = ?",
                       (decision, outcome, opp_id, r["id"]))
        db.update("import_batches", batch_id, {"status": "committed", "created_count": counts["created"],
                                               "committed_by": actor["id"], "committed_at": now_iso()})
        audit.record(db, actor["id"], "import.commit", "import_batch", batch_id, counts)
    return counts


def discard(db, actor, batch_id: int) -> None:
    _editable(db, actor, batch_id)
    with db:
        db.update("import_batches", batch_id, {"status": "discarded"})
        audit.record(db, actor["id"], "import.discard", "import_batch", batch_id)


def summary(db, batch_id: int) -> dict:
    rows = db.all("SELECT status, COUNT(*) AS n FROM import_rows WHERE batch_id = ? GROUP BY status", (batch_id,))
    return {r["status"]: r["n"] for r in rows}
