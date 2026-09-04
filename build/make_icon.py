"""Regenerate build/app.ico (dev tool, not shipped; needs Pillow).

A blue rounded square with a white branch glyph — two nodes joined by a
curve — so the app is told apart from the TSMIS Exporter's "TS" tile at a
glance. Run from the repo root: python build\\make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "app.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size):
    s = size * 8                                          # draw big, downsample
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=s // 5, fill=(53, 106, 173, 255))
    w = max(s // 14, 1)
    node = s // 9
    # trunk on the left, a branch curving off it to a node at the top-right
    x0, ytop, ybot = s * 0.33, s * 0.22, s * 0.78
    d.line((x0, ytop, x0, ybot), fill="white", width=w)
    d.arc((x0 - s * 0.02, s * 0.22, x0 + s * 0.62, s * 0.72), start=180, end=270,
          fill="white", width=w)
    d.line((x0 + s * 0.30, s * 0.22, x0 + s * 0.36, s * 0.22), fill="white", width=w)
    for cx, cy in ((x0, ytop), (x0, ybot), (x0 + s * 0.36, ytop)):
        d.ellipse((cx - node, cy - node, cx + node, cy + node), fill="white")
        d.ellipse((cx - node + w, cy - node + w, cx + node - w, cy + node - w),
                  fill=(53, 106, 173, 255))
    return im.resize((size, size), Image.LANCZOS)


def main():
    frames = [render(sz) for sz in SIZES]
    frames[-1].save(OUT, format="ICO", sizes=[(sz, sz) for sz in SIZES],
                    append_images=frames[:-1])
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
