"""Shared domain helpers: phone normalisation, INR formatting, value parsing."""
from __future__ import annotations

import re


def normalize_indian_mobile(raw: str | None) -> tuple[str | None, str | None]:
    """Return (+91XXXXXXXXXX, None) or (None, error). Accepts common Indian formats."""
    if not raw or not raw.strip():
        return None, "Mobile number is required."
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("0091"):
        digits = digits[4:]
    elif len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "6789":
        return None, "Enter a valid 10-digit Indian mobile number."
    return "+91" + digits, None


def format_inr(amount: int | float | None, short: bool = True) -> str:
    """₹ with Indian grouping; short form uses lakh / crore (e.g. ₹20 L, ₹1.2 Cr)."""
    if amount is None:
        return "Not provided"
    amount = int(round(amount))
    neg = amount < 0
    a = abs(amount)
    if short and a >= 1_00_00_000:
        text = f"₹{a / 1_00_00_000:.2f}".rstrip("0").rstrip(".") + " Cr"
    elif short and a >= 1_00_000:
        text = f"₹{a / 1_00_000:.2f}".rstrip("0").rstrip(".") + " L"
    else:
        s = str(a)
        if len(s) > 3:
            head, tail = s[:-3], s[-3:]
            head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
            s = head + "," + tail
        text = "₹" + s
    return ("−" if neg else "") + text


PLACEHOLDER_PLACES = {"city not given", "area not given", "not given", "salon name not given"}


def clean_place(v) -> str:
    """A city / area as typed: spaces tidied and a word typed twice made one - the City box starts with
    'Hyderabad', so typing the city again gave 'HyderabadHyderabad'."""
    import re as _re
    s = " ".join(str(v or "").split()).strip(" ,")
    m = _re.fullmatch(r"(?i)(.+?)[ ,]*\1", s)
    return m.group(1).strip(" ,") if m and len(m.group(1)) >= 3 else s


def known_place(v):
    """None for placeholder text like 'City not given' (imports with missing values)."""
    return None if not v or str(v).strip().lower() in PLACEHOLDER_PLACES else v


def parse_inr(value: str | None) -> int | None:
    """Parse '20,00,000', '20L', '20 lakh', '1.2 cr', '500000' -> int rupees. Blank -> None."""
    if value is None:
        return None
    v = value.strip().lower().replace(",", "").replace("₹", "").replace("rs.", "").replace("rs", "").strip()
    if v == "":
        return None
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(l|lac|lakh|lakhs|cr|crore|crores|k|thousand|thousands)?", v)
    if not m:
        raise ValueError("Enter an amount like 2500000, 25L or 1.2Cr")
    num = float(m.group(1))
    unit = m.group(2)
    mult = {"l": 1e5, "lac": 1e5, "lakh": 1e5, "lakhs": 1e5, "cr": 1e7, "crore": 1e7, "crores": 1e7, "k": 1e3, "thousand": 1e3, "thousands": 1e3}.get(unit, 1)
    result = int(round(num * mult))
    if result < 0:
        raise ValueError("Amount cannot be negative")
    return result


def parse_int(value: str | None, minimum: int = 0, maximum: int = 10**9) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        n = int(str(value).strip().replace(",", ""))
    except ValueError:
        raise ValueError("Enter a whole number")
    if n < minimum or n > maximum:
        raise ValueError(f"Must be between {minimum} and {maximum}")
    return n


def parse_float(value: str | None, minimum: float = 0, maximum: float = 100) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        n = float(str(value).strip())
    except ValueError:
        raise ValueError("Enter a number")
    if n < minimum or n > maximum:
        raise ValueError(f"Must be between {minimum} and {maximum}")
    return n
