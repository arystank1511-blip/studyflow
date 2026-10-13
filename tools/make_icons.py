"""Draw the StudyFlow mark as PNG icons for iOS and Android home screens.

Run once after changing the logo:  python tools/make_icons.py   (needs Pillow; not a runtime dependency)
The shapes match static/brand-mark.svg: blue tile, white wave, amber dot.
"""
from pathlib import Path

from PIL import Image, ImageDraw

BLUE = (46, 91, 255, 255)
AMBER = (255, 176, 32, 255)
WHITE = (255, 255, 255, 255)
STATIC = Path(__file__).resolve().parent.parent / "static"


def cubic(p0, p1, p2, p3, steps=40):
    for i in range(steps + 1):
        t = i / steps
        yield tuple((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t ** 2 * c + t ** 3 * d
                    for a, b, c, d in zip(p0, p1, p2, p3))


def draw(size, rounded=True, padding=0.0):
    scale = 8  # Supersample, then shrink, for smooth edges.
    big = size * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0) if rounded else BLUE)
    canvas = ImageDraw.Draw(image)
    if rounded:
        canvas.rounded_rectangle((0, 0, big - 1, big - 1), radius=big * 12 / 40, fill=BLUE)
    inner = big * (1 - 2 * padding)
    offset = big * padding
    unit = inner / 40

    def point(x, y):
        return (offset + x * unit, offset + y * unit)

    # Path from the SVG: M9 24 c3.5-6 7 3 11-3 s6.5-5 11-2
    first = list(cubic(point(9, 24), point(12.5, 18), point(16, 27), point(20, 21)))
    second = list(cubic(point(20, 21), point(24, 15), point(26.5, 16), point(31, 19)))
    width = round(3.2 * unit)
    line = first + second[1:]
    canvas.line(line, fill=WHITE, width=width, joint="curve")
    for end in (line[0], line[-1]):
        canvas.ellipse((end[0] - width / 2, end[1] - width / 2, end[0] + width / 2, end[1] + width / 2), fill=WHITE)
    cx, cy = point(29, 12.5)
    radius = 3 * unit
    canvas.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=AMBER)
    return image.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    # iOS draws its own rounded corners and ignores transparency, so the tile is square.
    draw(180, rounded=False, padding=0.08).convert("RGB").save(STATIC / "apple-touch-icon.png", optimize=True)
    draw(192).save(STATIC / "icon-192.png", optimize=True)
    draw(512).save(STATIC / "icon-512.png", optimize=True)
    # Android "maskable" icons need a safe zone, so the mark sits inside a full-bleed tile.
    draw(512, rounded=False, padding=0.12).save(STATIC / "icon-maskable-512.png", optimize=True)
    print("icons written")
