"""Others: people who call Salon Shastra for a JOB at a salon, or for ACADEMY training.

Job seekers get an HR screening (role, experience, skills, current/expected salary, where they live and where they are
from, notice period, preferences) so the team can map them to the right salon. Academy callers get a counsellor's
intake (courses, goal, level, when they can join, budget approach — lowest cost or real learning, support needed).
Each inquiry has an owner, a status, call history and follow-up reminders that appear in Tasks.
"""
from __future__ import annotations

import json

from .. import audit
from ..permissions import user_can
from ..timeutil import now_iso, parse_local_input
from . import tasks as T
from .common import normalize_indian_mobile
from .errors import PermissionDenied, ValidationError

KINDS = {"job": "Job inquiry", "academy": "Academy inquiry"}
PREFIX = {"job": "JB", "academy": "AC"}

# -- The questions ---------------------------------------------------------------------------------------------------
# (key, label, type, options, help) — type: one (pick one) | many (pick any) | text | number
YES_NO = ["Yes", "No"]
GENDER = ["Female", "Male", "Other"]

JOB_ROLES = ["Hair stylist (women)", "Men's barber / stylist", "Unisex hair stylist", "Hair colour / chemical expert",
             "Beautician / skin therapist", "Makeup artist", "Nail technician", "Spa / massage therapist",
             "Mehendi artist", "Receptionist / front desk", "Salon manager", "Helper / trainee", "Other"]
JOB_SKILLS = ["Women's haircuts", "Men's cuts & beard", "Hair colour & highlights", "Keratin / smoothening",
              "Hair spa & treatments", "Facials & clean-up", "Waxing & threading", "Pedicure & manicure",
              "Nail extensions & art", "Party makeup", "Bridal makeup", "Massage", "Billing / software",
              "Customer handling & sales"]
JOB_QUESTIONS = [
    ("About them", [
        ("gender", "Gender", "one", GENDER, "Some salons hire for men's or women's sections."),
        ("age", "Age", "number", None, ""),
        ("languages", "Languages they speak", "many", ["Telugu", "Hindi", "English", "Urdu", "Tamil", "Kannada", "Other"], ""),
        ("education", "Education", "one", ["Below 10th", "10th", "12th / Inter", "Graduate", "Other"], ""),
    ]),
    ("Work they want", [
        ("roles", "Role(s) wanted", "many", JOB_ROLES, ""),
        ("experience", "Years of experience", "one", ["Fresher", "Less than 1 year", "1–3 years", "3–5 years",
                                                       "5–10 years", "10+ years"], ""),
        ("skills", "What they are good at", "many", JOB_SKILLS, ""),
        ("training", "Course / certificate done", "text", None, "e.g. 6-month hair course at … academy"),
        ("salon_type", "Salon type they prefer", "many", ["Men's salon", "Women's salon / parlour", "Unisex salon",
                                                           "Luxury / premium", "Any"], ""),
    ]),
    ("Current job & pay", [
        ("working_now", "Working now?", "one", ["Yes", "No", "Serving notice"], ""),
        ("current_salon", "Current / last salon (name & area)", "text", None, ""),
        ("current_salary", "Current salary (₹ a month)", "number", None, ""),
        ("expected_salary", "Expected salary (₹ a month)", "number", None, ""),
        ("incentive_ok", "OK with salary + incentive / commission?", "one", YES_NO + ["Fixed only"], ""),
        ("join_when", "Can join", "one", ["Immediately", "Within 15 days", "Within a month", "Later"], ""),
    ]),
    ("Location & conditions", [
        ("work_areas", "Areas they can work in", "text", None, "e.g. Kukatpally, KPHB, Miyapur"),
        ("relocate", "Ready to move to another city?", "one", YES_NO, ""),
        ("stay_needed", "Needs accommodation?", "one", YES_NO, "Common for people coming from other towns."),
        ("shift", "Shift preference", "one", ["Full day", "Morning", "Evening", "Weekends only", "Any"], ""),
        ("portfolio", "Can share work photos / resume on WhatsApp?", "one", YES_NO, ""),
    ]),
]

ACADEMY_COURSES = ["Hairdressing (cutting & styling)", "Men's barbering & grooming", "Hair colour & chemical treatments",
                   "Keratin, smoothening & hair spa", "Hair extensions", "Beauty therapy (facials, waxing, threading)",
                   "Cosmetology diploma", "Advanced skin / aesthetics", "Makeup artistry", "Bridal & HD makeup",
                   "Hairstyling for events & brides", "Nail art & extensions", "Spa & massage therapy",
                   "Eyelash & eyebrow (lash extensions, microblading)", "Mehendi", "Salon management / business",
                   "Train-the-trainer (become an educator)", "Not sure — needs guidance"]
ACADEMY_QUESTIONS = [
    ("About the student", [
        ("gender", "Gender", "one", GENDER, ""),
        ("age", "Age", "number", None, ""),
        ("education", "Education", "one", ["10th", "12th / Inter", "Graduate", "Post-graduate", "Other"], ""),
        ("occupation", "What they do now", "one", ["Student", "Working in a salon", "Salon owner", "Homemaker",
                                                   "Working in another field", "Looking for work"], ""),
        ("decision_by", "Who decides / pays", "one", ["Self", "Parents / family", "Employer"], ""),
    ]),
    ("Course", [
        ("courses", "Courses interested in", "many", ACADEMY_COURSES, ""),
        ("level", "Their current level", "one", ["Complete beginner", "Some practice / self-taught",
                                                  "Working professional (upskill)"], ""),
        ("course_type", "Kind of course", "one", ["Short certificate", "Professional course",
                                                   "Diploma (long)", "Advanced masterclass", "Not sure"], ""),
        ("goal", "Why they want to learn", "many", ["Get a salon job", "Start own salon / studio",
                                                    "Freelance / home service", "Grow in current job",
                                                    "Work abroad", "Personal interest"], ""),
    ]),
    ("Timing & fees", [
        ("join_when", "Willing to join", "one", ["This month", "Next month", "In 2–3 months", "Just enquiring"], ""),
        ("batch", "Batch that suits them", "one", ["Weekday", "Weekend", "Evening", "Fast-track (daily, full day)",
                                                   "Online / blended"], ""),
        ("duration", "How long can they study", "one", ["Up to 1 month", "1–3 months", "3–6 months", "6–12 months",
                                                        "Flexible"], ""),
        ("fee_approach", "What matters more", "one", ["Lowest fee possible", "Balance of fee and quality",
                                                      "Real learning — flexible on fee"],
         "Ask: “Are you looking for the lowest fee, or the best training even if it costs more?”"),
        ("budget", "Budget", "one", ["Under ₹25,000", "₹25,000–50,000", "₹50,000–1 lakh", "₹1–2 lakh", "₹2 lakh +",
                                     "Not decided"], ""),
        ("support", "Support they need", "many", ["EMI / instalments", "Education loan", "Hostel / stay",
                                                  "Job placement after course", "Kit / products included"], ""),
    ]),
    ("Next step", [
        ("compared", "Other academies they are considering", "text", None, ""),
        ("visit", "Will they visit for a demo / counselling?", "one", ["Yes — fixed a date", "Maybe", "Online call first",
                                                                       "No"], ""),
    ]),
]
QUESTIONS = {"job": JOB_QUESTIONS, "academy": ACADEMY_QUESTIONS}
ALL_Q = {k: {q[0]: q for _s, qs in sections for q in qs} for k, sections in QUESTIONS.items()}
# what the list shows / filters on
SUMMARY = {"job": ("roles", "experience", "expected_salary", "join_when"),
           "academy": ("courses", "level", "fee_approach", "join_when")}
SUMMARY_LABEL = {"job": ("Role", "Experience", "Expects ₹/month", "Can join"),
                 "academy": ("Course", "Level", "Fees", "Joins")}

STATUSES = {
    "job": [("new", "New"), ("contacted", "Contacted"), ("screened", "Screened"), ("interview", "Interview set"),
            ("placed", "Placed"), ("on_hold", "On hold"), ("not_suitable", "Not suitable"),
            ("not_interested", "Not interested"), ("closed", "Closed")],
    "academy": [("new", "New"), ("contacted", "Contacted"), ("counselled", "Counselled"), ("demo", "Visit / demo set"),
                ("enrolled", "Enrolled"), ("on_hold", "On hold"), ("not_interested", "Not interested"),
                ("closed", "Closed")],
}
OPEN_STATUSES = ("new", "contacted", "screened", "interview", "counselled", "demo", "on_hold")
TEMPS = [("hot", "Hot"), ("warm", "Warm"), ("cold", "Cold")]
SOURCES = [("incoming_call", "Incoming call"), ("walk_in", "Walk-in"), ("whatsapp", "WhatsApp"),
           ("meta_ad", "Meta / Instagram ad"), ("referral", "Referral"), ("website", "Website"), ("other", "Other")]
SOURCE_LABELS = dict(SOURCES)


def status_label(kind: str, status: str) -> str:
    return dict(STATUSES.get(kind, [])).get(status, status.replace("_", " ").title())


def answers_of(inq: dict) -> dict:
    try:
        return json.loads(inq.get("answers") or "{}")
    except ValueError:
        return {}


def show(value) -> str:
    if value in (None, "", []):
        return ""
    if isinstance(value, list):
        return ", ".join(value)
    if isinstance(value, (int, float)) and value >= 1000:
        return f"₹{value:,.0f}".replace(",", ",")
    return str(value)


# -- Saving ----------------------------------------------------------------------------------------------------------
def _need(db, actor, cap):
    if not user_can(db, actor, cap):
        raise PermissionDenied(cap)


def _answers_from(kind: str, data) -> dict:
    out = {}
    for key, (_k, _label, typ, opts, _h) in ALL_Q[kind].items():
        if typ == "many":
            vals = data.getlist(key) if hasattr(data, "getlist") else (data.get(key) or [])
            vals = [v for v in vals if v in opts]
            if vals:
                out[key] = vals
            continue
        raw = (data.get(key) or "").strip()
        if not raw:
            continue
        if typ == "one":
            if raw in opts:
                out[key] = raw
        elif typ == "number":
            digits = "".join(ch for ch in raw if ch.isdigit())
            if digits:
                out[key] = int(digits[:9])
        else:
            out[key] = raw[:300]
    return out


def _clean(db, actor, data, kind: str) -> dict:
    errors, out = {}, {}
    name = (data.get("full_name") or "").strip()
    if not name:
        errors["full_name"] = "Enter their name."
    out["full_name"] = name[:120]
    phone, err = normalize_indian_mobile(data.get("phone"))
    if err:
        errors["phone"] = err
    out["phone_normalized"] = phone
    alt = (data.get("alt_phone") or "").strip()
    if alt:
        alt_n, aerr = normalize_indian_mobile(alt)
        if aerr:
            errors["alt_phone"] = aerr
        out["alt_phone"] = alt_n
    else:
        out["alt_phone"] = None
    for k, n in (("email", 200), ("city", 80), ("locality", 80), ("hometown", 120)):
        out[k] = (data.get(k) or "").strip()[:n] or None
    out["notes"] = (data.get("notes") or "").strip()[:3000] or None
    src = data.get("source") or "incoming_call"
    out["source"] = src if src in SOURCE_LABELS else "other"
    temp = data.get("temperature") or None
    out["temperature"] = temp if temp in dict(TEMPS) else None
    owner = data.get("owner_id")
    out["owner_id"] = int(owner) if owner and str(owner).isdigit() else actor["id"]
    if out["owner_id"] != actor["id"] and not user_can(db, actor, "crm.assign"):
        out["owner_id"] = actor["id"]
    if errors:
        raise ValidationError(errors)
    ans = _answers_from(kind, data)
    s = SUMMARY[kind]
    out.update({"answers": json.dumps(ans, ensure_ascii=False), "interest": show(ans.get(s[0])) or None,
                "experience": show(ans.get(s[1])) or None,
                "budget_or_salary": show(ans.get(s[2])) or (show(ans.get("budget")) if kind == "academy" else None),
                "join_when": show(ans.get(s[3])) or None})
    return out


def duplicate_of(db, kind: str, phone: str | None, not_id: int | None = None) -> dict | None:
    if not phone:
        return None
    return db.one("SELECT id, ref_code, status FROM inquiries WHERE kind = ? AND phone_normalized = ? AND id <> ? "
                  "ORDER BY id DESC LIMIT 1", (kind, phone, not_id or 0))


def create(db, actor, kind: str, data) -> int:
    _need(db, actor, "crm.create")
    if kind not in KINDS:
        raise ValidationError({"kind": "Choose job or academy."})
    vals = _clean(db, actor, data, kind)
    dup = duplicate_of(db, kind, vals["phone_normalized"])
    if dup and not data.get("allow_duplicate"):
        raise ValidationError({"phone": f"This number already has a {KINDS[kind].lower()} ({dup['ref_code']}). "
                                        "Open it and log this call there — or tick 'Save as a new inquiry anyway'."})
    now = now_iso()
    with db:
        iid = db.insert("inquiries", {**vals, "kind": kind, "status": "new", "created_by": actor["id"],
                                      "created_at": now, "updated_at": now, "last_contact_at": now})
        db.execute("UPDATE inquiries SET ref_code = ? WHERE id = ?", (f"{PREFIX[kind]}-{iid:05d}", iid))
        T.add_activity(db, actor["id"], "inquiry", iid, "call" if vals["source"] == "incoming_call" else "note",
                       f"{KINDS[kind]} recorded ({SOURCE_LABELS[vals['source']]})")
        _follow_up(db, actor, iid, vals["full_name"], kind, vals["owner_id"], data)
        audit.record(db, actor["id"], "inquiry.create", "inquiry", iid, {"kind": kind})
    return iid


def _follow_up(db, actor, iid, name, kind, owner_id, data):
    due = parse_local_input(data.get("follow_up_due") or "")
    if due:
        T.create_task(db, actor, title=(data.get("follow_up_title") or "").strip()
                      or f"Call back {name} ({KINDS[kind].lower()})", assignee_id=owner_id or actor["id"],
                      due_at=due, task_type="call", entity_type="inquiry", entity_id=iid)


def update(db, actor, inq: dict, data) -> None:
    _need(db, actor, "crm.edit")
    vals = _clean(db, actor, data, inq["kind"])
    with db:
        db.execute("UPDATE inquiries SET " + ", ".join(f"{k} = ?" for k in vals) + ", updated_at = ? WHERE id = ?",
                   (*vals.values(), now_iso(), inq["id"]))
        T.add_activity(db, actor["id"], "inquiry", inq["id"], "data_update", "Details updated")
        audit.record(db, actor["id"], "inquiry.update", "inquiry", inq["id"], {})


def log(db, actor, inq: dict, data) -> None:
    """A call or note, an optional new status / temperature, and an optional follow-up."""
    _need(db, actor, "crm.log_activity")
    note = (data.get("note") or "").strip()[:2000]
    status = data.get("status") or inq["status"]
    if status not in dict(STATUSES[inq["kind"]]):
        raise ValidationError({"status": "Choose a status."})
    temp = data.get("temperature") if data.get("temperature") in dict(TEMPS) else inq.get("temperature")
    due = parse_local_input(data.get("follow_up_due") or "")
    if not note and status == inq["status"] and temp == inq.get("temperature") and not due:
        raise ValidationError({"note": "Write what happened, change the status or set a call-back."})
    now = now_iso()
    with db:
        parts = [note] if note else []
        if status != inq["status"]:
            parts.append(f"Status: {status_label(inq['kind'], inq['status'])} → {status_label(inq['kind'], status)}")
        if parts:
            T.add_activity(db, actor["id"], "inquiry", inq["id"], "call" if data.get("was_call") else "note",
                           " · ".join(parts))
        db.execute("UPDATE inquiries SET status = ?, temperature = ?, updated_at = ?, last_contact_at = ? WHERE id = ?",
                   (status, temp, now, now if data.get("was_call") else inq["last_contact_at"], inq["id"]))
        if status not in OPEN_STATUSES:     # finished: their open reminders are no longer needed
            db.execute("UPDATE tasks SET status = 'cancelled', outcome = ?, updated_at = ? WHERE entity_type = "
                       "'inquiry' AND entity_id = ? AND status = 'open'",
                       (f"Inquiry {status_label(inq['kind'], status).lower()}", now, inq["id"]))
        _follow_up(db, actor, inq["id"], inq["full_name"], inq["kind"], inq["owner_id"], data)


# -- Reading ---------------------------------------------------------------------------------------------------------
def load(db, iid: int) -> dict | None:
    r = db.one("SELECT i.*, u.full_name AS owner_name FROM inquiries i LEFT JOIN users u ON u.id = i.owner_id "
               "WHERE i.id = ?", (iid,))
    if r:
        r["a"] = answers_of(r)
    return r


FILTERS = {  # extra filters per kind: (arg, answer key, label, options)
    "job": [("role", "roles", "Any role", JOB_ROLES),
            ("exp", "experience", "Any experience", ALL_Q["job"]["experience"][3])],
    "academy": [("course", "courses", "Any course", ACADEMY_COURSES),
                ("fees", "fee_approach", "Any fee approach", ALL_Q["academy"]["fee_approach"][3]),
                ("joins", "join_when", "Joining any time", ALL_Q["academy"]["join_when"][3])],
}


def search(db, args) -> tuple[list[dict], int]:
    where, params = [], []
    kind = args.get("kind")
    if kind in KINDS:
        where.append("i.kind = ?")
        params.append(kind)
    st = args.get("status") or "open"
    if st == "open":
        where.append("i.status IN (%s)" % ",".join("?" * len(OPEN_STATUSES)))
        params += list(OPEN_STATUSES)
    elif st != "all":
        where.append("i.status = ?")
        params.append(st)
    if args.get("temp") in dict(TEMPS):
        where.append("i.temperature = ?")
        params.append(args["temp"])
    q = (args.get("q") or "").strip()
    if q:
        like = f"%{q.lower()}%"
        cond = ("(lower(i.full_name) LIKE ? OR lower(COALESCE(i.interest, '')) LIKE ? OR lower(COALESCE(i.city, '')) "
                "LIKE ? OR lower(COALESCE(i.locality, '')) LIKE ? OR lower(COALESCE(i.hometown, '')) LIKE ? OR "
                "lower(COALESCE(i.ref_code, '')) LIKE ? OR lower(COALESCE(i.notes, '')) LIKE ?")
        params += [like] * 7
        digits = "".join(ch for ch in q if ch.isdigit())
        if len(digits) >= 4:
            cond += " OR i.phone_normalized LIKE ?"
            params.append(f"%{digits}%")
        where.append(cond + ")")
    owner = args.get("owner")
    if owner and str(owner).isdigit():
        where.append("i.owner_id = ?")
        params.append(int(owner))
    sql = ("SELECT i.*, u.full_name AS owner_name, (SELECT MIN(t.due_at) FROM tasks t WHERE t.entity_type = 'inquiry' "
           "AND t.entity_id = i.id AND t.status = 'open') AS next_due FROM inquiries i LEFT JOIN users u ON "
           "u.id = i.owner_id" + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY i.created_at DESC, i.id DESC LIMIT 500")
    rows = db.all(sql, params)
    for r in rows:
        r["a"] = answers_of(r)
    # answer filters (role, course, fees…): applied on the answers
    for k in (kind,) if kind in KINDS else ():
        for arg, key, _l, _o in FILTERS[k]:
            want = args.get(arg)
            if want:
                rows = [r for r in rows if want == r["a"].get(key) or want in (r["a"].get(key) or [])]
    return rows, len(rows)


def counts(db) -> dict:
    out = {"job": 0, "academy": 0}
    for r in db.all("SELECT kind, COUNT(*) AS n FROM inquiries WHERE status IN (%s) GROUP BY kind"
                    % ",".join("?" * len(OPEN_STATUSES)), list(OPEN_STATUSES)):
        out[r["kind"]] = r["n"]
    return out


def history(db, iid: int) -> list[dict]:
    return db.all("SELECT a.*, u.full_name AS actor_name FROM activities a LEFT JOIN users u ON u.id = a.actor_id "
                  "WHERE a.entity_type = 'inquiry' AND a.entity_id = ? ORDER BY a.at DESC, a.id DESC", (iid,))


def open_tasks(db, iid: int) -> list[dict]:
    return db.all("SELECT * FROM tasks WHERE entity_type = 'inquiry' AND entity_id = ? AND status = 'open' "
                  "ORDER BY due_at", (iid,))
