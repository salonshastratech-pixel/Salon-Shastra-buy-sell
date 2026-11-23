"""Smart sheet import: any workbook (Google Sheets export, Excel, CSV) → only the relevant sellers and buyers.

What "smart" means here, in plain terms:
* Every tab is judged on its own: sellers, buyers, or not relevant (staff lists, dashboards, instructions, templates,
  empty tabs). Tabs without a phone column are never imported.
* Columns are recognised by meaning, not exact spelling ("Mobile number", "phone_number", "2. WhatsApp Number",
  "Contact Number" are all the phone). Advertising columns (ad_id, campaign…) are ignored; the form/campaign name
  is kept as the lead's source.
* Every row is cleaned: phones in any format ("p:+91…", "91-77…", "9550700252.0"), money in any format
  ("10–20 Lakhs", "under ₹20 lakhs", "40000/-", "2.5Lakh"), bands ("5-8 chairs"), years ("2024" = started in 2024),
  floors, salon types, timelines.
* Rows that are not real leads are left out with a reason: test/dummy rows, invalid numbers, "not interested",
  "wrong number", people looking for a job or staff. A buyer-sheet row that says "want to sell my salon" becomes a
  seller lead.
* The same person appearing several times (in one tab, in several tabs, or already in TeamOS) becomes ONE record:
  new information fills empty fields; nothing the team already recorded is overwritten (differences become a note).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime

from .common import normalize_indian_mobile

MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 5000
THIS_YEAR = date.today().year

# -- Column recognition --------------------------------------------------------------------------------------------
# (canonical field, side, patterns). First match wins, so specific patterns come before general ones.
# side: c = contact/common, s = seller, b = buyer, n = notes, x = ignore
COLUMNS = [
    ("ignore", "x", [r"^(ad|adset|campaign|form)\s*id$", r"^ad\s*name$", r"^adset\s*name$", r"^is\s*organic$",
                     r"^inbox\s*url$", r"^id$", r"^lead\s*id$", r"declaration", r"acknowledg", r"^consent",
                     r"^column\s*\d+$", r"overdue", r"score", r"^s\.?\s*no$", r"drive link|file link|upload"]),
    ("source_campaign", "c", [r"^form\s*name$", r"^campaign\s*name$", r"^(lead\s*)?source$"]),
    ("preferred_callback", "c", [r"best (contact )?time", r"time to (schedule a )?call"]),
    ("hairdressers", "s", [r"hair\s*dressers"]),
    ("beauticians", "s", [r"no\.? of beauticians"]),
    ("pilot_interest", "s", [r"pilot program"]),
    ("platform", "c", [r"^platform$"]),
    ("timestamp", "c", [r"^timestamp$", r"^created\s*time$", r"submission\s*date", r"^date$"]),
    ("email", "c", [r"e\s*-?mail"]),
    ("whatsapp", "c", [r"whats\s*app"]),
    ("phone", "c", [r"phone", r"mobile", r"contact\s*(number|no)", r"^number$", r"^cell"]),
    ("salon_name", "s", [r"(name of|^)\s*salon\s*name|^name of salon|^salon$|business name|company\s*name"]),
    ("full_name", "c", [r"full\s*name", r"owner\s*name", r"^name\b", r"^names$", r"^1\s*full name", r"authori[sz]ed person"]),
    ("city_area", "c", [r"city\s*\+\s*area"]),
    ("city", "c", [r"^city", r"which city"]),
    ("locality", "c", [r"area\s*/\s*locality|locality|^location|^area$|which area|looking for\?|which location"]),
    ("filled_by", "s", [r"filled by|being filled"]),
    ("decision_maker", "c", [r"decision.?maker"]),
    ("business_type", "s", [r"type of business|^business type$|salon type|^business\s*type"]),
    ("years", "s", [r"years? in operation|when.*start(ed)? the salon"]),
    ("branches", "s", [r"branches|outlets"]),
    ("sale_type", "s", [r"this form is for|^sale type$"]),
    ("chairs", "s", [r"cutting chairs|no\.? of chairs|^chairs|chairs band"]),
    ("wash_stations", "s", [r"shampoo"]),
    ("treatment_rooms", "s", [r"beauty rooms|no\.? of beds"]),
    ("area_sqft", "s", [r"total area|area of the salon|sq\.?\s*ft|sft"]),
    ("floor", "s", [r"floor"]),
    ("property_type", "s", [r"is the property|property type"]),
    ("rent", "s", [r"monthly rent|rent amount|current monthly rent|^rent$"]),
    ("deposit", "s", [r"security deposit|advance"]),
    ("lease_till", "s", [r"valid till|lease.*till"]),
    ("lease_remaining", "s", [r"lease remaining"]),
    ("lease_transferable", "s", [r"lease transferable|transferable to a new owner"]),
    ("sales", "s", [r"monthly sales|monthly gross revenue|current monthly revenue|sales range"]),
    ("expenses", "s", [r"monthly expenses"]),
    ("profit_status", "s", [r"profit status|is the business currently|business status"]),
    ("staff_count", "s", [r"team size|staff count|active staff"]),
    ("staff_continuity", "s", [r"staff.*(continue|stay)|staff continuity"]),
    ("owner_dependency", "s", [r"dependent mainly on you|owner dependency"]),
    ("reason_text", "s", [r"why are you planning to sell|detailed reason"]),
    ("reason_category", "s", [r"primary reason|main reason for considering sale|reason category"]),
    ("urgency", "c", [r"how urgent|^urgency$|top priority"]),
    ("asking_price", "s", [r"expected selling price|selling price|expected price|pricing quoted"]),
    ("inclusions", "s", [r"included in the sale|included in sale"]),
    ("liabilities", "s", [r"dues\s*/\s*liabilities|liabilities\?|loan/emi"]),
    ("litigation", "s", [r"legal.*issues|pending disputes"]),
    ("gst", "s", [r"gst registration"]),
    ("google_rating", "s", [r"google rating"]),
    ("services", "s", [r"revenue.?generating services|services contribute|main revenue services"]),
    ("billing_software", "s", [r"billing software|track sales|tracking system|revenue recording"]),
    ("prepaid", "s", [r"prepaid packages"]),
    ("describes_you", "c", [r"best describes you|describes you"]),
    ("looking_to_buy", "b", [r"looking to buy a salon"]),
    ("new_salon_kind", "b", [r"kind of salon do you want to open"]),
    ("budget", "b", [r"invest(ment)?\s*(budget|range)|how much do you want to invest|planned total investment|"
                     r"invest\s*ment potential|investment potential|^budget"]),
    ("timeline", "b", [r"when do you want|when are you planning|when would you like to start|target timeline|^timeline$|"
                       r"which location are you looking to acquire"]),
    ("intent", "b", [r"why are you interested|investment intent"]),
    ("experience", "b", [r"experience in the salon|beauty industry"]),
    ("visit_willing", "b", [r"willing to visit"]),
    ("lead_type", "n", [r"^lead\s*type$", r"lead temp", r"^priority$", r"lead\s*status"]),
    ("call_status", "n", [r"call\s*status"]),
    ("notes", "n", [r"remark", r"notes?$", r"comment", r"follow\s*up", r"consultation|consultaion", r"payment collected",
                    r"assigned to", r"impact", r"price\?|this price|valuation", r"liabilit.*explain|nature of",
                    r"next step", r"pending amount|outstanding value", r"seriously thinking|does not sell in",
                    r"parking .*visibility", r"instagram"]),
]
# These headers on their own mean the tab is NOT about buying/selling salons (staff, students, products, HR…).
OFF_TOPIC_HEADERS = re.compile(r"\b(stylist role|role|salary|resume|qualification|course|student|trainer|"
                               r"kyc|aadhar|pan card|incentive|attendance|inventory|sku|product name)\b", re.I)
HELPER_TABS = re.compile(r"^(lists?|setup|instructions?|dashboard|team_?productivity|expected_form_headers|"
                         r"today_followup_queue|kpi)", re.I)
OFF_TOPIC_TABS = re.compile(r"staff|employee|hr\b|student|course|academy|webinar|trainer|inventory|product|"
                            r"property inspection|verification form|rate card|menu", re.I)
SELLER_TITLE = re.compile(r"seller|exit|struggling|pilot|owner|sale of salon|sell", re.I)
BUYER_TITLE = re.compile(r"buyer|invest|acquisition|new salon|enquiry|purchase", re.I)
NEW_SALON_TITLE = re.compile(r"new salon|open a salon|new_salon", re.I)


def _norm_header(h) -> str:
    s = str(h or "").replace("_", " ").replace("\n", " ").lower()
    s = re.sub(r"^\s*\d+\s*[\.\)]\s*", "", s)           # "1. Full Name"
    s = re.sub(r"\s+", " ", s).strip(" ?:")
    return s


def recognise(header) -> tuple[str | None, str | None]:
    n = _norm_header(header)
    if not n:
        return None, None
    for field, side, pats in COLUMNS:
        if any(re.search(p, n) for p in pats):
            return field, side
    return None, None


# -- Reading workbooks -------------------------------------------------------------------------------------------
def _cell(c) -> str:
    if c is None:
        return ""
    if isinstance(c, float) and c.is_integer():
        return str(int(c))
    if isinstance(c, (datetime, date)):
        return c.strftime("%Y-%m-%d %H:%M:%S") if isinstance(c, datetime) else c.isoformat()
    return str(c).strip()


def read_workbook(filename: str, data: bytes) -> list[tuple[str, list[list[str]]]]:
    from .errors import ValidationError
    if len(data) > MAX_BYTES:
        raise ValidationError({"file": "File is larger than 8 MB."})
    name = (filename or "").lower()
    if name.endswith(".csv"):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
        return [(filename.rsplit(".", 1)[0], [[(c or "").strip() for c in r] for r in csv.reader(io.StringIO(text))])]
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception:
            raise ValidationError({"file": "Could not read this Excel file."})
        return [(ws.title, [[_cell(c) for c in r] for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
    raise ValidationError({"file": "Upload an Excel (.xlsx) or CSV file, or paste a Google Sheets link."})


PHONE_LIKE = re.compile(r"^(p:)?\+?[\d\s\-\.]{9,16}$")
ID_LIKE = re.compile(r"^(l|ag|as|c|f):\d")


def _looks_like_data(row: list[str]) -> bool:
    vals = [v for v in row if v]
    return bool(vals) and (sum(1 for v in vals if PHONE_LIKE.match(v) or ID_LIKE.match(v)) >= 2)


def find_header(rows: list[list[str]], siblings: list[list[str]] | None = None):
    """Returns (header_index or None, header list). Handles a title row above the header and header-less tabs."""
    nonempty = [i for i, r in enumerate(rows) if any(r)]
    for i in nonempty[:5]:
        r = rows[i]
        if _looks_like_data(r):
            break
        texts = [v for v in r if v and not PHONE_LIKE.match(v)]
        if len(texts) >= 2 and sum(1 for v in r if recognise(v)[0]) >= 1:
            return i, r
    # header-less tab (e.g. a lead-ads sheet pasted without its header): borrow a sibling's header of the same shape
    def trim(r):
        r = list(r)
        while r and not r[-1]:
            r.pop()
        return r
    first = trim(rows[nonempty[0]]) if nonempty else []
    for sib in siblings or []:
        sib = trim(sib)
        if sum(1 for v in sib if recognise(v)[0]) < 3:
            continue
        # the borrowed header must line up with this tab's data: the phone column holds phone numbers, and an
        # id / date column (if any) holds ids / dates — at least two checks must agree
        checks = 0
        for i, h in enumerate(sib):
            fld, v = recognise(h)[0], (first[i] if i < len(first) else "")
            if fld in ("phone", "whatsapp"):
                if not PHONE_LIKE.match(v or ""):
                    checks = -99
                    break
                checks += 1
            elif _norm_header(h) in ("id", "lead id") and ID_LIKE.match(v or ""):
                checks += 1
            elif fld == "timestamp" and re.match(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}", v or ""):
                checks += 1
        if checks >= 2:
            return None, sib + [f"Column {j + 1}" for j in range(len(sib), len(first))]
    return None, []


# -- Value cleaning --------------------------------------------------------------------------------------------------
def clean_phones(v: str) -> list[str]:
    """All valid mobiles in a cell: '9908846247/9573955067' → both."""
    out = []
    for part in re.split(r"[/,;|]| or |&", str(v or "")):
        p = clean_phone(part)
        if p and p not in out:
            out.append(p)
    return out


def clean_phone(v: str) -> str | None:
    s = str(v or "").strip()
    if re.search(r"[/,;|]| or ", s):
        found = clean_phones(s)
        return found[0] if found else None
    if not s or "dummy" in s.lower():
        return None
    s = re.sub(r"^p:", "", s, flags=re.I)
    s = re.sub(r"\.0$", "", s)
    digits = re.sub(r"\D", "", s)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    phone, err = normalize_indian_mobile(digits)
    return None if err else phone


_NUM = r"(\d+(?:\.\d+)?)"
UNIT = {"cr": 1e7, "crore": 1e7, "crores": 1e7, "l": 1e5, "lac": 1e5, "lakh": 1e5, "lakhs": 1e5, "lacs": 1e5,
        "k": 1e3, "thousand": 1e3}


def money_range(v: str, small_means_lakh: bool = False) -> tuple[int | None, int | None]:
    """'10–20 Lakhs' → (10L, 20L); 'under ₹20 lakhs' → (None, 20L); '40000/-' → (40000, 40000); '2.5Lakh' → 2.5L."""
    s = str(v or "").lower().replace("_", " ").replace(",", "").replace("₹", " ").replace("rs.", " ").replace("rs", " ")
    s = s.replace("–", "-").replace("—", "-").replace(" to ", "-").replace("/-", " ")
    s = re.sub(r"(\d)\s+(?=\d)", r"\1", s)  # "30 lakhs to35lakhs" safety
    if not re.search(r"\d", s):
        return None, None
    unit_rx = r"(\d+(?:\.\d+)?)\s*(crores?|cr|lakhs?|lacs?|lac|l|k|thousand)(?![a-z])"
    unit_m = re.findall(unit_rx, s)
    nums = [float(x) for x in re.findall(_NUM, s)]
    if not nums:
        return None, None
    trailing = unit_m[-1][1] if unit_m else None

    def scale(x, u=None):
        u = u or trailing
        if u:
            return int(round(x * UNIT.get(u.rstrip("s") if u.rstrip("s") in UNIT else u, 1)))
        if small_means_lakh and x < 100:
            return int(round(x * 1e5))   # "1.8" in a monthly-expenses column means ₹1.8 lakh
        return int(round(x))
    if unit_m and len(unit_m) >= 2:
        vals = [scale(float(a), b) for a, b in unit_m[:2]]
    else:
        vals = [scale(x) for x in nums[:2]]
    lo, hi = (min(vals), max(vals)) if len(vals) == 2 else (vals[0], vals[0])
    if re.search(r"\b(under|below|less than|upto|up to|within)\b", s):
        return None, hi
    if re.search(r"\b(above|over|more than|\+)\b", s) or s.strip().endswith("+"):
        return lo, None
    return lo, hi


def first_int(v: str) -> int | None:
    m = re.search(r"\d+", str(v or "").replace(",", ""))
    return int(m.group()) if m else None


def band_low(v: str) -> int | None:
    """'5-8' → 5 (conservative), '10+' → 10, '4.0' → 4."""
    s = str(v or "")
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    return int(float(m.group(1))) if m else None


def parse_date(v: str) -> str | None:
    s = str(v or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d/%m/%Y %H:%M:%S", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:19] if "%H" in fmt else s[:10], fmt).date().isoformat()
        except ValueError:
            continue
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})T", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


HYD = ["hyderabad", "hyd", "hyderbad", "hydb", "secunderabad", "secundrabad", "cyberabad"]
HYD_AREAS = ["kompally", "kondapur", "manikonda", "miyapur", "kothapet", "ameenpur", "boduppal", "dilsukhnagar",
             "sr nagar", "s r nagar", "srinagar colony", "sri nagar colony", "jubilee hills", "jublihils", "film nagar",
             "gachibowli", "madhapur", "kukatpally", "ameerpet", "balapur", "meerpet", "badangpet", "nadergul",
             "chandanagar", "lb nagar", "uppal", "kphb", "nizampet", "bachupally", "begumpet", "banjara hills",
             "attapur", "mehdipatnam", "tolichowki", "habsiguda", "tarnaka", "malkajgiri", "alwal", "medchal",
             "shamshabad", "narsingi", "kokapet", "hitech city", "hitec city", "bn reddy", "vanasthalipuram",
             "hayathnagar", "ecil", "a s rao nagar", "sainikpuri", "manikonda", "puppalaguda", "patancheru",
             "beeramguda", "chanda nagar", "lingampally", "nallagandla", "kokapet", "gandipet", "moosapet",
             "erragadda", "yousufguda", "panjagutta", "somajiguda", "himayatnagar", "abids", "koti", "saroornagar",
             "champapet", "karmanghat", "nagole", "peerzadiguda", "pocharam", "ghatkesar", "kothur", "amangal"]
STATE_WORDS = {"telangana", "telagana", "andhra pradesh", "andhra", "andra", "ap", "ts", "india"}


def split_place(city_raw: str = "", area_raw: str = "") -> tuple[str | None, str | None]:
    """'sr nagar HYD' → ('Hyderabad', 'Sr Nagar'); 'Ameenpur, Chandanagar, Hyderabad.' → ('Hyderabad', 'Ameenpur')."""
    def tidy(x):
        x = re.sub(r"\s+", " ", re.sub(r"[^\w\s,/&-]", " ", x or "")).strip(" ,.-")
        return x.title() if x else None
    text = " ".join(p for p in [area_raw, city_raw] if p)
    low = text.lower()
    city = None
    if any(re.search(rf"\b{h}\b", low) for h in HYD):
        city = "Hyderabad"
    area = None
    for a in HYD_AREAS:
        if re.search(rf"\b{re.escape(a)}\b", low):
            area, city = a.title(), city or "Hyderabad"
            break
    if not city:
        parts = [p.strip() for p in re.split(r"[,/]| - ", city_raw or area_raw or "") if p.strip()]
        parts = [p for p in parts if p.lower() not in STATE_WORDS and not re.fullmatch(r"\d{6}", p)]
        city = tidy(parts[-1]) if parts else None
        if city and city.lower() in ("upcoming city", "na", "n/a", "no", "nil", "none"):
            city = None
    if not area:
        src = area_raw or (city_raw if city_raw and city and city.lower() not in (city_raw or "").lower().strip() else "")
        parts = [p.strip() for p in re.split(r"[,/]", src or "") if p.strip()]
        parts = [p for p in parts if p.lower() not in STATE_WORDS and p.lower() not in HYD
                 and not re.fullmatch(r"\d{6}", p)]
        area = tidy(re.sub(rf"\b({'|'.join(HYD)})\b", "", parts[0], flags=re.I)) if parts else None
        if area and city and area.lower() == city.lower():
            area = None
    return city, area


def yes_no(v: str) -> str | None:
    s = str(v or "").strip().lower()
    if s.startswith(("yes", "y ", "y")) and not s.startswith("yearly"):
        return "yes"
    if s.startswith(("no", "n ")) or s in ("n", "nil", "none") or "no pending" in s:
        return "no"
    return None


def salon_format(v: str) -> tuple[str | None, str | None]:
    s = str(v or "").lower()
    pos = "luxury" if "luxury" in s else ("premium" if "premium" in s else None)
    if "unisex" in s or "family" in s:
        return "unisex", pos
    if re.search(r"\bmen|gents|barber|male", s) and "women" not in s:
        return "men", pos
    if re.search(r"women|ladies|beauty|parlou?r|bridal|female|makeover", s):
        return "women", pos
    return None, pos


def floor_level(v: str) -> str | None:
    s = str(v or "").lower()
    if "mall" in s:
        return "mall"
    if "base" in s or "cellar" in s:
        return "basement"
    if "ground" in s or s.strip() in ("g", "gf", "0"):
        return "ground"
    if re.search(r"\b(1st|first|1)\b", s):
        return "first"
    if re.search(r"\b(2nd|3rd|second|third|[2-9](th)?)\b", s):
        return "upper"
    return None


def purchase_window(v: str) -> str | None:
    s = str(v or "").lower().replace("_", " ")
    if re.search(r"immediate|15 days|30 days|1-2 month|1–2 month|asap|right now", s):
        return "30d"
    if re.search(r"3 month|1–3|1-3|90", s):
        return "90d"
    if re.search(r"6 month|3–6|3-6", s):
        return "180d"
    if re.search(r"explor|later|not sure|6\+|year", s):
        return "later"
    return None


def sale_timeline(v: str) -> str | None:
    s = str(v or "").lower()
    if re.search(r"immediate|30 days|asap|fast sale|urgent", s):
        return "immediate"
    if re.search(r"1-3|1–3|3 month", s):
        return "within_3m"
    if re.search(r"3-6|3–6|6 month", s):
        return "3_6m"
    if re.search(r"6-12|6–12|year", s):
        return "6_12m"
    if re.search(r"no rush|not urgent|best price|flexible", s):
        return "no_rush"
    return None


def reason_category(v: str) -> str | None:
    s = str(v or "").lower()
    for key, words in (("relocation", "relocat|moving|shift"), ("financial_pressure", "loss|financial|debt|money|rent"),
                       ("partnership_change", "partner"), ("new_venture", "new venture|new business|other business"),
                       ("retirement", "retire"), ("personal", "personal|health|family")):
        if re.search(words, s):
            return key
    return "other" if s else None


NOT_RELEVANT = [
    (re.compile(r"<test lead|dummy data|^test$|^testing$"), "Test / dummy entry"),
    (re.compile(r"\bnot\s*(intre?s?te?d|interested|intrested)\b|no interest|not looking"), "Not interested"),
    (re.compile(r"wrong\s*(number|no)"), "Wrong number"),
    (re.compile(r"looking for (a )?(staff|job|work)|asking (for )?staff|need(s)? staff|staff requirement|"
                r"looking for job|job seeker|want(s)? (a )?job|^staff$"), "Looking for staff / a job — not a buyer or seller"),
]
SELL_SIGNAL = re.compile(r"want(s)? to sell|sell(ing)? (the|my|his|her)? ?salon|salon (for )?sale|exit|i own a salon")


def judge_row(text: str) -> str | None:
    t = text.lower()
    for rx, why in NOT_RELEVANT:
        if rx.search(t):
            return why
    return None


def file_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


__all__ = ["recognise", "read_workbook", "find_header", "clean_phone", "money_range", "split_place", "judge_row",
           "json"]
