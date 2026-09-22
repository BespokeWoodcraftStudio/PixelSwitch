#!/usr/bin/env python3
"""Draw the PixelSwitch app icon at every size the asset catalog needs.

The mark is a switch: a square-cornered track with the Pixel Ventures unit
square as its knob, in the Pixel Ventures palette (pixelventures
brand/tokens.json: dark ground #0D0D0C, hairline #2A2A28, ink #F7F7F5,
accent #00FF66). The rounded body is macOS's standard icon shape, not part
of the mark; the mark itself has no rounded corners.

Run from the repo root:  python3 Tools/icon/make_icon.py
Needs Pillow.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
ICONSET = ROOT / "PixelSwitch/Resources/Assets.xcassets/AppIcon.appiconset"
PREVIEW = ROOT / "assets/PixelSwitch-icon.png"

GROUND_TOP = (0x1A, 0x1A, 0x18)
GROUND_BOTTOM = (0x0D, 0x0D, 0x0C)
HAIRLINE = (0x2A, 0x2A, 0x28)
INK = (0xF7, 0xF7, 0xF5)
ACCENT = (0x00, 0xFF, 0x66)

# Geometry on Apple's 1024 icon grid: an 824 px body centred with 100 px margins.
BODY = (100, 100, 924, 924)
TRACK = (212, 362, 812, 662)  # 600 x 300


def squircle(box, n=5.0, steps=720):
    """Superellipse |x|^n + |y|^n = 1, close to macOS's continuous-corner body."""
    x0, y0, x1, y1 = box
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        pts.append((cx + rx * math.copysign(abs(c) ** (2 / n), c),
                    cy + ry * math.copysign(abs(s) ** (2 / n), s)))
    return pts


def draw(size):
    ss = 4  # supersample, then downscale for clean edges
    w = size * ss
    k = w / 1024
    # Small sizes get a heavier track so it survives downscaling.
    stroke, gap = (72, 24) if size <= 32 else (44, 30)

    img = Image.new("RGBA", (w, w), (0, 0, 0, 0))

    # Body: a neutral vertical tone step clipped to the squircle.
    grad = Image.new("RGBA", (w, w))
    gd = ImageDraw.Draw(grad)
    for y in range(w):
        t = y / (w - 1)
        tone = tuple(round(a + (b - a) * t) for a, b in zip(GROUND_TOP, GROUND_BOTTOM))
        gd.line([(0, y), (w, y)], fill=tone + (255,))
    mask = Image.new("L", (w, w), 0)
    body = [(x * k, y * k) for x, y in squircle(BODY)]
    ImageDraw.Draw(mask).polygon(body, fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    d.line(body + [body[0]], fill=HAIRLINE + (255,), width=max(1, round(4 * k)))

    # Track: square-cornered outline.
    x0, y0, x1, y1 = TRACK
    for i in range(round(stroke * k)):
        d.rectangle([x0 * k + i, y0 * k + i, x1 * k - 1 - i, y1 * k - 1 - i], outline=INK + (255,))

    # Knob: the unit square, switched to the right ("on").
    inner_h = (y1 - y0) - 2 * stroke
    side = inner_h - 2 * gap
    kx1 = x1 - stroke - gap
    ky0 = y0 + stroke + gap
    d.rectangle([(kx1 - side) * k, ky0 * k, kx1 * k - 1, (ky0 + side) * k - 1], fill=ACCENT + (255,))

    return img.resize((size, size), Image.LANCZOS)


def draw_mark(width, height):
    """The switch mark on its own, white on transparent, for the menu bar.

    macOS renders a menu-bar image as a template: only the alpha matters, and
    the system colours it for the light or dark bar. So the accent knob is
    drawn in the same ink as the track; what carries the mark is its shape.
    Proportions follow the app icon's 600x300 track.
    """
    ss = 8
    w, h = width * ss, height * ss
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    stroke = max(ss, round(h * 0.17))
    gap = max(ss, round(h * 0.11))
    for i in range(stroke):
        d.rectangle([i, i, w - 1 - i, h - 1 - i], outline=INK + (255,))

    side = h - 2 * stroke - 2 * gap
    kx1 = w - stroke - gap
    ky0 = stroke + gap
    d.rectangle([kx1 - side, ky0, kx1 - 1, ky0 + side - 1], fill=INK + (255,))

    return img.resize((width, height), Image.LANCZOS)


MENUBAR = ROOT / "PixelSwitch/Resources/Assets.xcassets/MenuBarLogo.imageset"
MENUBAR_CONTENTS = """{
  "images" : [
    { "filename" : "menubar-logo.png", "idiom" : "mac", "scale" : "1x" },
    { "filename" : "menubar-logo@2x.png", "idiom" : "mac", "scale" : "2x" },
    { "filename" : "menubar-logo@3x.png", "idiom" : "mac", "scale" : "3x" }
  ],
  "info" : { "author" : "xcode", "version" : 1 },
  "properties" : { "template-rendering-intent" : "template" }
}
"""

if __name__ == "__main__":
    for s in (16, 32, 64, 128, 256, 512, 1024):
        draw(s).save(ICONSET / f"icon_{s}.png")
    PREVIEW.parent.mkdir(exist_ok=True)
    draw(512).save(PREVIEW)

    # Menu-bar mark: 20x10 points, at 1x / 2x / 3x.
    MENUBAR.mkdir(parents=True, exist_ok=True)
    for scale, suffix in ((1, ""), (2, "@2x"), (3, "@3x")):
        draw_mark(20 * scale, 10 * scale).save(MENUBAR / f"menubar-logo{suffix}.png")
    (MENUBAR / "Contents.json").write_text(MENUBAR_CONTENTS)
    print("wrote", ICONSET, PREVIEW, "and", MENUBAR)
