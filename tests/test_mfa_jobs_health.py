"""Two-step sign-in for privileged roles, job queue, SLA sweep, health check."""
import time
from datetime import timedelta

import pytest
from conftest import PW, SELLER_FORM, user

from teamos import jobs, mfa
from teamos.timeutil import now_utc, to_iso


class TestMFA:
    @pytest.fixture
    def mfa_roles(self):
        return "super_admin,finance"

    def _login(self, app, email):
        from conftest import Client
        return Client(app, email)

    def test_admin_must_enrol_before_anything_else(self, app, db):
        c = self._login(app, "admin@t.test")
        r = c.get("/admin/users")
        assert r.status_code == 302 and r.headers["Location"].endswith("/mfa/setup")
        assert c.get("/sellers/").status_code == 302
        # crafted POST also blocked
        assert c.post("/admin/users", {"email": "x@y.z", "full_name": "X", "role": "super_admin",
                                       "password": "Abcdefghij1"}).status_code == 302
        assert db.scalar("SELECT COUNT(*) FROM users WHERE email = 'x@y.z'") == 0

    def test_enrol_verify_and_codes(self, app, db):
        c = self._login(app, "admin@t.test")
        page = c.get("/mfa/setup").data.decode()
        secret = db.scalar("SELECT mfa_secret FROM users WHERE email = 'admin@t.test'")
        assert secret.replace(" ", "")[:4] in page.replace(" ", "")
        assert c.post("/mfa/setup", {"code": "000000"}).status_code == 200
        assert db.scalar("SELECT mfa_enabled_at FROM users WHERE email = 'admin@t.test'") is None
        r = c.post("/mfa/setup", {"code": mfa.current_code(secret)})
        assert b"recovery codes" in r.data
        assert db.scalar("SELECT COUNT(*) FROM mfa_recovery_codes WHERE used_at IS NULL") == 8
        assert c.get("/admin/users").status_code == 200

        # New session: password alone is not enough
        c2 = self._login(app, "admin@t.test")
        assert c2.get("/admin/users").headers["Location"].endswith("/mfa")
        # replaying the code already used is refused
        assert c2.post("/mfa", {"code": mfa.current_code(secret)}).status_code == 401
        # next time-step code works
        assert c2.post("/mfa", {"code": mfa.current_code(secret, time.time() + 30)}).status_code == 302
        assert c2.get("/admin/users").status_code == 200

    def test_recovery_code_single_use(self, app, db):
        c = self._login(app, "admin@t.test")
        c.get("/mfa/setup")
        secret = db.scalar("SELECT mfa_secret FROM users WHERE email = 'admin@t.test'")
        page = c.post("/mfa/setup", {"code": mfa.current_code(secret)}).data.decode()
        import re
        code = re.findall(r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}", page)[0]
        c2 = self._login(app, "admin@t.test")
        assert c2.post("/mfa", {"code": code}).status_code == 302
        c3 = self._login(app, "admin@t.test")
        assert c3.post("/mfa", {"code": code}).status_code == 401

    def test_repeated_wrong_codes_end_the_session(self, app, db):
        c = self._login(app, "admin@t.test")
        c.get("/mfa/setup")
        secret = db.scalar("SELECT mfa_secret FROM users WHERE email = 'admin@t.test'")
        c.post("/mfa/setup", {"code": mfa.current_code(secret)})
        c2 = self._login(app, "admin@t.test")
        for _ in range(5):
            c2.post("/mfa", {"code": "111111"})
        r = c2.get("/")
        assert r.status_code == 302 and "/login" in r.headers["Location"]

    def test_team_members_not_forced(self, app):
        assert self._login(app, "seller@t.test").get("/sellers/").status_code == 200

    def test_finance_role_requires_mfa(self, app):
        assert self._login(app, "fin@t.test").get("/admin/finance").status_code == 302


def test_admin_can_reset_someone_elses_mfa(login, db):
    uid = user(db, "fin@t.test")["id"]
    db.execute("UPDATE users SET mfa_secret = 'ABC', mfa_enabled_at = '2026-01-01T00:00:00Z' WHERE id = ?", (uid,))
    login("admin@t.test").post(f"/admin/users/{uid}/reset-mfa")
    assert db.scalar("SELECT mfa_enabled_at FROM users WHERE id = ?", (uid,)) is None
    assert db.scalar("SELECT COUNT(*) FROM audit_events WHERE action = 'user.mfa_reset'") == 1


def test_job_queue_dedupe_retry_and_dead_letter(db):
    assert jobs.enqueue(db, "noop", dedupe_key="k1")
    assert jobs.enqueue(db, "noop", dedupe_key="k1") is None
    bad = jobs.enqueue(db, "noop", {"fail": True})
    jobs.run_once(db)
    assert db.scalar("SELECT status FROM jobs WHERE dedupe_key = 'k1'") == "done"
    assert db.scalar("SELECT status FROM jobs WHERE id = ?", (bad,)) == "queued"   # retry scheduled
    for _ in range(jobs.MAX_ATTEMPTS):
        db.execute("UPDATE jobs SET run_after = '2000-01-01T00:00:00Z' WHERE id = ?", (bad,))
        jobs.run_once(db)
    row = db.one("SELECT * FROM jobs WHERE id = ?", (bad,))
    assert row["status"] == "dead" and "requested failure" in row["last_error"]
    assert db.scalar("SELECT COUNT(*) FROM job_runs WHERE job_id = ? AND outcome = 'error'", (bad,)) == jobs.MAX_ATTEMPTS


def test_sla_sweep_alerts_once_and_escalates_after_24h(login, db):
    login("seller@t.test").post("/sellers/new", SELLER_FORM)
    tid = db.scalar("SELECT id FROM tasks")
    db.execute("UPDATE tasks SET due_at = ? WHERE id = ?", (to_iso(now_utc() - timedelta(hours=30)), tid))
    db.execute("DELETE FROM notifications")
    jobs.schedule_periodic(db)
    jobs.schedule_periodic(db)             # same 15-min bucket → not duplicated
    assert db.scalar("SELECT COUNT(*) FROM jobs WHERE kind = 'sla_sweep'") == 1
    jobs.run_once(db)
    seller_id = user(db, "seller@t.test")["id"]
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE kind = 'overdue' AND user_id = ?", (seller_id,)) == 1
    assert db.scalar("SELECT COUNT(*) FROM notifications WHERE kind = 'escalation'") == 2   # lead + admin
    jobs.enqueue(db, "sla_sweep")
    jobs.run_once(db)                      # second sweep: no repeat alerts
    assert db.scalar("SELECT COUNT(*) FROM notifications") == 3


def test_healthz_reports_without_login(app):
    r = app.test_client().get("/healthz")
    assert r.status_code == 200 and r.json["status"] == "ok" and r.json["db"] == "ok"
    assert set(r.json) == {"status", "db", "schema", "jobs_dead", "jobs_backlog_30m"}


def test_uat26_sqlite_backup_restores_and_detects_tampering(app, tmp_path, monkeypatch):
    from teamos.backup import backup_sqlite, verify_sqlite_backup
    from cryptography.fernet import Fernet
    monkeypatch.setenv("BACKUP_KEY", Fernet.generate_key().decode())
    m = backup_sqlite(app.test_db_url[len("sqlite:///"):], str(tmp_path / "bk"))
    f = tmp_path / "bk" / m["file"]
    assert m["encrypted"] and f.name.endswith(".enc")
    ok, report = verify_sqlite_backup(str(f))
    assert ok, report
    f.write_bytes(f.read_bytes() + b"x")
    ok, report = verify_sqlite_backup(str(f))
    assert not ok and "Checksum mismatch" in report[0]
