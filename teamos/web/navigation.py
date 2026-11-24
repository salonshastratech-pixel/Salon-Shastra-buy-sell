"""One map of TeamOS: modules, their pages, and how a page relates to its module.

Every staff page gets, from this map:
* a **Back** button (to the page you came from inside TeamOS, otherwise the page above this one),
* a breadcrumb (Module / … / this page),
* the module's own sub-navigation (the other pages of the same module), filtered by what the person may open.
"""
from __future__ import annotations

from urllib.parse import urlparse

from flask import g, request, url_for

from ..permissions import user_can

# key, label, icon, pages: (label, endpoint, url-args, capability or None)
MODULES = [
    {"key": "today", "label": "Today", "icon": "sun", "pages": [
        ("My Day", "main.my_day", {}, None),
        ("Tasks", "tasks.index", {}, None),
        ("Calendar", "channels.calendar", {}, None),
        ("My availability", "team.mine", {}, None),
    ]},
    {"key": "team", "label": "Team", "icon": "users", "pages": [
        ("Workload", "main.team_board", {}, "crm.view"),
        ("Who's working", "team.week", {}, "crm.view"),
        ("Activity", "team.activity", {}, "crm.view"),
    ]},
    {"key": "owners", "label": "Salon Owners", "icon": "store", "pages": [
        ("All salon owners", "sellers.index", {}, "crm.view"),
        ("Pilot program", "pilot.index", {}, "crm.view"),
        ("Vikas's free times", "pilot.vikas", {}, "crm.view"),
        ("+ New salon owner", "sellers.new", {}, "crm.create"),
    ]},
    {"key": "buyers", "label": "Buyers & investors", "icon": "briefcase", "pages": [
        ("All buyers", "buyers.index", {}, "crm.view"),
        ("Needs review", "buyers.index", {"queue": "criteria"}, "crm.view"),
        ("+ New buyer", "buyers.new", {}, "crm.create"),
    ]},
    {"key": "deals", "label": "Matching & deals", "icon": "link", "pages": [
        ("Match Desk", "matches.desk", {}, "crm.view"),
        ("Deals", "deals.index", {}, "crm.view"),
    ]},
    {"key": "talk", "label": "Conversations", "icon": "chat", "pages": [
        ("WhatsApp inbox", "inbox.index", {}, "crm.view"),
        ("Bulk messages", "inbox.batches", {}, "crm.view"),
        ("Forms & requests", "forms.inbox", {}, "crm.view"),
    ]},
    {"key": "reports", "label": "Reports", "icon": "chart", "pages": [
        ("Overview", "reports.index", {}, "crm.view"),
        ("Lead sources", "workflow.sources", {}, "crm.view"),
        ("Learning", "improve.learning", {}, "crm.view"),
    ]},
    {"key": "admin", "label": "Tools & admin", "icon": "cog", "pages": [
        ("All tools", "improve.tools", {}, None),
        ("Users", "admin.users", {}, "users.manage"),
        ("Automations", "workflow.automations", {}, None),
        ("Imports", "imports.index", {}, "crm.import"),
        ("Duplicates", "admin.duplicates", {}, "duplicates.resolve"),
        ("Integrations", "admin.integrations", {}, "settings.manage"),
        ("Message templates", "admin.templates", {}, "settings.manage"),
        ("Lead ads", "channels.lead_ads", {}, "settings.manage"),
        ("Finance", "admin.finance", {}, "finance.view"),
        ("Jobs", "admin.jobs", {}, "settings.manage"),
        ("E-mail outbox", "admin.outbox", {}, "settings.manage"),
        ("Audit log", "admin.audit_log", {}, "audit.view"),
        ("Field dictionary", "main.dictionary", {}, None),
    ]},
]
BY_KEY = {m["key"]: m for m in MODULES}

# endpoint (or blueprint prefix) → module key
ENDPOINT_MODULE = {
    "main.my_day": "today", "tasks.": "today", "channels.calendar": "today", "team.mine": "today",
    "main.digest_preview": "today", "auth.change_password": "today", "main.search": "today",
    "main.team_board": "team", "team.": "team", "improve.hand_over": "team",
    "sellers.": "owners", "pilot.": "owners", "workflow.progress_preview": "owners",
    "buyers.": "buyers",
    "matches.": "deals", "deals.": "deals",
    "inbox.": "talk", "forms.": "talk",
    "reports.": "reports", "workflow.sources": "reports", "improve.learning": "reports",
    "admin.": "admin", "imports.": "admin", "channels.lead_ads": "admin", "workflow.automations": "admin",
    "improve.tools": "admin", "main.dictionary": "admin", "workflow.install": "admin", "main.planned": "admin",
}
# detail pages: where "up" goes when there is no earlier page to return to
PARENT = {
    "sellers.detail": "sellers.index", "sellers.new": "sellers.index", "buyers.detail": "buyers.index",
    "buyers.new": "buyers.index", "deals.detail": "deals.index", "matches.detail": "matches.desk",
    "tasks.detail": "tasks.index", "inbox.thread": "inbox.index", "inbox.batch": "inbox.batches",
    "forms.review": "forms.inbox", "imports.preview": "imports.index", "imports.smart_preview": "imports.index",
    "pilot.vikas": "pilot.index", "pilot.fill": "sellers.index", "pilot.answers": "sellers.index",
    "team.week": "main.team_board", "team.activity": "main.team_board",
}


def module_for(endpoint: str) -> str | None:
    if endpoint in ENDPOINT_MODULE:
        return ENDPOINT_MODULE[endpoint]
    for k, v in ENDPOINT_MODULE.items():
        if k.endswith(".") and endpoint.startswith(k):
            return v
    return None


def _allowed(db, user, cap) -> bool:
    return cap is None or user_can(db, user, cap)


def _safe_back(ref: str | None) -> str | None:
    """A same-site TeamOS page we may send someone back to (never the current page, login or a customer link)."""
    if not ref:
        return None
    u = urlparse(ref)
    if u.netloc and u.netloc != request.host:
        return None
    path = u.path or "/"
    if path == request.path or path.startswith(("/login", "/mfa", "/s/", "/f/", "/p/", "/static/")):
        return None
    return path + (("?" + u.query) if u.query else "")


def context(db) -> dict:
    user = g.get("user")
    ep = request.endpoint or ""
    if not user or ep.startswith(("public", "static", "pwa", "auth.login", "auth.mfa")):
        return {}
    key = module_for(ep)
    mod = BY_KEY.get(key) if key else None
    sub = []
    if mod:
        for label, endpoint, args, cap in mod["pages"]:
            if not _allowed(db, user, cap):
                continue
            try:
                href = url_for(endpoint, **args)
            except Exception:  # noqa: BLE001 — a page that doesn't exist in this build is simply not shown
                continue
            on = endpoint == ep and all(request.args.get(k) == str(v) for k, v in args.items()) and (
                args or not any(request.args.get(k) for k in ("queue",)))
            sub.append({"label": label, "href": href, "on": on})
    home = sub[0]["href"] if sub else url_for("main.my_day")
    is_home = any(s["on"] for s in sub)
    parent = PARENT.get(ep)
    up = url_for(parent) if parent else (home if not is_home else None)
    back = _safe_back(request.args.get("back")) or _safe_back(request.referrer) or up
    menu = []
    for m in MODULES:
        pages = [p for p in m["pages"] if _allowed(db, user, p[3])]
        if not pages:
            continue
        try:
            href = url_for(pages[0][1], **pages[0][2])
        except Exception:  # noqa: BLE001
            continue
        menu.append({"key": m["key"], "label": m["label"], "icon": m["icon"], "href": href, "on": m["key"] == key})
    links = []
    if ep in ("sellers.detail", "buyers.detail") and user_can(db, user, "crm.view"):
        oid = (request.view_args or {}).get("opp_id")
        opp = db.one("SELECT id, kind, contact_id FROM opportunities WHERE id = ?", (oid,)) if oid else None
        if opp:
            links = related(db, opp)
    return {"related_links": links, "nav": {"module": mod, "module_label": mod["label"] if mod else None, "home": home, "sub": sub,
                    "is_home": is_home, "back": back if not (is_home and back == home) else back,
                    "show_back": not (ep == "main.my_day"), "menu": menu}}


def related(db, opp: dict) -> list[dict]:
    """Everything in other modules connected to this salon owner / buyer — one strip at the top of the record."""
    oid, kind = opp["id"], opp["kind"]
    base = "sellers.detail" if kind == "seller" else "buyers.detail"
    out = []
    n = db.scalar("SELECT COUNT(*) FROM tasks WHERE entity_type = 'opportunity' AND entity_id = ? AND status = 'open'",
                  (oid,)) or 0
    out.append({"label": "Open tasks", "n": n, "href": url_for(base, opp_id=oid) + "#log-call", "tone": "warn" if n else ""})
    conv = db.one("SELECT c.id, c.unread FROM conversations c JOIN conversation_links l ON l.conversation_id = c.id "
                  "WHERE l.opportunity_id = ? ORDER BY c.last_message_at DESC LIMIT 1", (oid,))
    if not conv:
        conv = db.one("SELECT id, unread FROM conversations WHERE contact_id = ? ORDER BY last_message_at DESC LIMIT 1",
                      (opp["contact_id"],))
    if conv:
        out.append({"label": "WhatsApp chat", "n": conv["unread"] or None, "href": url_for("inbox.thread", conv_id=conv["id"]),
                    "tone": "hot" if conv["unread"] else ""})
    col = "seller_opp_id" if kind == "seller" else "buyer_opp_id"
    n = db.scalar(f"SELECT COUNT(*) FROM match_candidates WHERE {col} = ? AND suppressed = 0", (oid,)) or 0
    if n:
        out.append({"label": "Matches", "n": n, "href": url_for(base, opp_id=oid, tab="matches")})
    deals = db.all(f"SELECT id, ref_code FROM deals WHERE {col} = ? ORDER BY id DESC", (oid,))
    for d in deals[:2]:
        out.append({"label": f"Deal {d['ref_code']}", "href": url_for("deals.detail", deal_id=d["id"])})
    sub = db.one("SELECT id, status FROM form_submissions WHERE opportunity_id = ? ORDER BY id DESC LIMIT 1", (oid,))
    if sub:
        out.append({"label": "Form answers", "href": url_for("forms.review", sub_id=sub["id"]),
                    "tone": "warn" if sub["status"] in ("submitted", "pending_review") else ""})
    if kind == "seller":
        prof = db.one("SELECT form_status, chairpe_status FROM pilot_profiles WHERE opportunity_id = ?", (oid,))
        if prof:
            out.append({"label": "Pilot program", "href": url_for("sellers.detail", opp_id=oid, tab="pilot")})
    nxt = db.one("SELECT due_at FROM tasks WHERE entity_type = 'opportunity' AND entity_id = ? AND status = 'open' "
                 "AND task_type IN ('visit','call') AND due_at IS NOT NULL ORDER BY due_at LIMIT 1", (oid,))
    if nxt:
        out.append({"label": "Calendar", "href": url_for("channels.calendar"), "note": nxt["due_at"]})
    for o in db.all("SELECT id, kind, ref_code FROM opportunities WHERE contact_id = ? AND id <> ? ORDER BY id",
                    (opp["contact_id"], oid))[:3]:
        out.append({"label": ("Also a buyer " if o["kind"] == "buyer" else "Also selling ") + o["ref_code"],
                    "href": url_for("buyers.detail" if o["kind"] == "buyer" else "sellers.detail", opp_id=o["id"])})
    return out
