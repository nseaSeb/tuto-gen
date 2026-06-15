#!/usr/bin/env python3
"""Génère l'icône macOS de tuto-gen (assets/icon.icns) sans dépendance externe.

Dessine un PNG 1024×1024 (squircle + dégradé + bouton lecture), produit toutes
les tailles d'un .iconset puis assemble le .icns via `iconutil`.

Usage : .venv/bin/python packaging/make_icon.py
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT_PNG = ROOT / "assets" / "icon_1024.png"
OUT_ICNS = ROOT / "assets" / "icon.icns"

SS = 4  # supersampling pour des bords lisses
SIZE = 1024
S = SIZE * SS

# Dégradé diagonal indigo → violet (style "creator/vidéo")
TOP = (99, 102, 241)      # #6366F1
BOTTOM = (168, 85, 247)   # #A855F7


def _lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def rounded_mask(size: int, radius: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return m


def gradient(size: int) -> Image.Image:
    g = Image.new("RGB", (size, size))
    px = g.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = (
                _lerp(TOP[0], BOTTOM[0], t),
                _lerp(TOP[1], BOTTOM[1], t),
                _lerp(TOP[2], BOTTOM[2], t),
            )
    return g


def build_png() -> Image.Image:
    # Fond dégradé masqué par un coin arrondi (squircle macOS ≈ 22,5 %)
    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    grad = gradient(S).convert("RGBA")
    base.paste(grad, (0, 0), rounded_mask(S, int(S * 0.225)))

    draw = ImageDraw.Draw(base)

    # Disque clair translucide derrière le triangle de lecture
    cx = cy = S // 2
    r = int(S * 0.30)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, 235))

    # Triangle "play" centré, pointant vers la droite (décalage optique léger)
    tri_r = int(r * 0.55)
    offset = int(tri_r * 0.12)
    pts = [
        (cx - tri_r * 0.62 + offset, cy - tri_r * 0.85),
        (cx - tri_r * 0.62 + offset, cy + tri_r * 0.85),
        (cx + tri_r * 0.95 + offset, cy),
    ]
    draw.polygon(pts, fill=(124, 58, 237, 255))  # violet profond

    # Downscale anti-aliasé
    return base.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    img = build_png()
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT_PNG)

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        specs = [
            (16, "16x16"), (32, "16x16@2x"),
            (32, "32x32"), (64, "32x32@2x"),
            (128, "128x128"), (256, "128x128@2x"),
            (256, "256x256"), (512, "256x256@2x"),
            (512, "512x512"), (1024, "512x512@2x"),
        ]
        for px, name in specs:
            img.resize((px, px), Image.LANCZOS).save(iconset / f"icon_{name}.png")
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(OUT_ICNS)],
            check=True,
        )
    print(f"OK → {OUT_ICNS.relative_to(ROOT)}  ({OUT_ICNS.stat().st_size} octets)")
    print(f"     {OUT_PNG.relative_to(ROOT)} (aperçu 1024×1024)")


if __name__ == "__main__":
    main()
