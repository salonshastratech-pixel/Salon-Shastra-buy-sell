"""Area check for reports: how many salons, beauty parlours, nail studios, barbers and spas are around a place — free,
from OpenStreetMap (Overpass API, no key). OpenStreetMap only knows shops volunteers have mapped, so counts are a
floor ("at least"), never a census; the report says so. The look-up is slow (10–40 s), so it runs in the background
and the result is added to the report draft when it arrives.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request

from ..timeutil import now_iso
from . import geo

SERVERS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
CATEGORIES = [("salon", "Hair & unisex salons"), ("beauty", "Beauty parlours"), ("nails", "Nail studios"),
              ("barber", "Barber / men's shops"), ("spa", "Spas & massage")]
CAT_LABEL = dict(CATEGORIES)
_CACHE: dict[tuple, tuple[float, dict]] = {}
CACHE_SECONDS = 24 * 3600
log = logging.getLogger(__name__)


def _query(lat: float, lng: float, radius_m: int) -> str:
    a = f"(around:{radius_m},{lat:.5f},{lng:.5f})"
    return (f"[out:json][timeout:40];("
            f'nwr["shop"~"^(hairdresser|beauty|barber|massage|nails)$"]{a};'
            f'nwr["leisure"="spa"]{a};nwr["amenity"="spa"]{a};'
            f");out tags center 400;")


def classify(tags: dict) -> str | None:
    shop, beauty, name = tags.get("shop"), (tags.get("beauty") or ""), (tags.get("name") or "").lower()
    if shop == "nails" or "nail" in beauty or "nail" in name:
        return "nails"
    if shop == "barber" or tags.get("hairdresser") in ("barber", "male") or "barber" in name or "men's" in name \
            or "mens salon" in name:
        return "barber"
    if shop in ("massage",) or tags.get("leisure") == "spa" or tags.get("amenity") == "spa" or " spa" in f" {name}":
        return "spa"
    if shop == "beauty" or "parlour" in name or "parlor" in name or "beauty" in name:
        return "beauty"
    if shop == "hairdresser":
        return "salon"
    return None


def fetch(lat: float, lng: float, radius_km: float, opener=None) -> dict:
    """{counts: {cat: n}, total, names: [...]} — raises on network failure (caller handles)."""
    key = (round(lat, 3), round(lng, 3), radius_km)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    q = _query(lat, lng, int(radius_km * 1000))
    last = None
    for url in SERVERS:
        try:
            req = urllib.request.Request(url + "?data=" + urllib.parse.quote(q),
                                         headers={"User-Agent": "SalonShastra-TeamOS/1.0 (area check for reports)"})
            raw = (opener or urllib.request.urlopen)(req, timeout=60).read()
            elements = json.loads(raw.decode("utf-8")).get("elements", [])
            break
        except Exception as e:  # noqa: BLE001 — try the next free server
            last = e
    else:
        raise RuntimeError(f"OpenStreetMap did not answer ({last})")
    counts = {k: 0 for k, _ in CATEGORIES}
    names = []
    for el in elements:
        cat = classify(el.get("tags") or {})
        if cat:
            counts[cat] += 1
            if el["tags"].get("name") and len(names) < 12:
                names.append(el["tags"]["name"])
    out = {"counts": counts, "total": sum(counts.values()), "names": names}
    _CACHE[key] = (time.time(), out)
    return out


def recommendations(d: dict, place: str, radius_km: float, for_investor: bool) -> list[str]:
    c, total = d["counts"], d["total"]
    tips = []
    if total >= 15:
        tips.append(f"Busy market: at least {total} salons and parlours within {radius_km:g} km of {place}. "
                    + ("Choose a clear difference (premium, bridal, men's grooming or a niche) — not lower prices."
                       if for_investor else
                       "Compete on experience, specialist services and repeat customers — avoid price wars."))
    elif total <= 4:
        tips.append(f"Few salons are mapped within {radius_km:g} km of {place} (at least {total}). This can be an "
                    "under-served area — confirm footfall and spending power with a visit before deciding.")
    else:
        tips.append(f"Moderate competition: at least {total} salons and parlours within {radius_km:g} km of {place}.")
    if c["nails"] == 0 and total >= 3:
        tips.append("No nail studio is mapped nearby — nail art and extensions could be a gap to fill.")
    if c["barber"] >= 2 * max(1, c["salon"]) and c["barber"] >= 4:
        tips.append("Many barbers but fewer unisex/family salons — a well-run unisex or family salon may stand out.")
    if c["beauty"] <= 1 and total >= 3:
        tips.append("Few beauty parlours are mapped — women's beauty services (facials, waxing, bridal) look "
                    "under-supplied.")
    if c["spa"] == 0 and total >= 5:
        tips.append("No spa is mapped nearby — massage / spa add-ons could increase the bill size.")
    return tips


def build(place: str, radius_km: float = 2, for_investor: bool = False, opener=None) -> dict | None:
    """The 'Your area' block for a report, or None when the place is not on the built-in area map."""
    pt = geo.coords(place)
    if not pt:
        return None
    d = fetch(pt[0], pt[1], radius_km, opener=opener)
    return {"place": place, "radius_km": radius_km, "counts": d["counts"], "total": d["total"], "names": d["names"],
            "tips": recommendations(d, place, radius_km, for_investor), "checked_at": now_iso(),
            "source": "OpenStreetMap — counts are what volunteers have mapped (at least this many)"}


def attach(db, table: str, report_id: int, place: str, radius_km: float, for_investor: bool) -> str:
    """Background job: look the area up and add it to the report draft (only if the report has no area yet)."""
    try:
        area = build(place, radius_km, for_investor)
    except Exception as e:  # noqa: BLE001 — a report without the area block is still complete
        log.warning("area check failed for %s: %s", place, e)
        return "failed"
    if not area:
        return "unknown place"
    row = None
    for _ in range(10):  # the draft may still be committing when this starts
        row = db.one(f"SELECT content FROM {table} WHERE id = ?", (report_id,))
        if row:
            break
        time.sleep(2)
    if not row:
        return "no report"
    c = json.loads(row["content"])
    c["area"] = area
    db.execute(f"UPDATE {table} SET content = ?, updated_at = ? WHERE id = ?",
               (json.dumps(c, ensure_ascii=False), now_iso(), report_id))
    return "ok"


def schedule(table: str, report_id: int, place: str | None, radius_km: float, for_investor: bool) -> bool:
    """Start the area look-up in the background for a new report draft. Off in tests (no network) unless enabled."""
    from flask import current_app, has_app_context

    from .. import background
    if not place or not has_app_context() or not geo.coords(place):
        return False
    app = current_app._get_current_object()
    if app.config.get("TESTING") and not app.config.get("AREA_CHECK_IN_TESTS"):
        return False
    return background.run(app, f"area:{table}:{report_id}", attach, table, report_id, place, radius_km, for_investor)
