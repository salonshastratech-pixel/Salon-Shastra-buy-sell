"""Branded, premium report PDFs (ReportLab, built-in fonts so they render the same on any server).

Salon owner — SWOT report:     cover (logo, salon, overall health dial, "what's inside") · 01 Snapshot · 02 SWOT ·
                               03 Growth plan + talk to Vikas.
Investor — readiness report:   cover (logo, name, readiness dial, verdict) · 01 Snapshot · 02 Recommendation ·
                               03 Next steps.
One theme for both: ivory paper, mahogany and gold, serif headings.
"""
from __future__ import annotations

import io
import os
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.platypus import (CondPageBreak, Flowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from .pilot import CONSULTANT_NAME, FEES

# -- Theme -------------------------------------------------------------------------------------------------------------
BRAND = colors.HexColor("#c04000")          # Salon Shastra mahogany-orange
BRAND_DARK = colors.HexColor("#893815")
MAHOGANY = colors.HexColor("#5a1e0e")       # deep panels
GOLD = colors.HexColor("#b8893a")
GOLD_SOFT = colors.HexColor("#f3e6cc")
IVORY = colors.HexColor("#fffaf4")
CREAM = colors.HexColor("#fbf3ea")
LINE = colors.HexColor("#ecdccd")
INK = colors.HexColor("#231a14")
MUTED = colors.HexColor("#7a6a5f")
TINT = CREAM
SOFT = LINE
GOOD, MID, BAD = colors.HexColor("#1f7a4d"), colors.HexColor("#c98a00"), colors.HexColor("#b3261e")
QUAD = {  # title colour, background
    "strengths": (colors.HexColor("#1f7a4d"), colors.HexColor("#eef7f1")),
    "weaknesses": (colors.HexColor("#b3261e"), colors.HexColor("#fdf0ee")),
    "opportunities": (colors.HexColor("#1d5fa8"), colors.HexColor("#eef4fb")),
    "threats": (colors.HexColor("#9a6200"), colors.HexColor("#fff6e5")),
}
W, H = A4
M = 17 * mm
CW = W - 2 * M
LOGO = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "brand", "logo.png")
CONTACT = "www.salonshastra.com  ·  +91 86868 78090  ·  support@salonshastra.com"


def _t(s) -> str:
    """Escape for Paragraph markup; built-in fonts have no rupee sign, so write 'Rs'."""
    return escape(str(s or "")).replace("₹", "Rs ").replace("  ", " ")


def _spaced(text: str) -> str:
    """Letter-spaced caps for small gold labels inside paragraphs."""
    return "&nbsp;&nbsp;&nbsp;".join("&nbsp;".join(escape(w.upper())) for w in text.split())


def _plain(s) -> str:
    return str(s or "").replace("₹", "Rs ")


def _col(score) -> colors.Color:
    return GOOD if score >= 7 else MID if score >= 5 else BAD


def _styles():
    base = dict(fontName="Helvetica", fontSize=10, leading=14.5, textColor=INK)
    st = lambda name, **kw: ParagraphStyle(name, **{**base, **kw})  # noqa: E731
    return {
        "h1": st("h1", fontName="Times-Bold", fontSize=21, leading=25, textColor=MAHOGANY, spaceAfter=4),
        "h2": st("h2", fontName="Times-Bold", fontSize=14, leading=18, textColor=MAHOGANY, spaceBefore=6, spaceAfter=5),
        "kicker": st("kicker", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=GOLD),
        "body": st("body"),
        "lead": st("lead", fontSize=11, leading=16),
        "small": st("small", fontSize=8.5, leading=11.5, textColor=MUTED),
        "bullet": st("bullet", fontSize=9.6, leading=13.2, leftIndent=11, bulletIndent=0, spaceAfter=3.5),
        "label": st("label", fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=MUTED),
        "value": st("value", fontName="Times-Bold", fontSize=12.5, leading=15),
        "quad": st("quad", fontName="Times-Bold", fontSize=14, leading=30, spaceAfter=4),
        "center": st("center", alignment=TA_CENTER),
        "white": st("white", textColor=colors.white, fontName="Helvetica-Bold", fontSize=11, leading=11,
                    alignment=TA_CENTER),
        "how": st("how", fontSize=9.4, leading=13),
        "tile_l": st("tile_l", fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=MUTED),
        "tile_v": st("tile_v", fontName="Times-Bold", fontSize=15, leading=19),
        "cta_h": st("cta_h", fontName="Times-Bold", fontSize=17, leading=21, textColor=GOLD_SOFT),
        "cta": st("cta", textColor=colors.HexColor("#f7ebe0"), fontSize=10, leading=14.5),
        "cta_small": st("cta_small", textColor=colors.HexColor("#e2cdb9"), fontSize=8.5, leading=12),
    }


# -- Building blocks -------------------------------------------------------------------------------------------------
class SectionHead(Flowable):
    """01 — SNAPSHOT  (gold, spaced) above a serif title and a short gold rule."""

    def __init__(self, num, kicker, title, width=CW):
        super().__init__()
        self.num, self.kicker, self.title, self.width, self.height = num, kicker, title, width, 21 * mm

    def draw(self):
        c = self.canv
        c.setFillColor(GOLD)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(0, self.height - 5 * mm, f"{self.num}  —  {' '.join(self.kicker.upper())}")
        c.setFillColor(MAHOGANY)
        c.setFont("Times-Bold", 21)
        c.drawString(0, self.height - 13.5 * mm, _plain(self.title)[:70])
        c.setStrokeColor(GOLD)
        c.setLineWidth(1.4)
        c.line(0, self.height - 17 * mm, 22 * mm, self.height - 17 * mm)


class ScoreBar(Flowable):
    def __init__(self, label, score, width):
        super().__init__()
        self.label, self.score, self.width, self.height = label, int(score), width, 9.5 * mm

    def draw(self):
        c = self.canv
        c.setFont("Helvetica", 9.5)
        c.setFillColor(INK)
        c.drawString(0, 3.2 * mm, _plain(self.label))
        x0, bw = 56 * mm, self.width - 56 * mm - 18 * mm
        c.setFillColor(colors.HexColor("#f1e6dc"))
        c.roundRect(x0, 2.2 * mm, bw, 3.6 * mm, 1.8 * mm, stroke=0, fill=1)
        c.setFillColor(_col(self.score))
        c.roundRect(x0, 2.2 * mm, max(3.6 * mm, bw * self.score / 10), 3.6 * mm, 1.8 * mm, stroke=0, fill=1)
        for i in range(1, 10):                          # quiet tick marks every point
            c.setStrokeColor(colors.white)
            c.setLineWidth(0.6)
            c.line(x0 + bw * i / 10, 2.2 * mm, x0 + bw * i / 10, 5.8 * mm)
        c.setFillColor(_col(self.score))
        c.roundRect(self.width - 14 * mm, 1.2 * mm, 14 * mm, 5.6 * mm, 2.8 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(self.width - 7 * mm, 2.9 * mm, f"{self.score}/10")


class StepDot(Flowable):
    def __init__(self, n, first=False):
        super().__init__()
        self.n, self.first, self.width, self.height = n, first, 11 * mm, 11 * mm

    def draw(self):
        c = self.canv
        r = 4.6 * mm
        c.setFillColor(GOLD if self.first else BRAND)
        c.circle(5.5 * mm, 5.5 * mm, r, stroke=0, fill=1)
        c.setStrokeColor(colors.white)
        c.setLineWidth(1.2)
        c.circle(5.5 * mm, 5.5 * mm, r - 0.9 * mm, stroke=1, fill=0)
        c.setFillColor(colors.white)
        c.setFont("Times-Bold", 12)
        c.drawCentredString(5.5 * mm, 4.1 * mm, str(self.n))


def _box(flowables, bg, pad=9, border=None, bar=None, width=CW):
    """A rounded panel; `bar` draws a thick accent line on the left."""
    t = Table([[flowables]], colWidths=[width])
    st = [("BACKGROUND", (0, 0), (-1, -1), bg), ("LEFTPADDING", (0, 0), (-1, -1), pad + (3 if bar else 0)),
          ("RIGHTPADDING", (0, 0), (-1, -1), pad), ("TOPPADDING", (0, 0), (-1, -1), pad),
          ("BOTTOMPADDING", (0, 0), (-1, -1), pad), ("ROUNDEDCORNERS", [7, 7, 7, 7])]
    if border:
        st.append(("BOX", (0, 0), (-1, -1), 0.7, border))
    if bar:
        st.append(("LINEBEFORE", (0, 0), (0, -1), 4, bar))
    t.setStyle(TableStyle(st))
    return t


def _tiles(items, S) -> Table:
    """Side-by-side spotlight tiles: [(label, value, colour)]."""
    w = (CW - 6 * mm * (len(items) - 1)) / len(items)
    cells, row = [], []
    for i, (label, value, col) in enumerate(items):
        row.append([Paragraph(_t(label).upper(), S["tile_l"]),
                    Paragraph(f'<font color="{col.hexval()}">{_t(value)}</font>', S["tile_v"])])
        if i < len(items) - 1:
            row.append("")
    cells.append(row)
    widths = []
    for i in range(len(items)):
        widths.append(w)
        if i < len(items) - 1:
            widths.append(6 * mm)
    t = Table(cells, colWidths=widths)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 9),
          ("BOTTOMPADDING", (0, 0), (-1, -1), 10), ("LEFTPADDING", (0, 0), (-1, -1), 10)]
    for i, (_l, _v, col) in enumerate(items):
        x = i * 2
        st += [("BACKGROUND", (x, 0), (x, 0), IVORY), ("BOX", (x, 0), (x, 0), 0.7, LINE),
               ("LINEABOVE", (x, 0), (x, 0), 3, col)]
    t.setStyle(TableStyle(st))
    return t


def _facts(facts, S) -> Table | None:
    cells = [[Paragraph(_t(k).upper(), S["label"]), Paragraph(_t(v), S["value"])] for k, v in facts or []]
    if not cells:
        return None
    rows = []
    for i in range(0, len(cells), 4):
        rows.append(cells[i:i + 4] + [[Paragraph("", S["label"])]] * (4 - len(cells[i:i + 4])))
    t = Table(rows, colWidths=[CW / 4] * 4)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, -1), IVORY),
                           ("BOX", (0, 0), (-1, -1), 0.7, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
                           ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("ROUNDEDCORNERS", [7, 7, 7, 7])]))
    return t


def _bullets(items, S) -> list:
    return [Paragraph(_t(x), S["bullet"], bulletText="•") for x in (items or ["—"])]


def _steps(steps, S, with_how=True) -> list:
    out = []
    for i, st in enumerate(steps or [], 1):
        when = f"&nbsp;&nbsp;<font color='{BRAND.hexval()}' size='8.5'>{_t(st.get('when'))}</font>" if st.get("when") else ""
        badge = (f"&nbsp;&nbsp;<font color='{GOLD.hexval()}' size='8'><b>START HERE</b></font>" if i == 1 else "")
        body = [Paragraph(f"<b>{_t(st['title'])}</b>{when}{badge}", S["body"])]
        if st.get("why"):
            body.append(Paragraph(_t(st["why"]), S["small"]))
        if with_how and st.get("how"):
            body.append(Paragraph(f"<font color='{BRAND_DARK.hexval()}'><b>How we help:</b></font> {_t(st['how'])}",
                                  S["how"]))
        row = Table([[StepDot(i, first=i == 1), body]], colWidths=[14 * mm, CW - 14 * mm])
        st_ = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 6),
               ("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("LEFTPADDING", (0, 0), (0, 0), 0),
               ("LINEBELOW", (1, 0), (1, 0), 0.5, LINE)]
        if i == 1:
            st_ += [("BACKGROUND", (0, 0), (-1, -1), GOLD_SOFT), ("ROUNDEDCORNERS", [7, 7, 7, 7]),
                    ("LEFTPADDING", (0, 0), (0, 0), 4)]
        row.setStyle(TableStyle(st_))
        out.append(KeepTogether(row))
    return out


def _area_section(c: dict, S) -> list:
    """'Your area' — nearby salons from OpenStreetMap, with what it means (only when the look-up has arrived)."""
    a = c.get("area")
    if not a:
        return []
    labels = [("salon", "Hair & unisex"), ("beauty", "Beauty parlours"), ("nails", "Nail studios"),
              ("barber", "Barber shops"), ("spa", "Spas")]
    cells = [[Paragraph(f"<font name='Times-Bold' size='17' color='{MAHOGANY.hexval()}'>{a['counts'].get(k, 0)}</font>"
                        f"<br/><font size='7.5' color='{MUTED.hexval()}'>{escape(lbl).upper()}</font>", S["center"])
              for k, lbl in labels]]
    tbl = Table(cells, colWidths=[(CW - 22) / 5] * 5)
    tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.white), ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                             ("INNERGRID", (0, 0), (-1, -1), 0.6, LINE), ("TOPPADDING", (0, 0), (-1, -1), 6),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    inner = [Paragraph(f"Around {escape(a['place'])} — within {a['radius_km']:g} km", S["h2"]), tbl, Spacer(1, 6)]
    inner += _bullets(a.get("tips"), S) if a.get("tips") else []
    inner.append(Paragraph("Source: OpenStreetMap — places volunteers have mapped, so real numbers are usually higher. "
                           "A guide, not a census; confirm with a visit.", S["small"]))
    return [Spacer(1, 8), KeepTogether(_box(inner, CREAM, pad=11, bar=GOLD)), Spacer(1, 6)]


# -- Page furniture ----------------------------------------------------------------------------------------------------
def _dial(c, cx, cy, r, value, label):
    """Score dial: ivory track, coloured arc, big serif number."""
    c.setLineCap(1)
    c.setStrokeColor(colors.HexColor("#efe2d6"))
    c.setLineWidth(9)
    c.circle(cx, cy, r, stroke=1, fill=0)
    col = _col(value)
    c.setStrokeColor(col)
    c.arc(cx - r, cy - r, cx + r, cy + r, startAng=90, extent=-360 * max(0.02, min(1.0, value / 10)))
    c.setFillColor(INK)
    c.setFont("Times-Bold", 34)
    c.drawCentredString(cx, cy - 2 * mm, f"{value:.1f}")
    c.setFont("Helvetica", 9)
    c.setFillColor(MUTED)
    c.drawCentredString(cx, cy - 8 * mm, "out of 10")
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(GOLD)
    c.drawCentredString(cx, cy - r - 9 * mm, " ".join(label.upper()))


def _logo(c, x, y, w, h):
    if os.path.exists(LOGO):
        try:
            c.drawImage(LOGO, x, y, width=w, height=h, preserveAspectRatio=True, anchor="sw", mask="auto")
            return True
        except Exception:  # noqa: BLE001 — a bad logo file must not stop the report
            pass
    c.setFillColor(MAHOGANY)
    c.setFont("Times-Bold", 18)
    c.drawString(x, y + 3 * mm, "Salon Shastra")
    return False


def _cover(meta: dict):
    """Page 1: ivory paper, mahogany spine, logo, name, the overall dial and a 'what's inside' list."""
    def draw(c, doc):
        c.saveState()
        c.setFillColor(IVORY)
        c.rect(0, 0, W, H, stroke=0, fill=1)
        c.setFillColor(MAHOGANY)                                    # spine
        c.rect(0, 0, 11 * mm, H, stroke=0, fill=1)
        c.setFillColor(GOLD)
        c.rect(11 * mm, 0, 1.2 * mm, H, stroke=0, fill=1)
        x = M + 6 * mm
        _logo(c, x, H - 38 * mm, 66 * mm, 22 * mm)
        c.setFillColor(GOLD)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(x, H - 52 * mm, " ".join(meta["kicker"].upper()))
        c.setStrokeColor(GOLD)
        c.setLineWidth(0.8)
        c.line(x, H - 55 * mm, W - M, H - 55 * mm)
        c.setFillColor(INK)
        c.setFont("Times-Bold", 30)
        lines = simpleSplit(_plain(meta["title"]), "Times-Bold", 30, W - M - x)[:2]
        y = H - 70 * mm
        for ln in lines:
            c.drawString(x, y, ln)
            y -= 12 * mm
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 11)
        c.drawString(x, y + 3 * mm, _plain(meta["prepared"])[:95])
        # the dial
        _dial(c, x + 33 * mm, H - 148 * mm, 25 * mm, meta["dial"], meta["dial_label"])
        if meta.get("chip"):
            text, col = meta["chip"]
            tw = c.stringWidth(text, "Helvetica-Bold", 9) + 10 * mm
            c.setFillColor(col)
            c.roundRect(x + 33 * mm - tw / 2, H - 194 * mm, tw, 7 * mm, 3.5 * mm, stroke=0, fill=1)
            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", 9)
            c.drawCentredString(x + 33 * mm, H - 191.6 * mm, text)
        # what's inside
        ix, iy, iw = x + 78 * mm, H - 118 * mm, W - M - (x + 78 * mm)
        c.setFillColor(CREAM)
        c.roundRect(ix, iy - 72 * mm, iw, 78 * mm, 4 * mm, stroke=0, fill=1)
        c.setFillColor(MAHOGANY)
        c.setFont("Times-Bold", 15)
        c.drawString(ix + 7 * mm, iy - 4 * mm, "What's inside")
        c.setStrokeColor(GOLD)
        c.setLineWidth(1.2)
        c.line(ix + 7 * mm, iy - 7 * mm, ix + 25 * mm, iy - 7 * mm)
        ty = iy - 16 * mm
        for n, (head, page) in enumerate(meta["inside"], 1):
            c.setFillColor(GOLD)
            c.setFont("Times-Bold", 13)
            c.drawString(ix + 7 * mm, ty, f"0{n}")
            c.setFillColor(INK)
            c.setFont("Helvetica", 9.6)
            wrapped = simpleSplit(_plain(head), "Helvetica", 9.6, iw - 30 * mm)[:2]
            for k, ln in enumerate(wrapped):
                c.drawString(ix + 17 * mm, ty - k * 4.4 * mm, ln)
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 8)
            c.drawRightString(ix + iw - 6 * mm, ty, f"p. {page}")
            ty -= (4.4 * len(wrapped) + 7) * mm
        # teaser numbers + strip
        ty0 = H - 214 * mm
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(x, ty0 + 12 * mm, W - M, ty0 + 12 * mm)
        cols = meta.get("stats") or []
        cw_ = (W - M - x) / max(1, len(cols))
        for i, (num, label) in enumerate(cols):
            cx = x + cw_ * i
            c.setFillColor(BRAND)
            c.setFont("Times-Bold", 30)
            c.drawString(cx, ty0, str(num))
            c.setFillColor(MUTED)
            c.setFont("Helvetica-Bold", 7.5)
            for k, ln in enumerate(simpleSplit(label.upper(), "Helvetica-Bold", 7.5, cw_ - 8 * mm)[:2]):
                c.drawString(cx, ty0 - 6 * mm - k * 3.6 * mm, ln)
        if meta.get("teaser"):
            sy = ty0 - 30 * mm
            c.setFillColor(GOLD_SOFT)
            c.roundRect(x, sy - 6 * mm, W - M - x, 15 * mm, 3 * mm, stroke=0, fill=1)
            c.setFillColor(GOLD)
            c.rect(x, sy - 6 * mm, 1.4 * mm, 15 * mm, stroke=0, fill=1)
            c.setFillColor(MAHOGANY)
            c.setFont("Times-Bold", 12)
            c.drawString(x + 6 * mm, sy + 2.5 * mm, meta["teaser"][0])
            c.setFillColor(INK)
            c.setFont("Helvetica", 9)
            c.drawString(x + 6 * mm, sy - 2.5 * mm, _plain(meta["teaser"][1])[:110])
        # foot
        c.setFillColor(MAHOGANY)
        c.rect(11 * mm, 0, W, 24 * mm, stroke=0, fill=1)
        c.setFillColor(GOLD_SOFT)
        c.setFont("Times-Bold", 11)
        c.drawString(x, 14 * mm, meta["foot_title"])
        c.setFillColor(colors.HexColor("#e2cdb9"))
        c.setFont("Helvetica", 8)
        c.drawString(x, 8.5 * mm, CONTACT)
        c.drawRightString(W - M, 14 * mm, meta["date"])
        c.drawRightString(W - M, 8.5 * mm, "Confidential")
        c.restoreState()
    return draw


def _inner(meta: dict):
    def draw(c, doc):
        c.saveState()
        c.setFillColor(IVORY)
        c.rect(0, 0, W, H, stroke=0, fill=1)
        _logo(c, M, H - 16 * mm, 32 * mm, 10.5 * mm)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 8.5)
        c.drawRightString(W - M, H - 11 * mm, _plain(meta["title"])[:55] + "  ·  " + meta["short"])
        c.setStrokeColor(GOLD)
        c.setLineWidth(0.8)
        c.line(M, H - 19 * mm, W - M, H - 19 * mm)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.5)
        c.line(M, 14 * mm, W - M, 14 * mm)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7.5)
        c.drawString(M, 9 * mm, meta["foot"])
        c.setFillColor(BRAND)
        c.setFont("Helvetica-Bold", 8)
        c.drawRightString(W - M, 9 * mm, f"{doc.page}")
        c.restoreState()
    return draw


def _doc(buf, title, subject):
    return SimpleDocTemplate(buf, pagesize=A4, leftMargin=M, rightMargin=M, topMargin=25 * mm, bottomMargin=20 * mm,
                             title=title, author="Salon Shastra", subject=subject)


def _cta(S, booking_link, salon=True) -> list:
    inner = [Paragraph(_spaced("Your next conversation"), S["kicker"]), Spacer(1, 3),
             Paragraph(f"Talk it through with {CONSULTANT_NAME}", S["cta_h"]), Spacer(1, 3),
             Paragraph(("Founder of Salon Shastra. In one call you get a clear, personal plan for your salon — what to "
                        "fix first, what to stop, and how much it can add each month." if salon else
                        "Founder of Salon Shastra. One honest call on your plan, your money and the right salon for you."),
                       S["cta"]), Spacer(1, 6)]
    if booking_link:
        inner.append(Paragraph(f'Pick a time that suits you: <link href="{escape(booking_link)}" '
                               f'color="{GOLD_SOFT.hexval()}"><u>{escape(booking_link)}</u></link>', S["cta"]))
    else:
        inner.append(Paragraph("Reply to our WhatsApp message to book a time.", S["cta"]))
    if salon:
        inner.append(Spacer(1, 8))
        pr = Table([[Paragraph("<b>Consultation</b>", S["cta_small"]), Paragraph("<b>Hyderabad</b>", S["cta_small"]),
                     Paragraph("<b>Outside Hyderabad</b>", S["cta_small"])],
                    [Paragraph("Meeting at our office", S["cta"]), Paragraph(f"Rs {FEES[('office', 1)]:,}", S["cta"]),
                     Paragraph(f"Rs {FEES[('office', 0)]:,}", S["cta"])],
                    [Paragraph("Visit to your salon", S["cta"]), Paragraph(f"Rs {FEES[('salon_visit', 1)]:,}", S["cta"]),
                     Paragraph(f"Rs {FEES[('salon_visit', 0)]:,}", S["cta"])]],
                   colWidths=[(CW - 26) * f for f in (0.46, 0.27, 0.27)])
        pr.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -2), 0.5, colors.HexColor("#8a4a33")),
                                ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        inner += [pr, Spacer(1, 7),
                  Paragraph(f"<font color='{GOLD_SOFT.hexval()}'><b>Bonus:</b></font> with a paid consultation, "
                            f"{CONSULTANT_NAME} records a professional video about you and your salon — free — to "
                            "boost your presence online.", S["cta"])]
    if not salon:   # compact: one paragraph, so it sits under the next steps
        link = (f' Pick a time: <link href="{escape(booking_link)}" color="{GOLD_SOFT.hexval()}"><u>'
                f'{escape(booking_link)}</u></link>' if booking_link else ' Reply to our WhatsApp message to book a time.')
        inner = [Paragraph(_spaced("Your next conversation"), S["kicker"]), Spacer(1, 2),
                 Paragraph(f"Talk it through with {CONSULTANT_NAME}", S["cta_h"]),
                 Paragraph("Founder of Salon Shastra. One honest call on your plan, your money and the right salon for "
                           "you." + link, S["cta"])]
        return [KeepTogether(_box(inner, MAHOGANY, pad=10, bar=GOLD))]
    return [KeepTogether(_box(inner, MAHOGANY, pad=14, bar=GOLD))]


# -- Salon owner: SWOT report -------------------------------------------------------------------------------------------
def build(report: dict, booking_link: str | None = None) -> bytes:
    """report = {'c': content dict, 'date': '24 Nov 2026', 'version': n}. Returns PDF bytes."""
    c = report["c"]
    S = _styles()
    buf = io.BytesIO()
    scores = c.get("scores") or []
    overall = sum(s for _l, s in scores) / len(scores) if scores else 5.0
    strongest = max(scores, key=lambda s: s[1]) if scores else None
    weakest = min(scores, key=lambda s: s[1]) if scores else None
    opps = c.get("opportunities") or []
    salon = c.get("salon_name") or "Your salon"
    meta = {"title": salon, "short": "Salon Growth Report", "date": report["date"],
            "kicker": "Salon Growth Report  ·  SWOT & action plan",
            "prepared": (f"Prepared exclusively for {c['owner_name']}  ·  " if c.get("owner_name") else "") + report["date"],
            "dial": overall, "dial_label": "Overall business health",
            "inside": [(f"The one area holding {salon} back the most" if weakest else "Where your salon stands", 2),
                       (f"{len(opps) or 'Your'} growth opportunit{'y' if len(opps) == 1 else 'ies'} we found", 3),
                       ("Your first win — what to do this week", 4),
                       (f"How {CONSULTANT_NAME.split(' ')[0]} would take it from here", 4)],
            "stats": [(len(c.get("strengths") or []), "strengths to build on"),
                      (len(opps), "growth opportunities"), (len(c.get("steps") or []), "steps in your plan")],
            "teaser": ("Your biggest opportunity is on page 3",
                       "One change most salons like yours miss — and you can start on it this week."),
            "foot_title": "Prepared by Salon Shastra for the salon owner",
            "foot": "Prepared by Salon Shastra from the owner's own answers. Confidential — for the salon owner only."}
    doc = _doc(buf, f"Salon Growth Report — {_plain(salon)}", "Salon growth report")
    story = [Spacer(1, 1), PageBreak()]                       # page 1 is drawn on the canvas

    # 01 Snapshot ------------------------------------------------------------------------------------------------------
    story.append(SectionHead("01", "Snapshot", "Where your salon stands today"))
    story.append(_box([Paragraph("<b>In short</b>", S["h2"]), Paragraph(_t(c["summary"]), S["lead"])], CREAM, pad=12,
                      bar=GOLD))
    story.append(Spacer(1, 9))
    if strongest and weakest:
        story.append(_tiles([("Your strongest area", f"{strongest[0]} · {strongest[1]}/10", _col(strongest[1])),
                             ("Needs attention first", f"{weakest[0]} · {weakest[1]}/10", _col(weakest[1]))], S))
        story.append(Spacer(1, 3))
        story.append(Paragraph("The plan on page 4 starts with what moves this score fastest.", S["small"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Your salon at a glance", S["h2"]))
    f = _facts(c.get("facts"), S)
    if f:
        story.append(f)
    story.append(Spacer(1, 6))
    story.append(Paragraph("Business health scores", S["h2"]))
    story.append(Paragraph("1 = needs urgent attention, 10 = excellent — from your own answers.", S["small"]))
    story.append(Spacer(1, 4))
    for label, score in scores:
        story.append(ScoreBar(label, score, CW))
    story += _area_section(c, S)
    story.append(PageBreak())

    # 02 SWOT --------------------------------------------------------------------------------------------------------
    story.append(SectionHead("02", "SWOT analysis", "What works, what holds you back"))
    story.append(Paragraph("Strengths and weaknesses are inside the salon; opportunities and threats come from outside. "
                           "Use the strengths to grab the opportunities.", S["small"]))
    story.append(Spacer(1, 8))
    half = (CW - 6 * mm) / 2

    def quad(key, title, letter):
        col, bg = QUAD[key]
        head = (f'<font name="Times-Bold" size="26" color="{col.hexval()}">{letter}</font>'
                f'&nbsp;&nbsp;<font color="{col.hexval()}">{title}</font>')
        return [Paragraph(head, S["quad"]), Spacer(1, 4)] + _bullets(c.get(key), S), bg, col
    q = [quad("strengths", "Strengths", "S"), quad("weaknesses", "Weaknesses", "W"),
         quad("opportunities", "Opportunities", "O"), quad("threats", "Threats", "T")]
    grid = Table([[q[0][0], "", q[1][0]], [q[2][0], "", q[3][0]]], colWidths=[half, 6 * mm, half])
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 10),
          ("RIGHTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 9),
          ("BOTTOMPADDING", (0, 0), (-1, -1), 13), ("LINEBELOW", (0, 0), (-1, 0), 7, IVORY)]
    for (r, col_), (_in, bg, colr) in zip([(0, 0), (0, 2), (1, 0), (1, 2)], q):
        st += [("BACKGROUND", (col_, r), (col_, r), bg), ("LINEABOVE", (col_, r), (col_, r), 3, colr)]
    grid.setStyle(TableStyle(st))
    story.append(grid)
    if opps:
        story.append(Spacer(1, 10))
        story.append(KeepTogether(_box([Paragraph(_spaced("Spotlight"), S["kicker"]),
                                        Paragraph("Your biggest opportunity", S["h2"]),
                                        Paragraph(_t(opps[0]), S["lead"])], GOLD_SOFT, pad=12, bar=GOLD)))
    if any(st.get("area") == "chairpe" for st in c.get("steps") or []):
        story.append(Spacer(1, 7))
        story.append(_box([Paragraph("<font name='Times-Bold' size='11.5' color='%s'>ChairPe</font>&nbsp;&nbsp;"
                                     "Billing, appointments, staff and each chair's profit in one app — <b>14-day free "
                                     "Elite trial</b>, no card. " % MAHOGANY.hexval()
                                     + f'<link href="https://www.chairpe.com" color="{BRAND.hexval()}"><u>www.chairpe.com</u></link>',
                                     S["body"])], colors.white, pad=8, border=LINE, bar=BRAND))
    story.append(PageBreak())

    # 03 Plan ----------------------------------------------------------------------------------------------------------
    story.append(SectionHead("03", "Growth plan", "Your next steps, in order of impact"))
    story.append(Paragraph("Each step says why it matters and how Salon Shastra helps. Start with the first one this week.",
                           S["small"]))
    story.append(Spacer(1, 6))
    story += _steps(c.get("steps"), S)
    story.append(CondPageBreak(85 * mm))
    story.append(Spacer(1, 10))
    story += _cta(S, booking_link, salon=True)
    doc.build(story, onFirstPage=_cover(meta), onLaterPages=_inner(meta))
    return buf.getvalue()


VERDICT_COLOURS = {"ready": ("#1f7a4d", "#eef7f1"), "with_support": ("#9a6200", "#fff6e5"),
                   "not_yet": ("#b3261e", "#fdf0ee")}


# -- Investor: readiness report -----------------------------------------------------------------------------------------
def build_investor(report: dict, booking_link: str | None = None) -> bytes:
    """Investor readiness report: verdict, readiness scores, recommended path, money split, strengths, risks,
    conditions, Vikas's note and next steps. No prices (founder's rule)."""
    c = report["c"]
    S = _styles()
    buf = io.BytesIO()
    scores = c.get("scores") or []
    overall = sum(s for _l, s in scores) / len(scores) if scores else 5.0
    fg, bg = (colors.HexColor(x) for x in VERDICT_COLOURS.get(c.get("verdict"), VERDICT_COLOURS["with_support"]))
    name = c.get("name") or "Investor"
    meta = {"title": name, "short": "Investor Readiness Report", "date": report["date"],
            "kicker": "Investor Readiness Report  ·  Should you invest, and how?",
            "prepared": f"Prepared exclusively for {name}  ·  {report['date']}",
            "dial": overall, "dial_label": "Overall readiness", "chip": (_plain(c.get("verdict_label") or ""), fg),
            "inside": [("Our honest verdict on your plan", 2), ("The salon path that fits you — and how to split "
                                                                "your money", 3),
                       ("The risks to manage before you put money in", 3), ("Your next steps", "3–4")],
            "stats": [(len(c.get("strengths") or []), "things working for you"),
                      (len(c.get("risks") or []), "risks to manage"), (len(c.get("steps") or []), "next steps")],
            "teaser": ("How to split your money is on page 3",
                       "What to spend on the salon and what to keep aside, so the first months stay calm."),
            "foot_title": "Prepared by Salon Shastra for the investor",
            "foot": "Prepared by Salon Shastra from your answers and your call with Vikas Rachakonda. Confidential."}
    doc = _doc(buf, f"Investor readiness — {_plain(name)}", "Investor readiness report")
    story = [Spacer(1, 1), PageBreak()]

    story.append(SectionHead("01", "Snapshot", "Our verdict on your plan"))
    big = ParagraphStyle("verdict", parent=S["body"], fontName="Times-Bold", fontSize=19, leading=23, textColor=fg)
    story.append(_box([Paragraph(_t(c["verdict_label"]), big), Spacer(1, 4), Paragraph(_t(c["verdict_text"]), S["lead"]),
                       Spacer(1, 5), Paragraph(_t(c["summary"]), S["small"])], bg, pad=13, bar=fg))
    story.append(Spacer(1, 9))
    if scores:
        strongest, weakest = max(scores, key=lambda s: s[1]), min(scores, key=lambda s: s[1])
        story.append(_tiles([("Your strongest point", f"{strongest[0]} · {strongest[1]}/10", _col(strongest[1])),
                             ("Work on this first", f"{weakest[0]} · {weakest[1]}/10", _col(weakest[1]))], S))
        story.append(Spacer(1, 6))
    story.append(Paragraph("Your plan at a glance", S["h2"]))
    f = _facts(c.get("facts"), S)
    if f:
        story.append(f)
    story.append(Spacer(1, 6))
    story.append(Paragraph("Readiness scores", S["h2"]))
    story.append(Paragraph("1 = needs work before investing, 10 = strong — from your answers.", S["small"]))
    story.append(Spacer(1, 4))
    for label, score in scores:
        story.append(ScoreBar(label, score, CW))
    story.append(PageBreak())

    story.append(SectionHead("02", "Recommendation", "The path that fits you"))
    rec = [Paragraph(_t(c["path"]), S["h2"]), Paragraph(_t(c["path_text"]), S["lead"])]
    if c.get("split"):
        rec += [Spacer(1, 5), Paragraph(f"<font color='{BRAND_DARK.hexval()}'><b>How to split your money:</b></font> "
                                        + _t(c["split"]), S["body"])]
    story.append(_box(rec, CREAM, pad=12, bar=GOLD))
    if c.get("vikas_note"):
        story.append(Spacer(1, 8))
        story.append(_box([Paragraph(_spaced("A note from Vikas"), S["kicker"]), Spacer(1, 3),
                           Paragraph(f"<i>“{_t(c['vikas_note'])}”</i>", S["lead"]),
                           Paragraph(f"— {CONSULTANT_NAME}, founder, Salon Shastra", S["small"])],
                          GOLD_SOFT, pad=12, bar=GOLD))
    story.append(Spacer(1, 10))
    half = (CW - 6 * mm) / 2

    def col(title, items, key):
        fgc, bgc = QUAD[key]
        return [Paragraph(f'<font color="{fgc.hexval()}">{title}</font>', S["quad"])] + _bullets(items, S), bgc, fgc
    (si, sb, sc), (ri, rb, rc) = col("What works for you", c.get("strengths"), "strengths"), \
        col("Risks to manage", c.get("risks") or ["No major risks in your answers — we still check every salon with "
                                                  "you before you sign."], "weaknesses")
    grid = Table([[si, "", ri]], colWidths=[half, 6 * mm, half])
    grid.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (0, 0), sb),
                              ("BACKGROUND", (2, 0), (2, 0), rb), ("LINEABOVE", (0, 0), (0, 0), 3, sc),
                              ("LINEABOVE", (2, 0), (2, 0), 3, rc), ("LEFTPADDING", (0, 0), (-1, -1), 10),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 9),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 13)]))
    story.append(grid)
    if c.get("conditions"):
        story.append(Spacer(1, 9))
        story.append(KeepTogether(_box([Paragraph("Before you put money in", S["h2"])] + _bullets(c["conditions"], S),
                                       colors.white, pad=11, border=LINE, bar=BRAND)))
    story.append(CondPageBreak(60 * mm))
    story.append(Spacer(1, 10))
    story.append(SectionHead("03", "Next steps", "What to do, in order"))
    story += _steps(c.get("steps"), S, with_how=False)
    story += _area_section(c, S)
    story.append(Spacer(1, 6))
    story += _cta(S, booking_link, salon=False)
    doc.build(story, onFirstPage=_cover(meta), onLaterPages=_inner(meta))
    return buf.getvalue()
