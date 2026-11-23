"""Database-backed job queue and worker.

* enqueue() is idempotent via dedupe_key.
* Claiming uses a conditional UPDATE so two workers never run the same job.
* Failures retry with exponential back-off (1, 2, 4, 8 min); after MAX_ATTEMPTS the job is 'dead'
  and visible in Admin → Background jobs. Nothing fails silently.
* The worker schedules the SLA sweep every 15 minutes (M5 adds re-scoring, M7 adds message sends).
"""
from __future__ import annotations

import json
import logging
import time
import traceback
from datetime import timedelta

from .domain import tasks as T
from .timeutil import now_iso, now_utc, to_iso

log = logging.getLogger("teamos.jobs")
MAX_ATTEMPTS = 5
ESCALATE_AFTER_HOURS = 24
HANDLERS = {}


def handler(kind):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def enqueue(db, kind: str, payload: dict | None = None, dedupe_key: str | None = None,
            run_after: str | None = None) -> int | None:
    if dedupe_key and db.scalar("SELECT id FROM jobs WHERE dedupe_key = ?", (dedupe_key,)):
        return None
    now = now_iso()
    return db.insert("jobs", {"kind": kind, "payload": json.dumps(payload or {}), "status": "queued",
                              "dedupe_key": dedupe_key, "run_after": run_after or now, "attempts": 0,
                              "created_at": now, "updated_at": now})


def schedule_periodic(db) -> None:
    n = now_utc()
    bucket = n.replace(minute=(n.minute // 15) * 15, second=0)
    enqueue(db, "sla_sweep", dedupe_key="sla_sweep:" + bucket.strftime("%Y%m%d%H%M"))
    enqueue(db, "form_chase", dedupe_key="form_chase:" + n.strftime("%Y%m%d%H"))  # hourly
    enqueue(db, "match_refresh", dedupe_key="match_refresh:" + bucket.strftime("%Y%m%d%H%M"))  # every 15 min
    enqueue(db, "webhook_retry", dedupe_key="webhook_retry:" + bucket.strftime("%Y%m%d%H%M"))  # every 15 min
    enqueue(db, "daily_digest", dedupe_key="daily_digest:" + n.strftime("%Y%m%d%H"))  # hourly; sends once/user/day
    enqueue(db, "stuck_sweep", dedupe_key="stuck_sweep:" + n.strftime("%Y%m%d"))  # daily; one alert per person
    enqueue(db, "learning_propose", dedupe_key="learning_propose:" + n.strftime("%G%V"))  # weekly


def run_once(db, limit: int = 20) -> int:
    """Run due jobs. Returns how many were processed."""
    processed = 0
    for job in db.all("SELECT * FROM jobs WHERE status = 'queued' AND run_after <= ? ORDER BY run_after, id LIMIT ?",
                      (now_iso(), limit)):
        claimed = db.execute("UPDATE jobs SET status = 'running', attempts = attempts + 1, updated_at = ? "
                             "WHERE id = ? AND status = 'queued'", (now_iso(), job["id"])).rowcount
        if not claimed:
            continue
        run_id = db.insert("job_runs", {"job_id": job["id"], "started_at": now_iso()})
        attempts = job["attempts"] + 1
        try:
            fn = HANDLERS.get(job["kind"])
            if fn is None:
                raise RuntimeError(f"No handler for job kind '{job['kind']}'")
            with db:
                message = fn(db, json.loads(job["payload"] or "{}")) or ""
            db.execute("UPDATE jobs SET status = 'done', last_error = NULL, updated_at = ? WHERE id = ?",
                       (now_iso(), job["id"]))
            db.execute("UPDATE job_runs SET finished_at = ?, outcome = 'done', message = ? WHERE id = ?",
                       (now_iso(), str(message)[:500], run_id))
            log.info("job done", extra={"job_id": job["id"], "kind": job["kind"]})
        except Exception as exc:  # noqa: BLE001 — every failure is recorded and retried
            err = f"{type(exc).__name__}: {exc}"
            dead = attempts >= MAX_ATTEMPTS
            retry_at = to_iso(now_utc() + timedelta(minutes=2 ** (attempts - 1)))
            db.execute("UPDATE jobs SET status = ?, last_error = ?, run_after = ?, updated_at = ? WHERE id = ?",
                       ("dead" if dead else "queued", err[:1000], retry_at, now_iso(), job["id"]))
            db.execute("UPDATE job_runs SET finished_at = ?, outcome = 'error', message = ? WHERE id = ?",
                       (now_iso(), traceback.format_exc()[-1500:], run_id))
            log.error("job failed", extra={"job_id": job["id"], "kind": job["kind"], "error": err, "dead": dead})
        processed += 1
    return processed


def worker_loop(db, interval: int = 30) -> None:  # pragma: no cover — exercised via run_once in tests
    log.info("worker started")
    while True:
        schedule_periodic(db)
        run_once(db)
        time.sleep(interval)


# -- handlers ------------------------------------------------------------------------
@handler("sla_sweep")
def sla_sweep(db, payload) -> str:
    """Alert assignees about overdue tasks once; escalate to team leads after 24 h overdue — once."""
    now = now_iso()
    alerted = escalated = 0
    leads = db.all("SELECT id FROM users WHERE is_active = 1 AND role IN ('team_lead', 'super_admin')")
    overdue = db.all("SELECT t.*, u.full_name AS assignee_name FROM tasks t JOIN users u ON u.id = t.assignee_id "
                     "WHERE t.status = 'open' AND t.due_at IS NOT NULL AND t.due_at < ?", (now,))
    link = lambda t: f"/tasks/{t['id']}"  # noqa: E731
    for t in overdue:
        events = {e["event"] for e in db.all("SELECT event FROM task_events WHERE task_id = ?", (t["id"],))}
        if "overdue_alerted" not in events:
            T.notify(db, t["assignee_id"], "overdue", f"Overdue: {t['title']}", link(t))
            db.insert("task_events", {"task_id": t["id"], "event": "overdue_alerted", "at": now})
            alerted += 1
        limit = to_iso(now_utc() - timedelta(hours=ESCALATE_AFTER_HOURS))
        if t["due_at"] < limit and "escalated" not in events:
            for lead in leads:
                if lead["id"] != t["assignee_id"]:
                    T.notify(db, lead["id"], "escalation",
                             f"Escalation: '{t['title']}' ({t['assignee_name']}) is over 24 h overdue", link(t))
            db.insert("task_events", {"task_id": t["id"], "event": "escalated", "at": now})
            escalated += 1
    return f"{alerted} overdue alerts, {escalated} escalations"


@handler("form_chase")
def form_chase(db, payload) -> str:
    """Chase personal form links not completed after 48 h (one task each) and expire old links (PRD §10.4)."""
    from .domain import forms
    return forms.chase_and_expire(db)


@handler("match_refresh")
def match_refresh(db, payload) -> str:
    """Incremental re-evaluation: only buyer/salon pairs whose inputs changed are recomputed (PRD §8.6)."""
    from .domain import matching
    st = matching.refresh(db)
    return f"evaluated {st['evaluated']} ({st['new']} new), unchanged {st['unchanged']}"


@handler("webhook_retry")
def webhook_retry(db, payload) -> str:
    from .domain import inbox
    return inbox.retry_failed_events(db)


@handler("daily_digest")
def daily_digest(db, payload) -> str:
    from .domain import digests
    if not digests.due_now():
        return "not yet 08:00 IST"
    return digests.send_all(db)


@handler("stuck_sweep")
def stuck_sweep(db, payload) -> str:
    """Records that have not moved for longer than their stage usually takes (M8)."""
    from .domain import progress
    return progress.stuck_sweep(db)


@handler("learning_propose")
def learning_propose(db, payload) -> str:
    """Weekly: propose matching-weight changes from declined matches (the founder approves; M8)."""
    from .domain import learning
    made = learning.propose(db)
    if made:
        from .domain import tasks as T
        for a in db.all("SELECT id FROM users WHERE is_active = 1 AND role = 'super_admin'"):
            T.notify(db, a["id"], "learning", f"{len(made)} suggestion(s) to improve match scoring — please review",
                     "/reports/learning")
    return f"{len(made)} proposals"


@handler("lead_ads")
def lead_ads(db, payload) -> str:
    """New Facebook / Instagram lead ads → smart import (M9). Retried by the job runner if it fails."""
    from .domain import leadads
    done = [leadads.process(db, i)["status"] for i in payload.get("ids") or []]
    return f"{len(done)} lead(s): " + ", ".join(done)


@handler("noop")
def noop(db, payload) -> str:
    if payload.get("fail"):
        raise RuntimeError("requested failure")
    return "ok"
