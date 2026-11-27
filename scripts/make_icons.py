"""Build the app / browser icons from the hat in the Salon Shastra logo (static/brand/logo.png).

  python scripts/make_icons.py
Writes static/icons/: icon-192.png, icon-512.png (home screen), maskable-512.png (Android adaptive icons — the hat
kept inside the safe circle), apple-touch-icon.png (iPhone, 180 px, solid background) and favicon-32.png,
favicon-16.png, favicon.ico (browser tab).
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / "teamos" / "static"
IVORY = (251, 247, 241, 255)        # the app's warm background


def hat() -> Image.Image:
    logo = Image.open(ROOT / "brand" / "logo.png").convert("RGBA")
    part = logo.crop((0, 0, 372, logo.height))          # the emblem, left of the lettering
    return part.crop(part.split()[3].point(lambda v: 255 if v > 20 else 0).getbbox())


def place(h: Image.Image, size: int, fill: float, bg=IVORY) -> Image.Image:
    """The hat centred on a square canvas, its longer side `fill` of the canvas."""
    canvas = Image.new("RGBA", (size * 4, size * 4), bg)          # draw large, then shrink once: crisp edges
    scale = fill * size * 4 / max(h.size)
    hh = h.resize((round(h.width * scale), round(h.height * scale)), Image.LANCZOS)
    canvas.alpha_composite(hh, ((canvas.width - hh.width) // 2, (canvas.height - hh.height) // 2))
    return canvas.resize((size, size), Image.LANCZOS)


def main():
    h = hat()
    out = ROOT / "icons"
    out.mkdir(exist_ok=True)
    place(h, 512, 0.78).save(out / "icon-512.png", optimize=True)
    place(h, 192, 0.78).save(out / "icon-192.png", optimize=True)
    place(h, 512, 0.62).save(out / "maskable-512.png", optimize=True)          # safe zone = inner 80% circle
    place(h, 180, 0.74).convert("RGB").save(out / "apple-touch-icon.png", optimize=True)
    clear = (0, 0, 0, 0)
    place(h, 32, 0.98, clear).save(out / "favicon-32.png", optimize=True)
    place(h, 16, 1.0, clear).save(out / "favicon-16.png", optimize=True)
    place(h, 64, 0.98, clear).save(out / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    print("icons written to", out)


if __name__ == "__main__":
    main()
