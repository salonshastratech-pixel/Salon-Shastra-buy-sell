"""Run slow housekeeping (re-scoring matches, staleness checks) off the web request, so pages answer at once.

Each run gets its own database connection. A key that is already running is not started twice. Under tests the work
runs immediately (deterministic results).
"""
from __future__ import annotations

import logging
import threading

_RUNNING: set[str] = set()
_LOCK = threading.Lock()


def run(app, key: str, fn, *args, **kwargs) -> bool:
    """Start fn(db, *args, **kwargs) in the background. Returns False if the same key is still running."""
    with _LOCK:
        if key in _RUNNING:
            return False
        _RUNNING.add(key)

    def work():
        from .db import Database
        try:
            with app.app_context():
                db = Database(app.config["DATABASE_URL"])
                try:
                    fn(db, *args, **kwargs)
                finally:
                    db.close()
        except Exception:  # noqa: BLE001 — housekeeping must never break a page
            logging.getLogger(__name__).exception("background task %s failed", key)
        finally:
            with _LOCK:
                _RUNNING.discard(key)

    if app.config.get("TESTING"):
        work()
    else:
        threading.Thread(target=work, name="bg-" + key, daemon=True).start()
    return True
