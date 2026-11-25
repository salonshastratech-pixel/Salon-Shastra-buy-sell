"""Branded SWOT report PDF for a salon owner (ReportLab, built-in fonts so it renders the same on any server).

Pages: 1) cover band, summary, salon at a glance, health scores  2) SWOT in four coloured boxes
3) next steps in order + how to talk to Vikas Rachakonda, consultation prices and the free-video bonus.
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
from reportlab.platypus import (CondPageBreak, Flowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from .pilot import CONSULTANT_NAME, FEES

BRAND = colors.HexColor("#c04000")
BRAND_DARK = colors.HexColor("#893815")
TINT = colors.HexColor("#fff6f1")
SOFT = colors.HexColor("#ffe3d5")
INK = colors.HexColor("#2b2118")
MUTED = colors.HexColor("#76695f")
QUAD = {  # title colour, background
    "strengths": (colors.HexColor("#1f7a4d"), colors.HexColor("#eaf6ef")),
    "weaknesses": (colors.HexColor("#b3261e"), colors.HexColor("#fdecea")),
    "opportunities": (colors.HexColor("#1d5fa8"), colors.HexColor("#e9f1fb")),
    "threats": (colors.HexColor("#9a6200"), colors.HexColor("#fff4de")),
}
W, H = A4
M = 16 * mm


def _t(s) -> str:
    """Escape for Paragraph markup; built-in fonts have no rupee sign, so write 'Rs'."""
    return escape(str(s or "")).replace("₹", "Rs ").replace("  ", " ")


def _styles():
    base = dict(fontName="Helvetica", fontSize=10, leading=14, textColor=INK)
    return {
        "h1": ParagraphStyle("h1", **{**base, "fontName": "Helvetica-Bold", "fontSize": 17, "leading": 22,
                                      "textColor": BRAND_DARK, "spaceAfter": 6}),
        "h2": ParagraphStyle("h2", **{**base, "fontName": "Helvetica-Bold", "fontSize": 12.5, "leading": 16,
                                      "textColor": BRAND_DARK, "spaceBefore": 8, "spaceAfter": 6}),
        "body": ParagraphStyle("body", **base),
        "small": ParagraphStyle("small", **{**base, "fontSize": 8.5, "leading": 11.5, "textColor": MUTED}),
        "bullet": ParagraphStyle("bullet", **{**base, "fontSize": 9.6, "leading": 13, "leftIndent": 10,
                                              "bulletIndent": 0, "spaceAfter": 3}),
        "label": ParagraphStyle("label", **{**base, "fontSize": 8, "leading": 10, "textColor": MUTED}),
        "value": ParagraphStyle("value", **{**base, "fontName": "Helvetica-Bold", "fontSize": 11, "leading": 14}),
        "quad": ParagraphStyle("quad", **{**base, "fontName": "Helvetica-Bold", "fontSize": 12, "leading": 24,
                                        "spaceAfter": 2}),
        "center": ParagraphStyle("center", **{**base, "alignment": TA_CENTER}),
        "white": ParagraphStyle("white", **{**base, "textColor": colors.white, "fontName": "Helvetica-Bold",
                                            "fontSize": 12, "leading": 12, "alignment": TA_CENTER}),
        "how": ParagraphStyle("how", **{**base, "fontSize": 9.6, "leading": 13}),
    }


class ScoreBar(Flowable):
    def __init__(self, label, score, width):
        super().__init__()
        self.label, self.score, self.width, self.height = label, int(score), width, 9 * mm

    def draw(self):
        c = self.canv
        c.setFont("Helvetica", 9.5)
        c.setFillColor(INK)
        c.drawString(0, 3 * mm, self.label)
        x0, bw = 52 * mm, self.width - 52 * mm - 14 * mm
        c.setFillColor(colors.HexColor("#efe7e1"))
        c.roundRect(x0, 2 * mm, bw, 4 * mm, 2 * mm, stroke=0, fill=1)
        col = (colors.HexColor("#1f7a4d") if self.score >= 7 else colors.HexColor("#d08a00") if self.score >= 5
               else colors.HexColor("#b3261e"))
        c.setFillColor(col)
        c.roundRect(x0, 2 * mm, max(4 * mm, bw * self.score / 10), 4 * mm, 2 * mm, stroke=0, fill=1)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 10)
        c.drawRightString(self.width, 2.6 * mm, f"{self.score}/10")


def _page(report, logo):
    def draw(canv, doc):
        logo_ok = logo
        canv.saveState()
        if doc.page == 1:
            canv.setFillColor(BRAND)
            canv.rect(0, H - 52 * mm, W, 52 * mm, stroke=0, fill=1)
            canv.setFillColor(BRAND_DARK)
            canv.rect(0, H - 54 * mm, W, 2 * mm, stroke=0, fill=1)
            if logo_ok:
                try:
                    canv.drawImage(logo_ok, M, H - 22 * mm, width=38 * mm, height=12 * mm, preserveAspectRatio=True,
                                   mask="auto")
                except Exception:  # noqa: BLE001 — a bad logo file must not stop the report
                    logo_ok = None
            if not logo_ok:
                canv.setFillColor(colors.white)
                canv.setFont("Helvetica-Bold", 15)
                canv.drawString(M, H - 18 * mm, "SALON SHASTRA")
            canv.setFillColor(colors.white)
            canv.setFont("Helvetica", 10)
            canv.drawRightString(W - M, H - 18 * mm, report.get("head", "Salon Growth Report") + " · " + report["date"])
            canv.setFont("Helvetica-Bold", 22)
            canv.drawString(M, H - 35 * mm, _plain(report.get("title") or report["c"].get("salon_name"))[:48])
            canv.setFont("Helvetica", 11.5)
            sub = report.get("sub") or "SWOT analysis and next steps"
            if report["c"].get("owner_name") and not report.get("sub"):
                sub = f"Prepared for {_plain(report['c']['owner_name'])} · " + sub
            canv.drawString(M, H - 44 * mm, sub[:90])
        else:
            canv.setFillColor(BRAND)
            canv.rect(0, H - 9 * mm, W, 9 * mm, stroke=0, fill=1)
            canv.setFillColor(colors.white)
            canv.setFont("Helvetica-Bold", 9)
            canv.drawString(M, H - 6 * mm, "SALON SHASTRA")
            canv.setFont("Helvetica", 9)
            canv.drawRightString(W - M, H - 6 * mm, _plain(report.get("title") or report["c"].get("salon_name"))[:60]
                                 + " · " + report.get("short", "SWOT report"))
        canv.setFillColor(MUTED)
        canv.setFont("Helvetica", 7.5)
        canv.drawString(M, 9 * mm, report.get("foot") or "Prepared by Salon Shastra from the owner's own answers. "
                                                         "Confidential — for the salon owner only.")
        canv.drawRightString(W - M, 9 * mm, f"Page {doc.page}")
        canv.restoreState()
    return draw


def _plain(s) -> str:
    return str(s or "").replace("₹", "Rs ")


def _box(flowables, bg, pad=8, border=None):
    t = Table([[flowables]], colWidths=[W - 2 * M])
    st = [("BACKGROUND", (0, 0), (-1, -1), bg), ("LEFTPADDING", (0, 0), (-1, -1), pad),
          ("RIGHTPADDING", (0, 0), (-1, -1), pad), ("TOPPADDING", (0, 0), (-1, -1), pad),
          ("BOTTOMPADDING", (0, 0), (-1, -1), pad), ("ROUNDEDCORNERS", [6, 6, 6, 6])]
    if border:
        st.append(("BOX", (0, 0), (-1, -1), 0.8, border))
    t.setStyle(TableStyle(st))
    return t


def _area_section(c: dict, S) -> list:
    """'Your area' — nearby salons from OpenStreetMap, with what it means (only when the look-up has arrived)."""
    a = c.get("area")
    if not a:
        return []
    labels = [("salon", "Hair & unisex salons"), ("beauty", "Beauty parlours"), ("nails", "Nail studios"),
              ("barber", "Barber / men's shops"), ("spa", "Spas & massage")]
    cells = [[Paragraph(f"<b>{a['counts'].get(k, 0)}</b><br/><font size='8'>{escape(l)}</font>", S["body"])
              for k, l in labels]]
    tbl = Table(cells, colWidths=[(W - 2 * M - 16) / 5] * 5)
    tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.white), ("BOX", (0, 0), (-1, -1), 0.6, SOFT),
                             ("INNERGRID", (0, 0), (-1, -1), 0.6, SOFT), ("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    inner = [Paragraph(f"Your area: {escape(a['place'])} (within {a['radius_km']:g} km)", S["h2"]), tbl,
             Spacer(1, 5)]
    inner += [Paragraph(_t(x), S["bullet"], bulletText="•") for x in a.get("tips") or []]
    inner.append(Paragraph("Source: OpenStreetMap — shops volunteers have mapped, so real numbers are usually higher. "
                           "A guide, not a census; confirm with a visit.", S["small"]))
    return [Spacer(1, 8), KeepTogether(_box(inner, TINT, pad=10, border=SOFT)), Spacer(1, 6)]


def build(report: dict, booking_link: str | None = None) -> bytes:
    """report = {'c': content dict, 'date': '24 Nov 2026', 'version': n}. Returns PDF bytes."""
    c = report["c"]
    S = _styles()
    buf = io.BytesIO()
    logo = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "brand", "logo.png")
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=M, rightMargin=M, topMargin=16 * mm, bottomMargin=18 * mm,
                            title=f"SWOT report — {_plain(c['salon_name'])}", author="Salon Shastra",
                            subject="Salon growth report")
    story = [Spacer(1, 42 * mm)]
    # Page 1 -------------------------------------------------------------------------------------------------
    story.append(_box([Paragraph("<b>In short</b>", S["h2"]), Paragraph(_t(c["summary"]), S["body"])], TINT,
                      border=SOFT))
    story.append(Spacer(1, 8))
    story.append(Paragraph("Your salon at a glance", S["h2"]))
    facts = c.get("facts") or []
    cells = [[Paragraph(_t(k), S["label"]), Paragraph(_t(v), S["value"])] for k, v in facts]
    rows = []
    for i in range(0, len(cells), 4):
        chunk = cells[i:i + 4] + [[Paragraph("", S["label"]), Paragraph("", S["value"])]] * (4 - len(cells[i:i + 4]))
        rows.append([cell for cell in chunk])
    if rows:
        cw = (W - 2 * M) / 4
        t = Table([[x for x in r] for r in rows], colWidths=[cw] * 4)
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                               ("BOX", (0, 0), (-1, -1), 0.6, SOFT), ("INNERGRID", (0, 0), (-1, -1), 0.6, SOFT),
                               ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        story.append(t)
    story.append(Spacer(1, 6))
    story.append(Paragraph("Business health scores", S["h2"]))
    story.append(Paragraph("1 = needs urgent attention, 10 = excellent. Based on your answers.", S["small"]))
    story.append(Spacer(1, 4))
    for label, score in c.get("scores") or []:
        story.append(ScoreBar(label, score, W - 2 * M))
    story.append(PageBreak())

    # Page 2: SWOT -------------------------------------------------------------------------------------------
    story.append(Paragraph("SWOT analysis", S["h1"]))
    story.append(Paragraph("What is working, what is holding the salon back, where the growth is, and what to "
                           "watch out for.", S["small"]))
    story.append(Spacer(1, 8))
    half = (W - 2 * M - 6 * mm) / 2

    def quad(key, title, letter):
        col, bg = QUAD[key]
        items = c.get(key) or ["—"]
        inner = [Paragraph(f'<font color="{col.hexval()}" size="20"><b>{letter}</b></font>&nbsp;&nbsp;'
                           f'<font color="{col.hexval()}">{title}</font>', S["quad"]), Spacer(1, 4)]
        inner += [Paragraph(_t(x), S["bullet"], bulletText="•") for x in items]
        return inner, bg
    (s_in, s_bg), (w_in, w_bg) = quad("strengths", "Strengths", "S"), quad("weaknesses", "Weaknesses", "W")
    (o_in, o_bg), (t_in, t_bg) = quad("opportunities", "Opportunities", "O"), quad("threats", "Threats", "T")
    grid = Table([[s_in, "", w_in], [o_in, "", t_in]], colWidths=[half, 6 * mm, half])
    grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (0, 0), s_bg), ("BACKGROUND", (2, 0), (2, 0), w_bg),
        ("BACKGROUND", (0, 1), (0, 1), o_bg), ("BACKGROUND", (2, 1), (2, 1), t_bg),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LINEBELOW", (0, 0), (-1, 0), 6, colors.white)]))
    story.append(grid)
    story.append(PageBreak())

    # Page 3: your area, next steps + CTA ---------------------------------------------------------------------
    story += _area_section(c, S)
    story.append(Paragraph("Your next steps", S["h1"]))
    story.append(Paragraph("In order of impact. Each step says why it matters and how Salon Shastra can help.",
                           S["small"]))
    story.append(Spacer(1, 8))
    for i, st in enumerate(c.get("steps") or [], 1):
        num = Table([[Paragraph(f"<b>{i}</b>", S["white"])]], colWidths=[9 * mm], rowHeights=[9 * mm])
        num.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), BRAND), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                                 ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ROUNDEDCORNERS", [4, 4, 4, 4]),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        body = [Paragraph(f"<b>{_t(st['title'])}</b>&nbsp;&nbsp;<font color='{BRAND.hexval()}' size='8.5'>"
                          f"{_t(st.get('when'))}</font>", S["body"])]
        if st.get("why"):
            body.append(Paragraph(_t(st["why"]), S["small"]))
        if st.get("how"):
            body.append(Paragraph(f"<font color='{BRAND_DARK.hexval()}'><b>How we help:</b></font> {_t(st['how'])}",
                                  S["how"]))
        row = Table([[num, body]], colWidths=[12 * mm, W - 2 * M - 12 * mm])
        row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.5, SOFT),
                                 ("BOTTOMPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 5),
                                 ("LEFTPADDING", (0, 0), (0, 0), 0)]))
        story.append(KeepTogether(row))
    story.append(CondPageBreak(80 * mm))
    story.append(Spacer(1, 10))
    cta = [Paragraph(f"Talk to {CONSULTANT_NAME}", S["h2"]),
           Paragraph("Discuss this report with the founder of Salon Shastra and get a clear plan for your salon."
                     + (" Pick a time that suits you:" if booking_link else " Reply to our message to book a time."),
                     S["body"])]
    if booking_link:
        cta.append(Paragraph(f'<link href="{escape(booking_link)}" color="{BRAND.hexval()}"><u>'
                             f'{escape(booking_link)}</u></link>', S["body"]))
    cta.append(Spacer(1, 6))
    prices = Table([
        [Paragraph("<b>Consultation</b>", S["body"]), Paragraph("<b>Hyderabad</b>", S["body"]),
         Paragraph("<b>Outside Hyderabad</b>", S["body"])],
        [Paragraph("Meeting at our office", S["body"]), Paragraph(f"Rs {FEES[('office', 1)]:,}", S["body"]),
         Paragraph(f"Rs {FEES[('office', 0)]:,}", S["body"])],
        [Paragraph("Visit to your salon", S["body"]), Paragraph(f"Rs {FEES[('salon_visit', 1)]:,}", S["body"]),
         Paragraph(f"Rs {FEES[('salon_visit', 0)]:,}", S["body"])],
    ], colWidths=[(W - 2 * M - 16) * f for f in (0.46, 0.27, 0.27)])
    prices.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), SOFT), ("BOX", (0, 0), (-1, -1), 0.6, SOFT),
                                ("INNERGRID", (0, 0), (-1, -1), 0.6, SOFT),
                                ("BACKGROUND", (0, 1), (-1, -1), colors.white)]))
    cta.append(prices)
    cta.append(Spacer(1, 6))
    cta.append(Paragraph(f"<b>Bonus:</b> with a paid consultation, {CONSULTANT_NAME} records a professional video "
                         "about you and your salon — free — to boost your presence online.", S["body"]))
    story.append(KeepTogether(_box(cta, TINT, pad=11, border=BRAND)))
    if any(st.get("area") == "chairpe" for st in c.get("steps") or []):
        story.append(Spacer(1, 8))
        story.append(KeepTogether(_box([
            Paragraph("ChairPe — run your salon, understand your business", S["h2"]),
            Paragraph("Salon Shastra's salon operating system: billing &amp; UPI payments with GST, appointments and "
                      "online booking, staff and commissions, stock, memberships, bills on WhatsApp, win-back of "
                      "customers who stopped coming, and each chair's profit (ChairPe Core).", S["body"]),
            Paragraph("<b>Try the Elite plan free for 14 days</b> — no card needed. Plans from Rs 599/month. "
                      f'<link href="https://www.chairpe.com" color="{BRAND.hexval()}"><u>www.chairpe.com</u></link>',
                      S["body"])], colors.white, pad=11, border=SOFT)))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Salon Shastra · www.salonshastra.com · +91 86868 78090 · support@salonshastra.com",
                           S["small"]))
    doc.build(story, onFirstPage=_page(report, logo if os.path.exists(logo) else None),
              onLaterPages=_page(report, None))
    return buf.getvalue()


VERDICT_COLOURS = {"ready": ("#1f7a4d", "#eaf6ef"), "with_support": ("#9a6200", "#fff4de"),
                   "not_yet": ("#b3261e", "#fdecea")}


def build_investor(report: dict, booking_link: str | None = None) -> bytes:
    """Investor readiness report: verdict, readiness scores, recommended path, money split, strengths, risks,
    conditions, Vikas's note and next steps. No prices (founder's rule)."""
    c = report["c"]
    S = _styles()
    buf = io.BytesIO()
    logo = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "brand", "logo.png")
    meta = {"c": c, "date": report["date"], "head": "Investor Readiness Report", "title": c.get("name") or "Investor",
            "sub": "Should you invest, and how? Our assessment and next steps", "short": "Investor readiness",
            "foot": "Prepared by Salon Shastra from your answers and your call with Vikas Rachakonda. Confidential."}
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=M, rightMargin=M, topMargin=16 * mm, bottomMargin=18 * mm,
                            title=f"Investor readiness — {_plain(meta['title'])}", author="Salon Shastra")
    fg, bg = (colors.HexColor(x) for x in VERDICT_COLOURS.get(c.get("verdict"), VERDICT_COLOURS["with_support"]))
    story = [Spacer(1, 42 * mm)]
    big = ParagraphStyle("verdict", parent=S["body"], fontName="Helvetica-Bold", fontSize=17, leading=22,
                         textColor=fg)
    story.append(_box([Paragraph(_t(c["verdict_label"]), big), Spacer(1, 4), Paragraph(_t(c["verdict_text"]), S["body"]),
                       Spacer(1, 4), Paragraph(_t(c["summary"]), S["small"])], bg, pad=12, border=fg))
    story.append(Spacer(1, 8))
    story.append(Paragraph("Your plan at a glance", S["h2"]))
    cells = [[Paragraph(_t(k), S["label"]), Paragraph(_t(v), S["value"])] for k, v in c.get("facts") or []]
    rows = []
    for i in range(0, len(cells), 4):
        chunk = cells[i:i + 4] + [[Paragraph("", S["label"]), Paragraph("", S["value"])]] * (4 - len(cells[i:i + 4]))
        rows.append(chunk)
    if rows:
        t = Table(rows, colWidths=[(W - 2 * M) / 4] * 4)
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.6, SOFT),
                               ("INNERGRID", (0, 0), (-1, -1), 0.6, SOFT), ("TOPPADDING", (0, 0), (-1, -1), 6),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        story.append(t)
    story.append(Spacer(1, 6))
    story.append(Paragraph("Readiness scores", S["h2"]))
    story.append(Paragraph("1 = needs work before investing, 10 = strong. Based on your answers.", S["small"]))
    story.append(Spacer(1, 4))
    for label, score in c.get("scores") or []:
        story.append(ScoreBar(label, score, W - 2 * M))
    story.append(PageBreak())

    story.append(Paragraph("Our recommendation", S["h1"]))
    rec = [Paragraph(f"<b>{_t(c['path'])}</b>", S["h2"]), Paragraph(_t(c["path_text"]), S["body"])]
    if c.get("split"):
        rec += [Spacer(1, 4), Paragraph("<b>How to split your money:</b> " + _t(c["split"]), S["body"])]
    story.append(_box(rec, TINT, pad=11, border=SOFT))
    if c.get("vikas_note"):
        story.append(Spacer(1, 8))
        story.append(_box([Paragraph("<b>A note from Vikas Rachakonda</b>", S["body"]),
                           Paragraph(_t(c["vikas_note"]), S["body"])], colors.white, pad=11, border=BRAND))
    story.append(Spacer(1, 10))
    half = (W - 2 * M - 6 * mm) / 2

    def col(title, items, key):
        fgc, bgc = QUAD[key]
        inner = [Paragraph(f'<font color="{fgc.hexval()}"><b>{title}</b></font>', S["quad"])]
        inner += [Paragraph(_t(x), S["bullet"], bulletText="•") for x in (items or ["—"])]
        return inner, bgc
    si, sb = col("What works for you", c.get("strengths"), "strengths")
    ri, rb = col("Risks to manage", c.get("risks"), "weaknesses")
    grid = Table([[si, "", ri]], colWidths=[half, 6 * mm, half])
    grid.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (0, 0), sb),
                              ("BACKGROUND", (2, 0), (2, 0), rb), ("LEFTPADDING", (0, 0), (-1, -1), 9),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 9),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 12)]))
    story.append(grid)
    if c.get("conditions"):
        story.append(Spacer(1, 8))
        story.append(Paragraph("Before you put money in", S["h2"]))
        for x in c["conditions"]:
            story.append(Paragraph(_t(x), S["bullet"], bulletText="•"))
    story.append(CondPageBreak(60 * mm))
    story.append(Spacer(1, 6))
    story += _area_section(c, S)
    story.append(Paragraph("Your next steps", S["h2"]))
    for i, st in enumerate(c.get("steps") or [], 1):
        num = Table([[Paragraph(f"<b>{i}</b>", S["white"])]], colWidths=[9 * mm], rowHeights=[9 * mm])
        num.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), BRAND), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                 ("ROUNDEDCORNERS", [4, 4, 4, 4]), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        body = [Paragraph(f"<b>{_t(st['title'])}</b>&nbsp;&nbsp;<font color='{BRAND.hexval()}' size='8.5'>"
                          f"{_t(st.get('when'))}</font>", S["body"])]
        if st.get("why"):
            body.append(Paragraph(_t(st["why"]), S["small"]))
        row = Table([[num, body]], colWidths=[12 * mm, W - 2 * M - 12 * mm])
        row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.5, SOFT),
                                 ("BOTTOMPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 5),
                                 ("LEFTPADDING", (0, 0), (0, 0), 0)]))
        story.append(KeepTogether(row))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Salon Shastra · www.salonshastra.com · +91 86868 78090 · support@salonshastra.com",
                           S["small"]))
    doc.build(story, onFirstPage=_page(meta, logo if os.path.exists(logo) else None), onLaterPages=_page(meta, None))
    return buf.getvalue()
