"""QA: open every page for each role in a real (headless) Chrome at phone, tablet and desktop sizes.

Reports per page: server errors, JavaScript errors, console errors (incl. CSP blocks), failed resources, the page being
wider than the screen, elements running off-screen (outside scrollable areas) and, on phones, the menu button working.
No extra packages: talks to Chrome over the DevTools protocol with a tiny built-in websocket client.

  QA_BASE_URL=http://127.0.0.1:8002 QA_LOGIN_FOUNDER=email:password ... python -I scripts/qa/viewport_sweep.py
"""
import base64
import http.cookiejar
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.environ.get("QA_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ROLES = (os.environ.get("QA_ROLES") or "founder,lead,shaheen,harika,accounts").split(",")
EXTRA = [u for u in (os.environ.get("QA_EXTRA_URLS") or "").split(",") if u]
PER_PATTERN = int(os.environ.get("QA_PER_PATTERN", "2"))
MAX_PAGES = int(os.environ.get("QA_MAX_PAGES", "400"))
VIEWPORTS = [v for v in [("phone", 375, 812, True), ("small-phone", 320, 640, True), ("tablet", 768, 1024, True),
             ("desktop", 1366, 900, False)] if v[0] in (os.environ.get("QA_VIEWPORTS") or "phone,small-phone,tablet,desktop").split(",")]
SKIP = ("logout", "/static/", "mailto:", "tel:", "/f/i/", "/t/", "/cal/", "format=csv", "/documents/", "/download",
        "/saved-searches/", "/export")
SEEDS = ["/", "/team", "/tasks/", "/tasks/?view=all", "/sellers/", "/sellers/?status=all", "/buyers/",
         "/buyers/?queue=criteria", "/matches/", "/deals/", "/forms/", "/imports/", "/dictionary", "/search?q=salon",
         "/inbox/", "/inbox/batches", "/reports/", "/reports/sources", "/reports/learning", "/calendar", "/tools",
         "/automations", "/install", "/admin/users", "/admin/audit", "/admin/duplicates", "/admin/finance",
         "/admin/jobs", "/admin/integrations", "/admin/templates", "/admin/outbox", "/admin/lead-ads", "/me/digest",
         "/sellers/new", "/buyers/new", "/pilot", "/pilot/vikas", "/team/availability", "/team/activity", "/team/activity?period=month", "/me/availability", "/investors", "/imports/remarks", "/imports/remarks?t=busy+ga+unnaru+repu+call+cheyandi"]
PUBLIC = ["/login", "/f/seller", "/f/buyer", "/f/seller?lang=te", "/f/buyer?lang=te", "/offline"]


def chrome_path():
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", shutil.which("chromium") or ""):
        if p and os.path.exists(p):
            return p
    raise SystemExit("No Chrome / Edge found")


# -- tiny websocket client (enough for the DevTools protocol) --------------------------------------------------
class WS:
    def __init__(self, url):
        u = urllib.parse.urlparse(url)
        self.s = socket.create_connection((u.hostname, u.port), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.s.recv(4096)
        self.rest = buf.split(b"\r\n\r\n", 1)[1]

    def _recv(self, n):
        while len(self.rest) < n:
            chunk = self.s.recv(1 << 16)
            if not chunk:
                raise ConnectionError("closed")
            self.rest += chunk
        out, self.rest = self.rest[:n], self.rest[n:]
        return out

    def send(self, obj):
        data = json.dumps(obj).encode()
        head = bytearray([0x81])
        n = len(data)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.s.sendall(bytes(head) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        msg = b""
        while True:
            b1, b2 = self._recv(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv(8))[0]
            payload = self._recv(n)
            op = b1 & 0x0F
            if op == 0x9:  # ping
                continue
            msg += payload
            if b1 & 0x80:
                return json.loads(msg.decode("utf-8", "replace"))


class CDP:
    def __init__(self, ws):
        self.ws, self.n, self.events = ws, 0, []

    def call(self, method, timeout=30, **params):
        self.n += 1
        my = self.n
        self.ws.send({"id": my, "method": method, "params": params})
        end = time.time() + timeout
        while time.time() < end:
            m = self.ws.recv()
            if m.get("id") == my:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})
            if "method" in m:
                self.events.append(m)
        raise TimeoutError(method)

    def wait(self, method, timeout=20):
        for i, e in enumerate(self.events):
            if e["method"] == method:
                return self.events.pop(i)
        end = time.time() + timeout
        self.ws.s.settimeout(timeout)
        while time.time() < end:
            m = self.ws.recv()
            if "method" in m:
                self.events.append(m)
                if m["method"] == method:
                    return m
        return None

    def drain(self, seconds):
        end = time.time() + seconds
        self.ws.s.settimeout(0.2)
        while time.time() < end:
            try:
                m = self.ws.recv()
                if "method" in m:
                    self.events.append(m)
            except (socket.timeout, TimeoutError):
                pass
        self.ws.s.settimeout(30)


# -- discovery (fast, over plain HTTP) -------------------------------------------------------------------------
class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and (a.get("href") or "").startswith("/"):
            self.links.append(a["href"].split("#")[0])


def creds(role):
    v = os.environ.get(f"QA_LOGIN_{role.upper()}") or f"{role}@demo.salonshastra.test:Demo@123"
    return v.split(":", 1)


def session(role):
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    email, pw = creds(role)
    op.open(BASE + "/login", urllib.parse.urlencode({"email": email, "password": pw}).encode())
    return op, cj


def pattern(url):
    p = urllib.parse.urlparse(url)
    q = "&".join(sorted(k for k, _ in urllib.parse.parse_qsl(p.query)))
    return re.sub(r"\d+", "N", p.path) + ("?" + q if q else "")


def discover(op):
    seen, queue, out, per = set(), list(SEEDS), [], {}
    while queue and len(out) < MAX_PAGES:
        u = queue.pop(0)
        if u in seen or any(s in u for s in SKIP):
            continue
        seen.add(u)
        pat = pattern(u)
        if per.get(pat, 0) >= PER_PATTERN:
            continue
        try:
            r = op.open(BASE + u, timeout=30)
            ctype, body = r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 405):
                continue
            out.append(u)
            continue
        except Exception:  # noqa: BLE001
            continue
        if "text/html" not in ctype:
            continue
        per[pat] = per.get(pat, 0) + 1
        out.append(u)
        lp = Links()
        lp.feed(body.decode("utf-8", "replace"))
        queue += [x for x in lp.links if x not in seen]
    return out


MEASURE = r"""(() => {
  const W = window.innerWidth, doc = document.documentElement;
  const clipped = el => { for (let p = el; p && p !== document.body; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (cs.position === 'fixed' || cs.display === 'none' || cs.visibility === 'hidden' || p.getAttribute('aria-hidden') === 'true') return true;
      if (p !== el && ['auto','scroll','hidden','clip'].includes(cs.overflowX)) return true; } return false; };
  const name = el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
      (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0,2).join('.') : '') +
      (el.textContent ? ' "' + el.textContent.trim().slice(0, 30).replace(/\s+/g,' ') + '"' : '');
  const bad = [];
  for (const el of document.body.querySelectorAll('*')) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    if ((r.right > W + 2 || r.left < -2) && !clipped(el)) bad.push(name(el) + ' →' + Math.round(r.right) + 'px');
  }
  let small = 0;
  if (W < 500) for (const el of document.querySelectorAll('a,button,input,select,textarea,summary')) {
    const r = el.getBoundingClientRect(); if (r.width && r.height && r.height < 24 && !clipped(el)) small++; }
  return {over: doc.scrollWidth - W, bad: [...new Set(bad)].slice(0, 5), small, title: document.title,
          h1: (document.querySelector('h1') || {}).textContent || ''};
})()"""

MENU = r"""new Promise(done => { const b = document.querySelector('.menu-toggle, [data-nav-toggle], button[aria-controls]');
  if (!b) return done('no menu button');
  b.click();
  setTimeout(() => { const nav = document.querySelector('.side, nav.nav'); const r = nav ? nav.getBoundingClientRect() : null;
    if (!(r && r.width > 100 && r.left >= -1 && r.right <= window.innerWidth + 1)) return done('menu did not open');
    if (b.getAttribute('aria-expanded') !== 'true') return done('menu open but aria-expanded not true');
    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}));
    setTimeout(() => { const r2 = nav.getBoundingClientRect(); done(r2.right <= 1 ? 'ok' : 'menu did not close with Escape'); }, 450);
  }, 450); })"""


def check(cdp, url, vp):
    cdp.events.clear()
    cdp.call("Page.navigate", url=BASE + url)
    loaded = cdp.wait("Page.loadEventFired", 25)
    cdp.drain(0.4)
    problems = []
    status = None
    for e in cdp.events:
        m, p = e["method"], e.get("params", {})
        if m == "Network.responseReceived":
            st, rt, ru = p["response"]["status"], p.get("type"), p["response"]["url"]
            if rt == "Document" and status is None:
                status = st
            elif st >= 400 and rt != "Document" and not ru.endswith("favicon.ico"):
                problems.append(f"resource {st}: {ru.replace(BASE, '')}")
        elif m == "Network.loadingFailed" and not p.get("canceled") and p.get("type") != "Document":
            problems.append(f"failed to load ({p.get('type')}): {p.get('errorText')}")
        elif m == "Runtime.exceptionThrown":
            d = p["exceptionDetails"]
            problems.append("JS error: " + ((d.get("exception") or {}).get("description") or d.get("text", ""))[:160])
        elif m == "Runtime.consoleAPICalled" and p.get("type") == "error":
            problems.append("console: " + " ".join(str(a.get("value", a.get("description", ""))) for a in p["args"])[:160])
        elif m == "Log.entryAdded" and p["entry"]["level"] == "error":
            t = p["entry"]["text"]
            if "favicon" not in t and "status of 4" not in t and "status of 5" not in t:
                problems.append("log: " + t[:160])
    if not loaded:
        problems.append("page did not finish loading in 25 s")
    if status and status >= 500:
        problems.append(f"SERVER ERROR {status}")
    try:
        r = cdp.call("Runtime.evaluate", expression=MEASURE, returnByValue=True)["result"]["value"]
    except Exception as e:  # noqa: BLE001
        return status, problems + [f"measure failed: {e}"], 0
    if r["over"] > 2:
        problems.append(f"page {r['over']}px wider than the {vp} screen")
    problems += [f"off-screen: {b}" for b in r["bad"]]
    return status, problems, r["small"]


def main():
    tmp = tempfile.mkdtemp(prefix="qa-chrome-")
    port = 9334
    proc = subprocess.Popen([chrome_path(), "--headless=new", f"--remote-debugging-port={port}",
                             f"--user-data-dir={tmp}", "--no-first-run", "--no-default-browser-check",
                             "--disable-extensions", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list"))
                page = next(p for p in pages if p["type"] == "page")
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.3)
        cdp = CDP(WS(page["webSocketDebuggerUrl"]))
        for d in ("Page", "Runtime", "Log", "Network"):
            cdp.call(d + ".enable")
        report, totals = {}, {"pages": 0, "problems": 0}
        plan = [("public", PUBLIC + EXTRA, None)]
        for role in ROLES:
            op, cj = session(role)
            plan.append((role, discover(op), cj))
            print(f"{role}: {len(plan[-1][1])} pages to check", flush=True)
        for vp, w, h, mobile in VIEWPORTS:
            cdp.call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=2 if mobile else 1,
                     mobile=mobile)
            cdp.call("Emulation.setTouchEmulationEnabled", enabled=mobile)
            for role, urls, cj in plan:
                cdp.call("Network.clearBrowserCookies")
                for c in (cj or []):
                    cdp.call("Network.setCookie", name=c.name, value=c.value, url=BASE + "/", httpOnly=True)
                small_pages = 0
                for u in urls:
                    try:
                        status, probs, small = check(cdp, u, vp)
                    except RuntimeError as e:  # e.g. a link the browser cannot open as a page
                        print(f"   skipped {u!r}: {e}")
                        continue
                    totals["pages"] += 1
                    small_pages += 1 if small > 3 else 0
                    if probs:
                        totals["problems"] += len(probs)
                        report.setdefault(f"{vp}/{role}", []).append({"url": u, "status": status, "problems": probs})
                if vp in ("phone", "small-phone") and role != "public" and urls:
                    menu = None
                    for _attempt in range(2):  # a slow machine can still be finishing the page load — try twice
                        check(cdp, "/", vp)
                        try:
                            menu = cdp.call("Runtime.evaluate", expression=MENU, returnByValue=True,
                                            awaitPromise=True)["result"].get("value")
                            break
                        except RuntimeError as e:
                            menu = f"menu check interrupted ({e})"
                    if menu != "ok":
                        report.setdefault(f"{vp}/{role}", []).append({"url": "/ (menu)", "problems": [menu]})
                        totals["problems"] += 1
                print(f"  {vp:8} {role:9} {len(urls):4} pages · problem pages: "
                      f"{len(report.get(f'{vp}/{role}', []))}", flush=True)
        out = os.environ.get("QA_REPORT", os.path.join(tempfile.gettempdir(), "viewport_sweep.json"))
        json.dump(report, open(out, "w", encoding="utf-8"), indent=1)
        print(f"\nChecked {totals['pages']} page views · {totals['problems']} problems · report: {out}")
        seen = {}
        for k, rows in report.items():
            for r in rows:
                for p in r["problems"]:
                    key = re.sub(r"\d+", "N", p)[:120]
                    seen.setdefault(key, []).append(f"{k} {r['url']}")
        for k, where in sorted(seen.items(), key=lambda x: -len(x[1]))[:60]:
            print(f"[{len(where):3}] {k}\n        e.g. {where[0]}")
    finally:
        proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
