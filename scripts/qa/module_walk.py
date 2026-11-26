"""QA: walk the app module by module as a given user, on a COPY of a database.

For each menu module (in menu order) it opens every page reachable inside that module (bounded), then fills and
submits every form on those pages once with realistic values, follows redirects, and reports any crash (with the
traceback), 404 link, or page that came back with a "Traceback". Destructive actions (sign-out, deletes, resets,
the database move, going live) are skipped.

  python -I scripts/qa/module_walk.py <copy-of.db> <email> [password] [--modules owners,buyers]
"""
import re
import shutil
import sys
import tempfile
import traceback
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SKIP_ACTION = re.compile(r"/logout|/delete|/disable|reset-|reset_|/revoke|/mfa|database-move|/active$|go_live|"
                         r"test_whatsapp|password|/discard|/remove|/cancel|/withdraw|/unlink|records/bulk", re.I)
SKIP_LINK = ("/logout", "/static/", "mailto:", "tel:", "/f/i/", "/t/", "/cal/", "format=csv", "export=csv",
             "/documents/", "/download", ".pdf", "/healthz", "javascript:", "#")
PER_PATTERN = 3


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.forms, self._form, self._select, self._ta = [], [], None, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "form":
            self._form = {"action": a.get("action") or "", "method": (a.get("method") or "get").lower(), "fields": [],
                          "id": a.get("id")}
            self.forms.append(self._form)
        elif tag in ("input", "select", "textarea") and (self._form is not None or a.get("form")):
            f = {"tag": tag, **a}
            target = self._form
            if a.get("form"):
                target = next((x for x in self.forms if x.get("id") == a["form"]), None)
            if target is None:
                return
            target["fields"].append(f)
            if tag == "select":
                f["options"], self._select = [], f
            if tag == "textarea":
                f["text"], self._ta = "", f
        elif tag == "option" and self._select is not None:
            self._select["options"].append({"value": a.get("value"), "selected": "selected" in a})

    def handle_data(self, data):
        if self._ta is not None:
            self._ta["text"] += data

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        elif tag == "select":
            self._select = None
        elif tag == "textarea":
            self._ta = None


def pattern(path: str) -> str:
    """Same page kind = same pattern; each record tab (?tab=…) and queue (?queue=…) counts as its own page."""
    base = re.sub(r"\d+", "#", path.split("?")[0])
    q = dict(x.split("=", 1) for x in (path.split("?", 1)[1].split("&") if "?" in path else []) if "=" in x)
    keep = "&".join(f"{k}={q[k]}" for k in sorted(q) if k in ("tab", "queue", "view", "kind", "period", "q"))
    return base + ("?" + keep if keep else "")


def fill(form: dict) -> dict:
    data = {}
    radios_done = set()
    for f in form["fields"]:
        name = f.get("name")
        if not name or "disabled" in f:
            continue
        tag, typ = f["tag"], (f.get("type") or "text").lower()
        val = f.get("value")
        if tag == "select":
            opts = [o for o in f["options"] if o["value"] not in (None, "")]
            sel = next((o for o in f["options"] if o["selected"]), None)
            data[name] = (sel or (opts[0] if opts else {"value": ""}))["value"] or ""
        elif tag == "textarea":
            data[name] = f.get("text") or "QA check note"
        elif typ == "hidden":
            data.setdefault(name, val or "")
        elif typ == "checkbox":
            if "checked" in f or name in ("consent_contact", "acknowledge"):
                data.setdefault(name, val or "1")
        elif typ == "radio":
            if name not in radios_done:
                data[name] = val or "1"
                radios_done.add(name)
        elif typ in ("submit", "button", "file", "image", "reset"):
            continue
        elif val:
            data[name] = val
        elif typ == "email":
            data[name] = "qa.check@example.com"
        elif typ == "tel" or f.get("inputmode") == "tel" or "phone" in name or "mobile" in name:
            data[name] = "9000099999"
        elif typ == "number" or f.get("inputmode") in ("numeric", "decimal"):
            data[name] = "5"
        elif typ == "date":
            data[name] = "2030-01-15"
        elif typ == "datetime-local":
            data[name] = "2030-01-15T11:00"
        elif typ == "time":
            data[name] = "11:00"
        elif typ == "url":
            data[name] = "https://drive.google.com/drive/folders/qa"
        elif typ == "password":
            data[name] = "Qa-Check-Password-123"
        elif "confirm" in name:
            data[name] = ""
        else:
            data[name] = "QA check"
    return data


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    only = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--modules=")), "")
    src, email = args[0], args[1]
    password = args[2] if len(args) > 2 else "Demo@123"
    tmp = Path(tempfile.mkdtemp()) / "walk.db"
    shutil.copy(src, tmp)
    from teamos import create_app
    app = create_app({"DATABASE_URL": f"sqlite:///{tmp}", "TESTING": True, "SECRET_KEY": "qa-walk", "COOKIE_SECURE": False,
                      "MFA_REQUIRED_ROLES": "", "QUIET_HOURS": "", "UPLOAD_DIR": str(tmp.parent / "uploads")})
    from teamos.db import open_standalone
    db = open_standalone(f"sqlite:///{tmp}")
    db.execute("UPDATE users SET must_change_password = 0, mfa_enabled_at = NULL")
    db.close()
    c = app.test_client()
    r = c.post("/login", data={"email": email, "password": password})
    assert r.status_code == 302, f"login failed for {email}: {r.status_code}"
    from teamos.web import navigation as NAV
    problems, totals = [], {"pages": 0, "forms": 0}
    flashes = {}
    with app.test_request_context():
        from flask import url_for
        modules = []
        for m in NAV.MODULES:
            if only and m["key"] not in only.split(","):
                continue
            urls = []
            for label, ep, kw, _cap in m["pages"]:
                try:
                    urls.append(url_for(ep, **kw))
                except Exception:  # noqa: BLE001
                    problems.append((m["key"], "menu", f"{label}: cannot build link to {ep}"))
            modules.append((m["key"], m["label"], urls))

    def get(url):
        try:
            resp = c.get(url)
        except Exception:  # noqa: BLE001
            return None, traceback.format_exc(limit=6)
        if resp.status_code in (301, 302, 303):
            loc = resp.headers.get("Location", "")
            return resp.status_code, loc
        return resp.status_code, resp.get_data(as_text=True)

    for key, label, start in modules:
        seen, per = set(), {}
        queue = list(start)
        pages = []
        m_problems = 0
        while queue and len(pages) < 150:
            u = queue.pop(0)
            p = urlparse(u)
            if p.netloc or any(s in u for s in SKIP_LINK) or u in seen:
                continue
            pat = pattern(u)
            if per.get(pat, 0) >= PER_PATTERN and u not in start:
                continue
            per[pat] = per.get(pat, 0) + 1
            seen.add(u)
            code, body = get(u)
            totals["pages"] += 1
            if code is None:
                problems.append((key, u, "CRASH\n" + body))
                m_problems += 1
                continue
            if code == 404 or (code == 200 and "Traceback" in body) or (code or 0) >= 500:
                problems.append((key, u, f"status {code}"))
                m_problems += 1
                continue
            if code != 200:
                continue
            pg = Page()
            pg.feed(body)
            pages.append((u, pg))
            prefixes = {x.split("?")[0].split("/")[1] for x in start} - {""}
            for href in pg.links:
                full = urljoin(u, href)
                if full.startswith("/") and (full.split("/")[1] in prefixes or full in start):
                    queue.append(full)
        # every form on this module's pages, once per (page pattern, action pattern)
        done_forms = set()
        for u, pg in pages:
            for form in pg.forms:
                if form["method"] != "post":
                    continue
                action = urljoin(u, form["action"] or u)
                if SKIP_ACTION.search(action):
                    continue
                sig = (pattern(u), pattern(action), tuple(sorted(f.get("name") or "" for f in form["fields"])))
                if sig in done_forms:
                    continue
                done_forms.add(sig)
                data = fill(form)
                totals["forms"] += 1
                try:
                    resp = c.post(action, data=data)
                    hops = 0
                    while resp.status_code in (301, 302, 303) and hops < 5:
                        loc = resp.headers.get("Location", "")
                        if urlparse(loc).netloc and urlparse(loc).netloc != "localhost":
                            break  # e.g. wa.me — outside the app
                        resp = c.get(loc)
                        hops += 1
                    body = resp.get_data(as_text=True) if resp.status_code not in (301, 302, 303) else ""
                    for msg in re.findall(r'class="flash error">(.*?)</', body, re.S):
                        flashes.setdefault(re.sub(r"\s+", " ", msg).strip()[:160], set()).add(pattern(action))
                    if resp.status_code >= 500 or "Traceback" in body:
                        problems.append((key, f"POST {action} (from {u})", f"status {resp.status_code}"))
                        m_problems += 1
                    elif resp.status_code == 404:
                        problems.append((key, f"POST {action} (from {u})", "404 after submit"))
                        m_problems += 1
                except Exception:  # noqa: BLE001
                    problems.append((key, f"POST {action} (from {u})", "CRASH\n" + traceback.format_exc(limit=8)))
                    m_problems += 1
        print(f"{label:22} pages={len(pages):3} forms={len([1 for s in done_forms]):3} problems={m_problems}")
    print(f"\nTOTAL pages={totals['pages']} forms={totals['forms']} problems={len(problems)}")
    for key, where, what in problems:
        print(f"\n[{key}] {where}\n{what}")
    print("\nMessages shown after submitting (for review):")
    for msg, where in sorted(flashes.items()):
        print(f"  - {msg}   <- {', '.join(sorted(where))[:200]}")


if __name__ == "__main__":
    main()
