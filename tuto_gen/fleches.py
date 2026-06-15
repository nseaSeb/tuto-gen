"""Flèches et formes dessinées à la main, à partir des PNG de assets/images.

Les visuels sont des doodles tracés à la main (trait noir sur fond
transparent, haute résolution). Plutôt que de les redessiner ou de les
vectoriser (perte de fidélité + dépendance lourde), on les exploite
directement :

    1. on recolore le trait avec la couleur de l'annotation (l'alpha du
       PNG définit la forme, donc le rendu reste net et anti-aliasé) ;
    2. on les pose sur la slide.

Deux familles :

    - FLÈCHES (``Fleche*``) : orientées. On applique une similitude
      (rotation + échelle uniforme, sans déformation) qui envoie la queue
      du dessin sur ``de`` et la pointe sur ``vers``. La pointe/queue sont
      localisées automatiquement par analyse de l'axe principal (PCA) du
      tracé ; un angle indicatif sert seulement à savoir quel bout est la
      pointe.
    - TAMPONS (``Call*``, ``Check``, ``Idée``) : non orientés. Posés bien
      droits, centrés sur le segment, dimensionnés selon sa longueur.

Pour ajouter une forme : déposer un PNG dans assets/images et l'ajouter à
``_ARROWS`` (avec son angle indicatif) ou à ``_STAMPS`` ci-dessous.
"""

from __future__ import annotations

import math
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------
# Flèches : nom de fichier (sans .png) -> angle indicatif (degrés, repère
# image x→droite / y→bas) de la direction queue→pointe. Approximatif : il
# sert uniquement à lever l'ambiguïté de sens de l'axe principal.
_ARROWS: dict[str, float] = {
    "Fleche1": -25,
    "Fleche2": 150,
    "Fleche 3": -100,
    "Fleche 4": -85,
    "Fleche 5": 70,
    "Fleche 6": -80,
    "Fleche 7": -30,
    "Fleche droite D": 0,
    "Fleche droite G": 180,
    "Fleche droite H": -90,
    "Fleche droite B": 90,
}

# Tampons : formes non orientées, posées bien droites.
_STAMPS: tuple[str, ...] = ("Call 1", "Call 2", "Call 3", "Check", "Idée")

# Style par défaut (et cible des anciens noms).
_DEFAUT = "Fleche1"
_ALIAS = {
    "skitch": _DEFAUT, "droite": _DEFAUT, "default": _DEFAUT,
    "marqueur": _DEFAUT, "halo": _DEFAUT, "neon": "Fleche 7",
    "fin": "Fleche1", "pointille": "Fleche 5", "ruban": "Fleche droite D",
    "bloc": "Fleche droite D", "double": "Fleche 5", "courbe": "Fleche 6",
    "craie": "Fleche 7",
}


def styles_disponibles() -> tuple[str, ...]:
    """Liste ordonnée des styles (flèches puis tampons) réellement présents."""
    noms = list(_ARROWS) + list(_STAMPS)
    return tuple(n for n in noms if (_asset_dir() / f"{n}.png").is_file())


# --------------------------------------------------------------------------
# Localisation des assets (dev + PyInstaller)
# --------------------------------------------------------------------------

def _asset_dir() -> Path:
    cands = []
    if hasattr(sys, "_MEIPASS"):
        cands.append(Path(sys._MEIPASS) / "assets" / "images")
    cands.append(Path(__file__).resolve().parent.parent / "assets" / "images")
    cands.append(Path.cwd() / "assets" / "images")
    for c in cands:
        if c.is_dir():
            return c
    return cands[-1]


# --------------------------------------------------------------------------
# Chargement + analyse (mis en cache)
# --------------------------------------------------------------------------

class _Forme:
    __slots__ = ("img", "tail", "tip", "stamp")

    def __init__(self, img, tail, tip, stamp):
        self.img = img          # PIL RGBA recadrée (trait noir)
        self.tail = tail        # (x, y) queue, en px de l'image recadrée
        self.tip = tip          # (x, y) pointe
        self.stamp = stamp


@lru_cache(maxsize=64)
def _charger(style: str) -> _Forme | None:
    chemin = _asset_dir() / f"{style}.png"
    if not chemin.is_file():
        return None
    img = Image.open(chemin).convert("RGBA")
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    stamp = style in _STAMPS
    if stamp:
        w, h = img.size
        return _Forme(img, (w / 2, h / 2), (w / 2, h / 2), True)
    tail, tip = _axe(img, _ARROWS.get(style, 0.0))
    return _Forme(img, tail, tip, False)


def _axe(img: Image.Image, hint_deg: float):
    """Renvoie (queue, pointe) en px via l'axe principal (PCA) du tracé."""
    a = np.asarray(img.getchannel("A"))
    ys, xs = np.where(a > 40)
    if len(xs) < 2:
        w, h = img.size
        return (0.0, h / 2), (w, h / 2)
    pts = np.column_stack([xs, ys]).astype(float)
    c = pts.mean(0)
    cov = np.cov((pts - c).T)
    _vals, vecs = np.linalg.eigh(cov)
    v = vecs[:, -1]                       # direction la plus étirée
    h = np.array([math.cos(math.radians(hint_deg)),
                  math.sin(math.radians(hint_deg))])
    if v.dot(h) < 0:                      # oriente queue -> pointe
        v = -v
    proj = (pts - c).dot(v)
    tip = pts[int(proj.argmax())]
    tail = pts[int(proj.argmin())]
    return (float(tail[0]), float(tail[1])), (float(tip[0]), float(tip[1]))


# --------------------------------------------------------------------------
# Recoloration
# --------------------------------------------------------------------------

def _teinter(img: Image.Image, couleur) -> Image.Image:
    r, g, b = couleur[:3]
    alpha = couleur[3] if len(couleur) > 3 else 255
    a = img.getchannel("A")
    if alpha < 255:
        a = a.point(lambda v: v * alpha // 255)
    solide = Image.new("RGBA", img.size, (r, g, b, 0))
    solide.putalpha(a)
    return solide


# --------------------------------------------------------------------------
# Pose sur le calque
# --------------------------------------------------------------------------

def poser(calque: Image.Image, p0, p1, couleur, taille=100, style="Fleche1",
          rotation=0.0):
    """Compose une flèche/forme PNG de `p0` vers `p1` sur `calque` (RGBA).

    `taille` est une échelle en pourcentage : 100 = la forme s'ajuste
    exactement au segment `de`→`vers` ; >100 l'agrandit, <100 la rétrécit
    (mise à l'échelle autour du milieu du segment, sans le déplacer).

    `rotation` est un angle en degrés (sens horaire) qui pivote la forme
    autour du milieu du segment, sans en changer la longueur ni la position.
    """
    forme = _charger(_ALIAS.get(style, style))
    if forme is None:
        forme = _charger(_DEFAUT)
    if forme is None:
        return
    teinte = _teinter(forme.img, couleur)

    # Échelle autour du milieu du segment.
    s = max(0.05, taille / 100.0)
    mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    p0 = (mx + (p0[0] - mx) * s, my + (p0[1] - my) * s)
    p1 = (mx + (p1[0] - mx) * s, my + (p1[1] - my) * s)

    # Rotation supplémentaire autour du milieu du segment.
    if rotation:
        ang = math.radians(rotation)
        cosa, sina = math.cos(ang), math.sin(ang)

        def _pivot(p):
            ddx, ddy = p[0] - mx, p[1] - my
            return (mx + ddx * cosa - ddy * sina,
                    my + ddx * sina + ddy * cosa)

        p0, p1 = _pivot(p0), _pivot(p1)

    dist = math.hypot(p1[0] - p0[0], p1[1] - p0[1]) or 1.0

    if forme.stamp:
        _poser_tampon(calque, teinte, p0, p1, dist, rotation)
    else:
        _poser_fleche(calque, teinte, forme.tail, forme.tip, p0, p1)


def _poser_fleche(calque, src, tail, tip, p0, p1):
    """Similitude : queue->p0, pointe->p1 (rotation + échelle uniforme)."""
    ux, uy = tip[0] - tail[0], tip[1] - tail[1]
    ul = math.hypot(ux, uy) or 1.0
    wx, wy = p1[0] - p0[0], p1[1] - p0[1]
    wl = math.hypot(wx, wy) or 1.0
    s = wl / ul
    beta = math.atan2(wy, wx) - math.atan2(uy, ux)
    cosb, sinb = math.cos(beta), math.sin(beta)
    # A = s·R (image -> slide), appliqué à (X - tail) + p0
    a00, a01 = s * cosb, -s * sinb
    a10, a11 = s * sinb, s * cosb

    def fwd(x, y):
        xx, yy = x - tail[0], y - tail[1]
        return (a00 * xx + a01 * yy + p0[0], a10 * xx + a11 * yy + p0[1])

    w, h = src.size
    coins = [fwd(0, 0), fwd(w, 0), fwd(0, h), fwd(w, h)]
    xs = [c[0] for c in coins]
    ys = [c[1] for c in coins]
    x0 = max(0, int(math.floor(min(xs))) - 1)
    y0 = max(0, int(math.floor(min(ys))) - 1)
    x1 = min(calque.width, int(math.ceil(max(xs))) + 1)
    y1 = min(calque.height, int(math.ceil(max(ys))) + 1)
    bw, bh = x1 - x0, y1 - y0
    if bw <= 0 or bh <= 0:
        return

    # Inverse A (slide -> image) pour Image.transform (sortie -> entrée).
    det = a00 * a11 - a01 * a10 or 1.0
    m00, m01 = a11 / det, -a01 / det
    m10, m11 = -a10 / det, a00 / det
    # entrée = M·(dst - p0) + tail, avec dst = (lx + x0, ly + y0)
    cx = tail[0] - (m00 * p0[0] + m01 * p0[1]) + (m00 * x0 + m01 * y0)
    cy = tail[1] - (m10 * p0[0] + m11 * p0[1]) + (m10 * x0 + m11 * y0)
    data = (m00, m01, cx, m10, m11, cy)
    morceau = src.transform((bw, bh), Image.AFFINE, data, resample=Image.BILINEAR)
    calque.alpha_composite(morceau, dest=(x0, y0))


def _poser_tampon(calque, src, p0, p1, dist, rotation=0.0):
    """Pose une forme non orientée, centrée sur le segment, pivotée de
    ``rotation`` degrés (sens horaire) autour de son centre."""
    w, h = src.size
    s = dist / max(w, h)
    nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    redim = src.resize((nw, nh), Image.LANCZOS)
    if rotation:
        # PIL tourne dans le sens trigo ; on veut le sens horaire à l'écran.
        redim = redim.rotate(-rotation, resample=Image.BICUBIC, expand=True)
        nw, nh = redim.size
    cx, cy = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    x0 = int(round(cx - nw / 2))
    y0 = int(round(cy - nh / 2))
    # Découpe au cas où ça déborde du calque.
    sx0 = max(0, -x0)
    sy0 = max(0, -y0)
    dx0 = max(0, x0)
    dy0 = max(0, y0)
    cw = min(nw - sx0, calque.width - dx0)
    ch = min(nh - sy0, calque.height - dy0)
    if cw <= 0 or ch <= 0:
        return
    if (sx0, sy0) != (0, 0) or (cw, ch) != (nw, nh):
        redim = redim.crop((sx0, sy0, sx0 + cw, sy0 + ch))
    calque.alpha_composite(redim, dest=(dx0, dy0))
