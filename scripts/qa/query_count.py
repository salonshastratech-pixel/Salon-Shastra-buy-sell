"""QA: how many database queries (and how much time) each page takes, on a COPY of a database.

  python -I scripts/qa/query_count.py <copy-of.db> <email> <password> [paths...]
Every query is a network round trip on the live server, so the count matters more than local time.
"""
import collections
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from teamos import create_app  # noqa: E402
from teamos import db as D  # noqa: E402

STATS = {"n": 0, "sql": collections.Counter()}
_orig = D.Database.execute


def counted(self, sql, params=()):
    STATS["n"] += 1
    STATS["sql"][" ".join(str(sql).split())[:110]] += 1
    return _orig(self, sql, params)


D.Database.execute = counted


def main():
    dbfile, email, pw, *paths = sys.argv[1:]
    app = create_app({"DATABASE_URL": f"sqlite:///{dbfile}", "COOKIE_SECURE": False, "SECRET_KEY": "qa",
                      "QUIET_HOURS": "", "MFA_REQUIRED_ROLES": "", "TESTING": True})  # background work counted with the page that started it
    c = app.test_client()
    r = c.post("/login", data={"email": email, "password": pw})
    assert r.status_code == 302, r.status_code
    for p in paths or ["/", "/sellers/", "/buyers/"]:
        STATS["n"] = 0
        STATS["sql"].clear()
        t = time.perf_counter()
        r = c.get(p)
        ms = (time.perf_counter() - t) * 1000
        print(f"{p:40} status={r.status_code} queries={STATS['n']:5} local_ms={ms:7.0f} kb={len(r.data) // 1024}")
        for s, n in STATS["sql"].most_common(4):
            if n > 3:
                print(f"     {n:4}x {s}")


if __name__ == "__main__":
    main()
