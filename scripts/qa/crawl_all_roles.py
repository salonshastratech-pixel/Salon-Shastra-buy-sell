"""QA crawler: every reachable GET page for every demo role. Reports server errors, broken links and odd statuses."""
import http.cookiejar
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

sys.stdout.reconfigure(encoding="utf-8")
import os
BASE = os.environ.get("QA_BASE_URL", "http://127.0.0.1:8000")  # a running app with the synthetic demo data
USERS = ["founder", "lead", "shaheen", "harika", "accounts"]
SEEDS = ["/", "/team", "/tasks/", "/tasks/?view=all", "/tasks/?view=done", "/tasks/?view=created", "/sellers/",
         "/sellers/?status=all", "/buyers/", "/buyers/?queue=criteria", "/buyers/?queue=funding", "/buyers/?status=all",
         "/matches/", "/deals/", "/deals/?view=all", "/forms/", "/imports/", "/dictionary", "/search?q=glow",
         "/search?q=9000000", "/admin/users", "/admin/audit", "/admin/duplicates", "/admin/finance", "/admin/jobs",
         "/mfa/setup", "/sellers/new", "/buyers/new", "/healthz",
         "/inbox/", "/inbox/?q=unassigned", "/inbox/?q=unmatched", "/inbox/?q=open", "/inbox/?q=closed",
         "/inbox/batches", "/reports/", "/reports/?period=week", "/reports/?period=month", "/admin/integrations",
         "/admin/templates", "/admin/outbox", "/me/digest"]
SKIP = ("logout", "/static/", "mailto:", "/f/i/", "/t/")


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and (a.get("href") or "").startswith("/"):
            self.links.append(a["href"].split("#")[0])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def crawl(user):
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    # QA_LOGIN_<NAME>="email:password" overrides a demo login (e.g. staff who now have real accounts)
    email, pw = (os.environ.get(f"QA_LOGIN_{user.upper()}") or f"{user}@demo.salonshastra.test:Demo-Shastra-2026")         .split(":", 1)
    op.open(BASE + "/login", urllib.parse.urlencode({"email": email, "password": pw}).encode())
    nr = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), NoRedirect)
    seen, queue, res = set(), list(SEEDS), {}
    while queue and len(seen) < 600:
        p = queue.pop(0)
        if p in seen or any(s in p for s in SKIP):
            continue
        seen.add(p)
        try:
            r = nr.open(BASE + p)
            code, body, ctype = r.status, r.read().decode("utf-8", "replace"), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            code, body, ctype = e.code, e.read().decode("utf-8", "replace"), ""
            if code in (301, 302, 303):
                code = f"{code}->{e.headers.get('Location')}"
        if isinstance(code, int) and code == 200 and "Traceback" in body:
            code = "200-but-traceback"
        res[p] = code
        if code == 200 and "text/html" in ctype:
            parser = Links()
            parser.feed(body)
            for x in parser.links:
                # keep crawl bounded: one representative per tab of each record type
                if x not in seen:
                    queue.append(x)
    return res


bad_total = 0
for u in USERS:
    res = crawl(u)
    counts = {}
    for c in res.values():
        k = c if isinstance(c, int) else str(c).split("->")[0]
        counts[k] = counts.get(k, 0) + 1
    bad = {p: c for p, c in res.items() if str(c).startswith("5") or "traceback" in str(c) or c == 404}
    bad_total += len(bad)
    print(f"{u:9} pages={len(res):4}  statuses={dict(sorted(counts.items(), key=str))}")
    for p, c in sorted(bad.items()):
        print(f"   !! {c}  {p}")
print("TOTAL PROBLEMS:", bad_total)
