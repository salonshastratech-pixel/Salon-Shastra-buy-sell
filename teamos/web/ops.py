"""Operations endpoint for free hosting (Render): an outside scheduler (e.g. cron-job.org) opens
/internal/tick?token=… every few minutes. That keeps the free server awake during office hours and runs the
background jobs (overdue alerts, form chasers, match refresh, digests) that a separate worker would run on a paid
server. Protected by the CRON_TOKEN environment variable; without it the endpoint does nothing."""
import hmac
import os
import time

from flask import Blueprint, current_app, jsonify, request

from ..db import get_db

bp = Blueprint("ops", __name__)


@bp.route("/internal/tick", methods=["GET", "POST"])
def tick():
    expected = os.environ.get("CRON_TOKEN") or ""
    given = request.args.get("token") or request.headers.get("X-Cron-Token") or ""
    # Render's generated token can contain '+', which a web address turns into a space when not encoded as %2B
    given = given.replace(" ", "+")
    if len(expected) < 16 or not hmac.compare_digest(given.encode(), expected.encode()):
        return jsonify({"ok": False}), 404  # looks like any unknown page to strangers
    from .. import background, jobs
    db = get_db()
    started = time.monotonic()
    jobs.schedule_periodic(db)
    # The jobs (sheet sync, alerts, chasers…) run after this reply, on their own connection — never holding a web
    # worker for tens of seconds. A tick that arrives while the previous one is still working simply skips.
    started_jobs = background.run(current_app._get_current_object(), "tick_jobs",
                                  lambda bdb: jobs.run_once(bdb, limit=30))
    return jsonify({"ok": True, "jobs_started": started_jobs, "ms": int((time.monotonic() - started) * 1000)})
