import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from teamos import create_app  # noqa: E402
from teamos.db import open_standalone  # noqa: E402
from teamos.security import hash_password  # noqa: E402
from teamos.timeutil import now_iso  # noqa: E402

PW = "Test-Password-1"


@pytest.fixture
def mfa_roles():
    return ""  # MFA is exercised in test_mfa.py, which overrides this fixture


@pytest.fixture
def app(tmp_path, mfa_roles):
    url = f"sqlite:///{tmp_path}/test.db"
    app = create_app({"DATABASE_URL": url, "COOKIE_SECURE": False, "TESTING": True, "SECRET_KEY": "test",
                      "MFA_REQUIRED_ROLES": mfa_roles, "UPLOAD_DIR": str(tmp_path / "uploads"),
                      "FILES_KEY": "Wz3Qt1tVYv0d6m1M4mQe5Gk0b8Yq3c2y7r9s1u4x6zA="})
    db = open_standalone(url)
    for email, name, role, team in [("admin@t.test", "Asha Admin", "super_admin", "admin"),
                                    ("seller@t.test", "Shaheen Seller", "team_member", "seller"),
                                    ("buyer@t.test", "Harika Buyer", "team_member", "buyer"),
                                    ("lead@t.test", "Ravi Lead", "team_lead", "general"),
                                    ("fin@t.test", "Fiona Finance", "finance", "finance")]:
        db.insert("users", {"email": email, "full_name": name, "role": role, "team": team, "is_active": 1,
                            "password_hash": hash_password(PW), "created_at": now_iso()})
    db.close()
    app.test_db_url = url
    return app


@pytest.fixture
def db(app):
    d = open_standalone(app.test_db_url)
    yield d
    d.close()


def user(db, email):
    return db.one("SELECT * FROM users WHERE email = ?", (email,))


class Client:
    """Test client that logs in and attaches the CSRF token automatically."""

    def __init__(self, app, email):
        self.c = app.test_client()
        r = self.c.post("/login", data={"email": email, "password": PW})
        assert r.status_code == 302, r.data
        self.app = app
        d = open_standalone(app.test_db_url)
        self.csrf = d.scalar("SELECT csrf_token FROM sessions s JOIN users u ON u.id = s.user_id WHERE u.email = ? "
                             "ORDER BY s.id DESC LIMIT 1", (email,))
        d.close()

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, data=None, csrf=True, **kw):
        data = dict(data or {})
        if csrf:
            data["csrf_token"] = self.csrf
        return self.c.post(url, data=data, **kw)


@pytest.fixture
def login(app):
    return lambda email: Client(app, email)


SELLER_FORM = {"full_name": "Test Owner", "phone": "98765 43210", "salon_name": "Test Salon", "city": "Hyderabad",
               "locality": "Kondapur", "source": "manual_call", "consent_contact": "1",
               "operating_status": "operating", "ownership_role": "owner", "sale_purpose": "sell_entire",
               "asking_price": "20L", "sale_timeline": "within_3m"}
