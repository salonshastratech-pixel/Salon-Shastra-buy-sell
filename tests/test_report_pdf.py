"""The premium report PDFs: logo embedded, cover + numbered sections, both reports build from real answers."""
import pathlib

from test_investor import STRONG
from test_pilot import FULL

from teamos.domain import investor as I
from teamos.domain import pilot as P
from teamos.domain import swot_pdf


def test_salon_and_investor_reports_build_with_logo():
    assert pathlib.Path(swot_pdf.LOGO).exists()                     # the real logo ships with the app
    a, _ = P.parse_answers(FULL, lenient=True)
    c = P.build_swot(a, ["chairpe"])
    pdf = swot_pdf.build({"c": c, "date": "26 Nov 2026"}, "https://x.test/s/abc/book")
    assert pdf[:4] == b"%PDF" and b"/Subtype /Image" in pdf and len(pdf) > 30000
    assert b"Salon Growth Report" in pdf                            # document title
    ia, _ = I.parse_answers(STRONG, lenient=True)
    ic = I.build_report(ia)
    ipdf = swot_pdf.build_investor({"c": ic, "date": "26 Nov 2026"}, None)
    assert ipdf[:4] == b"%PDF" and b"/Subtype /Image" in ipdf and b"Investor readiness" in ipdf
    ic["risks"], ic["steps"] = [], []                               # empty sections still render
    assert swot_pdf.build_investor({"c": ic, "date": "26 Nov 2026"}, None)[:4] == b"%PDF"
