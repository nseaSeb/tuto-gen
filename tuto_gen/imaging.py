"""Ouverture d'images avec prise en charge étendue des formats.

En plus des formats nativement gérés par Pillow (PNG, JPEG, WebP, GIF,
BMP, TIFF…), ce module ajoute, lorsque les dépendances optionnelles sont
installées :

    - HEIC / HEIF  via ``pillow-heif`` (photos iPhone) ;
    - SVG          via ``cairosvg`` (rastérisation du vectoriel).

Tout passe par :func:`ouvrir`, ce qui garantit le même comportement pour le
logo, le fond et les captures. Si une dépendance manque, le format
correspondant est simplement absent de :func:`formats_supportes` et
:func:`ouvrir` lève une erreur explicite.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

# Enregistre le décodeur HEIC/HEIF auprès de Pillow s'il est disponible.
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    _HEIC_OK = True
except Exception:  # pragma: no cover - dépendance optionnelle
    _HEIC_OK = False

# cairosvg rastérise le SVG (vectoriel) en bitmap.
try:
    import cairosvg

    _SVG_OK = True
except Exception:  # pragma: no cover - dépendance optionnelle
    _SVG_OK = False

# Largeur de rastérisation des SVG (vectoriel → pixels). Généreuse pour que le
# redimensionnement aval reste net, y compris sur un fond plein écran.
_SVG_LARGEUR = 2000

# Formats toujours gérés par Pillow seul (sans dépendance optionnelle).
_BASE = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")


def formats_supportes() -> tuple[str, ...]:
    """Extensions (avec point) réellement ouvrables sur cette installation."""
    exts = list(_BASE)
    if _HEIC_OK:
        exts += [".heic", ".heif"]
    if _SVG_OK:
        exts.append(".svg")
    return tuple(exts)


def motif_filetypes() -> list[tuple[str, str]]:
    """``filetypes`` prêt pour ``filedialog.askopenfilename``."""
    motif = " ".join(f"*{e}" for e in formats_supportes())
    return [("Images", motif), ("Tous", "*.*")]


def ouvrir(chemin) -> Image.Image:
    """Ouvre une image, HEIC/SVG compris si les libs optionnelles sont là.

    Mêmes contrats que :func:`PIL.Image.open` : lève une exception si le
    fichier est illisible ou le format non pris en charge.
    """
    p = Path(chemin)
    if p.suffix.lower() == ".svg":
        if not _SVG_OK:
            raise ValueError(
                "Fichier SVG non pris en charge : installez `cairosvg` "
                "(`pip install cairosvg`).")
        png = cairosvg.svg2png(url=str(p), output_width=_SVG_LARGEUR)
        return Image.open(io.BytesIO(png))
    return Image.open(p)
