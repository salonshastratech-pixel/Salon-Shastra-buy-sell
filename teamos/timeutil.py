"""Time helpers. Store UTC ISO-8601; display and compute business days in IST."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
_FMT = "%Y-%m-%dT%H:%M:%SZ"

# Tests may freeze time by setting this.
_frozen: datetime | None = None


def now_utc() -> datetime:
    return _frozen or datetime.now(timezone.utc).replace(microsecond=0)


def now_iso() -> str:
    return to_iso(now_utc())


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(timezone.utc).strftime(_FMT)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, _FMT).replace(tzinfo=timezone.utc)


def ist_day_bounds(day: datetime | None = None) -> tuple[str, str]:
    """UTC ISO bounds [start, end) of the IST calendar day containing `day`."""
    local = (day or now_utc()).astimezone(IST)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return to_iso(start), to_iso(start + timedelta(days=1))


def parse_local_input(value: str | None) -> str | None:
    """Parse an <input type=datetime-local> value entered in IST."""
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return to_iso(datetime.strptime(value, fmt).replace(tzinfo=IST))
        except ValueError:
            continue
    raise ValueError("Invalid date/time")


def fmt_ist(value: str | None, fmt: str = "%d %b %Y, %I:%M %p") -> str:
    dt = parse_iso(value)
    return dt.astimezone(IST).strftime(fmt) if dt else "—"


def relative(value: str | None) -> str:
    dt = parse_iso(value)
    if not dt:
        return "—"
    delta = dt - now_utc()
    secs = int(delta.total_seconds())
    past = secs < 0
    secs = abs(secs)
    if secs < 60:
        text = "now"
        return text
    if secs < 3600:
        text = f"{secs // 60} min"
    elif secs < 86400:
        text = f"{secs // 3600} h"
    else:
        text = f"{secs // 86400} d"
    return f"{text} ago" if past else f"in {text}"
