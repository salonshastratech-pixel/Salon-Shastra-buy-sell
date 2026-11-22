"""Append-only audit trail for sensitive and high-impact actions (PRD §14.1)."""
from __future__ import annotations

import json

from flask import has_request_context, request

from .timeutil import now_iso


def record(db, actor_id, action: str, entity_type: str | None = None, entity_id: int | None = None,
           detail: dict | None = None, outcome: str = "allowed") -> None:
    ip = request.remote_addr if has_request_context() else None
    db.insert("audit_events", {
        "at": now_iso(),
        "actor_id": actor_id,
        "action": action,
        "outcome": outcome,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "detail": json.dumps(detail, default=str) if detail else None,
        "ip": ip,
    })
