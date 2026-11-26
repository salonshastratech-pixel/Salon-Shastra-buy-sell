"""QA: render sample salon (SWOT) and investor reports to PDF + PNG pages for a visual check.

  python scripts/qa/report_preview.py <out-dir>        (PNG pages need PyMuPDF; the PDFs are written regardless)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_investor import STRONG  # noqa: E402
from test_pilot import FULL  # noqa: E402

from teamos.domain import investor as I  # noqa: E402
from teamos.domain import pilot as P  # noqa: E402
from teamos.domain import swot_pdf  # noqa: E402


def main():
    out = Path(sys.argv[1])
    answers, _ = P.parse_answers(FULL, lenient=True)
    c = P.build_swot(answers, ["digital_marketing", "chairpe"])
    c["area"] = {"place": "Kondapur, Hyderabad", "radius_km": 2,
                 "counts": {"salon": 42, "beauty": 18, "nails": 6, "barber": 23, "spa": 4},
                 "tips": ["42 hair salons within 2 km — stand out with a clear speciality and Google reviews.",
                          "Few nail studios nearby: nail services could be an easy add-on."]}
    (out / "swot_new.pdf").write_bytes(swot_pdf.build({"c": c, "date": "26 Nov 2026"},
                                                      "https://team.salonshastra.com/s/abc/book"))
    ia, _ = I.parse_answers(STRONG, lenient=True)
    ic = I.build_report(ia)
    ic["vikas_note"] = "You have the money and the time. Buy a running unisex salon with a strong team."
    (out / "investor_new.pdf").write_bytes(swot_pdf.build_investor({"c": ic, "date": "26 Nov 2026"},
                                                                   "https://team.salonshastra.com/s/xyz/book"))
    try:
        import fitz
    except ImportError:
        print("PDFs written (install PyMuPDF for PNG pages)")
        return
    for n in ("swot_new", "investor_new"):
        d = fitz.open(out / f"{n}.pdf")
        print(n, d.page_count, "pages")
        for i, pg in enumerate(d):
            pg.get_pixmap(dpi=75).save(str(out / f"{n}_{i + 1}.png"))


if __name__ == "__main__":
    main()
