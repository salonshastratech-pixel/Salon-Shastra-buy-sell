"""Reads the team's call remarks — in English, Telugu script, or Telugu written in English letters ("Tenglish") —
and turns them into facts TeamOS can act on.

    "busy ga unnaru, repu evening call cheyandi"   → busy; call back tomorrow 6 pm
    "staff problem, vikas anna tho matladali"      → needs staff hiring; wants to talk to Vikas
    "salon ammali anukuntunnaru 15L ki"            → wants to sell; asking ₹15 lakh
    "lift cheyaledu"                               → no answer
    "interest ledu"                                → not interested

Nothing is invented: every conclusion lists the words it came from, and the original remark is always kept.
The team can teach TeamOS new phrases (Tools & admin → Remark dictionary); taught phrases are used on every import
after that.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .. import audit
from ..permissions import user_can
from ..timeutil import IST, now_iso, now_utc, to_iso
from .errors import PermissionDenied, ValidationError

# Meanings TeamOS can act on: key → (plain English label, group)
MEANINGS = {
    "no_answer": ("Didn't answer", "outcome"),
    "busy": ("Busy — call again", "outcome"),
    "switched_off": ("Switched off / not reachable", "outcome"),
    "callback": ("Asked us to call back", "outcome"),
    "they_will_call": ("Said they will call us", "outcome"),
    "connected": ("Spoke with them", "outcome"),
    "not_interested": ("Not interested", "outcome"),
    "wrong_number": ("Wrong number", "outcome"),
    "interested": ("Interested", "interest"),
    "thinking": ("Still thinking / will decide", "interest"),
    "send_details": ("Wants details sent", "action"),
    "form_sent": ("Form sent to them", "action"),
    "form_filled": ("They filled the form", "action"),
    "office_visit": ("Will visit the office", "action"),
    "talk_vikas": ("Wants to talk to Vikas", "need"),
    "need_staff": ("Needs staff", "need"),
    "need_training": ("Needs staff training", "need"),
    "need_marketing": ("Needs marketing / more customers", "need"),
    "need_software": ("Needs billing software (ChairPe)", "need"),
    "need_consulting": ("Business is struggling — needs consulting", "need"),
    "need_new_salon": ("Wants to open a new salon", "need"),
    "wants_sell": ("Wants to sell the salon", "need"),
    "wants_buy": ("Wants to buy a salon", "need"),
    "money_loan": ("Money from a loan", "money"),
    "money_savings": ("Money from own savings", "money"),
    "no_money": ("Money is a problem now", "money"),
}

# Built-in phrases: (regex, meaning). English, Tenglish (Telugu in English letters, with common spellings) and Telugu.
_B = r"(?:^|[^\w])"           # word start that also works next to Telugu letters
PHRASES: list[tuple[str, str]] = [
    # outcome
    (r"not\s*(lift|pick)\w*|no\s*(response|answer|reply)|did\s*n.?t\s*(pick|lift|answer)|not\s*answer\w*|"
     r"lift\s*(cheya|chey|chesthaled|chestaled|cheyya)\w*|ett?a?l(e|a)du|ethaledu|ettaledu|ring\s*avuth?undi|"
     r"ring\s*ayindi|respond\s*(avva|kaa|ka)\w*|res?pon\w*\s*(ledu|ivvaledu)|రిస్పాన్స్ లేదు|ఎత్తలేదు|"
     r"స్పందన లేదు", "no_answer"),
    (r"\bbusy\b|bijee|biji|బిజీ|meeting\s*lo|driving|drive\s*chest\w*|pani\s*lo\s*unn\w*|పనిలో", "busy"),
    (r"switch(ed)?\s*off|not\s*reachable|out\s*of\s*(coverage|reach)|reach\s*(avvaledu|kaledu|avvatledu)|"
     r"స్విచ్ ఆఫ్", "switched_off"),
    (r"call\s*back|callback|call\s*later|malli\s*call|marala\s*call|tarv?atha?\s*call|taruvatha?\s*call|"
     r"later\s*call|call\s*(cheyyandi|cheyandi|cheyamannaru|cheyyamannaru|cheyamani|chey\s*ani)|"
     r"call\s*after|after\s*\d+\s*(pm|am|mins?|minutes|hours?)|in\s*\d+\s*mins?|half\s*an?\s*hour|"
     r"ara\s*ganta|తర్వాత\s*కాల్|మళ్ళీ\s*కాల్|కాల్ చేయండి", "callback"),
    (r"(he|she|they)\s*will\s*call|will\s*call\s*(back|us|you)?\s*(later)?$|call\s*chest(anu|aru|amani|anannaru)|"
     r"call\s*chesthanu|nene\s*call|vaalle\s*call|when\s*(she|he)\s*is\s*free", "they_will_call"),
    (r"\bspoke\b|\bspoken\b|discussed|explained|talked|matlad(anu|aamu|amu|aru|indi|ina)|"
     r"maatlad\w*|cheppanu|cheppamu|మాట్లాడ", "connected"),
    (r"not\s*(in?te?re?ste?d|intrested|interested)|no\s*interest|not\s*looking|interest\s*(ledu|leru|leedu)|"
     r"avasaram\s*(ledu|ledhu)|vaddu|vaddhu|vadhu|వద్దు|అవసరం లేదు|ఆసక్తి లేదు|don'?t\s*want", "not_interested"),
    (r"wrong\s*(number|no)|not\s*(the|his|her)\s*number|ఆ నంబర్ కాదు|తప్పు నంబర్", "wrong_number"),
    # interest
    (r"(?<!not )(?<!no )\binterested\b|interest\s*(undi|unnadi|chupincharu)|ready\s*(ga|unn\w*)?|ok\s*annaru|"
     r"sare\s*annaru|sari\s*annaru|chesth?anu\s*annaru|positive|keen|pilot\s*program|ఆసక్తి ఉంది|సరే",
     "interested"),
    (r"think\w*|aalochi\w*|alochi\w*|decide\s*chest\w*|will\s*decide|discuss\s*(with|chesi)|family\s*tho\s*"
     r"(matladi|discuss)|ఆలోచి", "thinking"),
    # actions
    (r"(send|share)\s*(the\s*)?(details|info\w*|brochure|form|link)|details\s*(pampandi|pampa|pamp\w*|kavali)|"
     r"vivaralu\s*pamp\w*|వివరాలు పంప", "send_details"),
    (r"form\s*(sent|shared|pampanu|pampamu|pampinchanu)|sent\s*(the\s*)?form|link\s*(sent|pampanu)", "form_sent"),
    (r"form\s*(filled|fill\s*chesar\w*|fill\s*chesindi|complete\w*)|filled\s*(the\s*)?form", "form_filled"),
    (r"(come|coming|visit\w*)\s*(to\s*)?(the\s*)?office|office\s*(ki|ku)\s*(vast|vost|ost|vach|ra)\w*|"
     r"office\s*visit|ఆఫీస్ కి", "office_visit"),
    # needs
    (r"vikas\s*(anna|sir|garu|gaaru)?\s*(tho|to|with)?\s*(matlad\w*|talk|speak|conference|call|meet)|"
     r"(talk|speak)\s*(to|with)\s*vikas|vikas\s*(anna|sir|garu)\s*(in\s*)?(conference|call)|"
     r"take\s*vikas|vikas\s*conference|వికాస్", "talk_vikas"),
    (r"staff\s*(problem|issue|shortage|kavali|ledu|leru|dorak\w*|dhorak\w*|levu|not\s*available|leaving|"
     r"vellipo\w*|requirement|needed|hiring)|need\s*staff|no\s*staff|perfect\s*staff|hiring|స్టాఫ్", "need_staff"),
    (r"training|train\s*(cheya|staff)|trainin\w*|శిక్షణ", "need_training"),
    (r"marketing|promotion|instagram|insta\b|facebook|google\s*(ads|review|ranking)|\bads\b|customers?\s*"
     r"(ravatledu|ravadam\s*ledu|ravaledu|takkuva|less|low)|walk\s*-?ins?\s*(less|low|takkuva)|"
     r"మార్కెటింగ్", "need_marketing"),
    (r"software|billing|chairpe|chair\s*pe|pos\b|appointment\s*(app|system)", "need_software"),
    (r"loss\s*(lo|lone|undi|running)|nashtam|nastam|profit\s*(ledu|raledu|takkuva|less)|sales\s*(down|takkuva|"
     r"less|low|padipoy\w*)|business\s*(improve|grow\w*|down|slow)|struggl\w*|నష్టం", "need_consulting"),
    (r"(new|kotha|kottha)\s*salon|open\s*(a\s*)?(new\s*)?salon|salon\s*(open|start)\s*(cheya|chey|chestam|"
     r"cheyyali|cheyali)|కొత్త సెలూన్", "need_new_salon"),
    (r"(want|wants|planning|plan)\s*to\s*sell|sell\s*(the|my|his|her)?\s*salon|salon\s*(for\s*)?sale|"
     r"amm(ali|aali|esta\w*|eyali|eyyali|uthunn\w*|utunn\w*)|ammak\w*|sell\s*chest\w*|exit|decided\s*to\s*sale|"
     r"అమ్మా|అమ్మే", "wants_sell"),
    (r"(want|wants|looking)\s*to\s*buy|buy\s*(a\s*)?salon|konali|konalani|konu\w*|కొనా", "wants_buy"),
    # money
    (r"\bloan\b|emi|finance|అప్పు|loan\s*(teesk|tisk)\w*|appu", "money_loan"),
    (r"savings|own\s*money|sontha\s*(dabbu|money)|సొంత డబ్బు", "money_savings"),
    (r"no\s*(money|funds|backup)|dabbu\s*(ledu|levu)|money\s*(problem|issue)|financial\s*(problem|issue)|"
     r"డబ్బు లేదు", "no_money"),
]
_COMPILED = [(re.compile(p, re.I), m) for p, m in PHRASES]

DAYS = {"monday": 0, "mon": 0, "somavaram": 0, "tuesday": 1, "tue": 1, "mangalavaram": 1, "wednesday": 2, "wed": 2,
        "budhavaram": 2, "thursday": 3, "thu": 3, "guruvaram": 3, "friday": 4, "fri": 4, "sukravaram": 4,
        "shukravaram": 4, "saturday": 5, "sat": 5, "shanivaram": 5, "sanivaram": 5, "sunday": 6, "sun": 6,
        "aadivaram": 6, "adivaram": 6}
TELUGU_SCRIPT = re.compile(r"[ఀ-౿]")
TENGLISH_WORDS = re.compile(r"\b(ga|lo|ki|ku|tho|ledu|leru|undi|unnaru|cheyandi|cheyyandi|annaru|chesaru|"
                            r"matlad\w*|repu|tarvatha|taruvatha|malli|vaddu|ammali|kavali|pampandi|anna|garu|"
                            r"anukuntunnaru|chestanu|chesthanu|sayantram|podduna|ippudu|ivala|eeroju|"
                            r"\w+(aledu|aleru|andi|unnaru|annaru|chesaru|tunnaru|tledu|kavali)|cheyy?ali|"
                            r"vellali|raavali|ravali)\b", re.I)


def _learned(db) -> list[tuple[re.Pattern, str, str]]:
    if db is None:
        return []
    out = []
    for r in db.all("SELECT phrase, meaning FROM remark_phrases WHERE active = 1"):
        if r["meaning"] in MEANINGS:
            out.append((re.compile(r"(?<!\w)" + re.escape(r["phrase"].strip().lower()) + r"(?!\w)", re.I),
                        r["meaning"], r["phrase"]))
    return out


def language(text: str) -> str:
    if TELUGU_SCRIPT.search(text):
        return "Telugu" if not re.search(r"[A-Za-z]{3,}", text) else "Telugu + English"
    return "Telugu in English letters" if TENGLISH_WORDS.search(text) else "English"


def _when(text: str, base: datetime) -> tuple[datetime | None, str | None]:
    """A callback time from words like 'repu evening', 'monday', 'after 5pm', 'in 10 mins', 'next week'."""
    t = text.lower()
    local = base.astimezone(IST)
    day, why = None, None
    if re.search(r"\b(today|ivala|eeroju|ee\s*roju|ippudu)\b|ఈ రోజు", t):
        day, why = local.date(), "today"
    if re.search(r"\b(tomorrow|tmrw|tmr|repu|reppu)\b|రేపు", t):
        day, why = (local + timedelta(days=1)).date(), "tomorrow"
    if re.search(r"day\s*after\s*tomorrow|ellundi", t):
        day, why = (local + timedelta(days=2)).date(), "day after tomorrow"
    if re.search(r"next\s*week|vachh?e\s*vaaram|vache\s*varam|వచ్చే వారం", t):
        day, why = (local + timedelta(days=7 - local.weekday())).date(), "next week"
    for w, wd in DAYS.items():
        if re.search(r"\b" + w + r"\b", t):
            ahead = (wd - local.weekday()) % 7 or 7
            day, why = (local + timedelta(days=ahead)).date(), w.capitalize()
            break
    hour, minute = None, 0
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t)
    if m:
        hour = int(m.group(1)) % 12 + (12 if m.group(3) == "pm" else 0)
        minute = int(m.group(2) or 0)
    elif re.search(r"evening|sayantram|saayantram|sayankalam|సాయంత్రం", t):
        hour = 18
    elif re.search(r"morning|podduna|poddhuna|ఉదయం|పొద్దున", t):
        hour = 10
    elif re.search(r"afternoon|madhyahnam|lunch\s*tarvatha", t):
        hour = 15
    elif re.search(r"night|raatri|రాత్రి", t):
        hour = 20
    m = re.search(r"after\s*(\d{1,2})\s*(?!am|pm|min|hour)", t) or re.search(r"(\d{1,2})\s*(tarvatha|taruvatha|"
                                                                            r"tarvata)", t)
    if hour is None and m:
        h = int(m.group(1))
        hour = h + 12 if 1 <= h <= 8 else h
    mins = re.search(r"(\d{1,3})\s*(mins?|minutes|nimishal\w*)", t)
    if mins and day is None:
        return local + timedelta(minutes=int(mins.group(1))), f"in {mins.group(1)} minutes"
    if re.search(r"half\s*an?\s*hour|ara\s*ganta|అరగంట", t) and day is None:
        return local + timedelta(minutes=30), "in half an hour"
    hrs = re.search(r"(\d)\s*(hours?|gantal\w*)", t)
    if hrs and day is None and hour is None:
        return local + timedelta(hours=int(hrs.group(1))), f"in {hrs.group(1)} hours"
    if day is None and hour is None:
        return None, None
    if day is None:
        day = local.date() if hour > local.hour else (local + timedelta(days=1)).date()
        why = "today" if day == local.date() else "tomorrow"
    hour = 10 if hour is None else hour
    hour = max(9, min(20, hour))
    when = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
    return when, f"{why} {when.strftime('%I:%M %p').lstrip('0').lower()}"


def _amount(text: str) -> int | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(l|lakh|lakhs|lac|lacs|k|cr|crore)\b", text, re.I)
    if not m:
        return None
    n, u = float(m.group(1)), m.group(2).lower()
    n *= 1000 if u == "k" else 10000000 if u.startswith("cr") else 100000
    return int(n)


def read(text: str, db=None, now: datetime | None = None, learned_cache: list | None = None) -> dict:
    """Understand one remark (or several joined). Returns meanings with the words they came from, a callback time,
    an amount if mentioned, the language, and a one-line summary in plain English."""
    raw = (text or "").strip()
    t = re.sub(r"\s+", " ", raw)
    found: dict[str, list[str]] = {}
    for rx, meaning, phrase in (learned_cache if learned_cache is not None else _learned(db)):
        if rx.search(t):
            found.setdefault(meaning, []).append(phrase)
    for rx, meaning in _COMPILED:
        m = rx.search(t)
        if m:
            found.setdefault(meaning, []).append(m.group(0).strip())
    # an explicit "not interested" outranks a bare "interested"; "will call" outranks "call back"
    if "not_interested" in found:
        found.pop("interested", None)
    if "wrong_number" in found:
        for k in ("interested", "callback", "connected"):
            found.pop(k, None)
    when, when_text = _when(t, now or now_utc())
    if when and not ({"callback", "they_will_call", "busy", "office_visit"} & found.keys()):
        found["callback"] = [when_text]  # "monday 11am" on its own means: call then
    outcome = next((k for k in ("wrong_number", "not_interested", "switched_off", "no_answer", "busy", "they_will_call",
                                "callback", "connected") if k in found), None)
    if outcome in ("busy", "callback", "they_will_call") and ({"connected", "interested"} & found.keys()):
        pass
    needs = [k for k in found if MEANINGS[k][1] == "need"]
    interest = ("cold" if "not_interested" in found else "hot" if "interested" in found or "office_visit" in found
                or "form_filled" in found else "warm" if "thinking" in found or "talk_vikas" in found else None)
    amount = _amount(t)
    parts = []
    if outcome:
        parts.append(MEANINGS[outcome][0])
    if when_text and outcome in ("callback", "busy", "they_will_call", "no_answer", None) or "office_visit" in found:
        parts.append(("call back " if "office_visit" not in found else "visit ") + when_text if when_text else "")
    parts += [MEANINGS[k][0] for k in found if MEANINGS[k][1] in ("need", "action", "money") and k != outcome]
    if "thinking" in found:
        parts.append(MEANINGS["thinking"][0])
    elif interest and "interested" not in found and interest != "cold":
        parts.append({"hot": "Interested", "warm": "Warm lead"}[interest])
    elif "interested" in found:
        parts.append("Interested")
    if amount:
        parts.append(f"mentions ₹{amount / 1e5:.1f} lakh" if amount >= 1e5 else f"mentions ₹{amount:,}")
    summary = "; ".join(p for p in parts if p)
    return {"text": raw, "language": language(raw) if raw else None, "meanings": found, "outcome": outcome,
            "callback_at": to_iso(when) if when else None, "callback_text": when_text, "needs": needs,
            "interest": interest, "amount": amount, "summary": summary,
            "understood": bool(found or amount), "evidence": [(MEANINGS[k][0], v) for k, v in found.items()]}


# Help areas on the Salon Owners side that each need maps to
NEED_TO_HELP = {"need_staff": "staff_hiring", "need_training": "staff_training", "need_marketing": "digital_marketing",
                "need_software": "chairpe", "need_consulting": "business_consulting", "talk_vikas": "vikas_consult",
                "wants_sell": "sell_salon", "need_new_salon": "new_salon_setup"}


# -- Teaching TeamOS new phrases ------------------------------------------------------------------------------------
def teach(db, actor, phrase: str, meaning: str) -> int:
    if not user_can(db, actor, "crm.import") and not user_can(db, actor, "crm.assign"):
        raise PermissionDenied("Only team leads and admins can teach new phrases.")
    phrase = re.sub(r"\s+", " ", (phrase or "").strip().lower())[:60]
    if len(phrase) < 3:
        raise ValidationError({"phrase": "Type the words exactly as the team writes them (at least 3 letters)."})
    if meaning not in MEANINGS:
        raise ValidationError({"meaning": "Choose what the words mean."})
    with db:
        old = db.one("SELECT id FROM remark_phrases WHERE phrase = ?", (phrase,))
        if old:
            db.execute("UPDATE remark_phrases SET meaning = ?, active = 1, created_by = ?, created_at = ? WHERE id = ?",
                       (meaning, actor["id"], now_iso(), old["id"]))
            pid = old["id"]
        else:
            pid = db.insert("remark_phrases", {"phrase": phrase, "meaning": meaning, "active": 1,
                                               "created_by": actor["id"], "created_at": now_iso()})
        audit.record(db, actor["id"], "remarks.taught", None, None, {"phrase": phrase, "meaning": meaning})
    return pid


def forget(db, actor, pid: int) -> None:
    if not user_can(db, actor, "crm.import") and not user_can(db, actor, "crm.assign"):
        raise PermissionDenied("Only team leads and admins can change the dictionary.")
    db.execute("UPDATE remark_phrases SET active = 0 WHERE id = ?", (pid,))


def learned(db) -> list[dict]:
    return db.all("SELECT p.*, u.full_name FROM remark_phrases p LEFT JOIN users u ON u.id = p.created_by "
                  "WHERE p.active = 1 ORDER BY p.created_at DESC")


def not_understood(db, limit: int = 30) -> list[str]:
    """Recent imported remarks TeamOS could not read — the best candidates to teach."""
    import json
    out, seen = [], set()
    cache = _learned(db)
    for r in db.all("SELECT notes FROM sheet_import_rows WHERE notes IS NOT NULL AND notes <> '[]' "
                    "ORDER BY id DESC LIMIT 300"):
        for n in json.loads(r["notes"] or "[]"):
            v = n.split(": ", 1)[1] if ": " in n[:42] else n
            v = v.strip()
            key = v.lower()
            if len(v) < 4 or key in seen or re.fullmatch(r"[\d\s.,:/-]+", v):
                continue
            seen.add(key)
            if not read(v, db, learned_cache=cache)["understood"]:
                out.append(v[:140])
            if len(out) >= limit:
                return out
    return out
