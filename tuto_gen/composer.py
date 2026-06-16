"""Composition visuelle des slides avec Pillow.

Produit une image RGB par scène, dans la résolution définie par le meta.
Deux layouts :

- `title`      : logo centré + titre + sous-titre sur fond plein.
- `screenshot` : header (logo + titre), screenshot centré avec annotations
                 (flèches / highlights), footer avec la narration.

Si un asset (logo, screenshot) est absent, un placeholder est généré à la
volée pour que le pipeline tourne sans vrais fichiers.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import fleches, imaging
from .config import Annotation, Meta, Scene, TexteLibre

# Polices système macOS (repli sur la police par défaut Pillow si absentes)
_FONTS_BOLD = [
    "assets/fonts/Inter-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
]
_FONTS_REGULAR = [
    "assets/fonts/Inter-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


# Police personnalisée globale (chemin), positionnée par `composer_scene`.
_POLICE_PERSO: str | None = None


def _appliquer_police(meta: Meta) -> None:
    global _POLICE_PERSO
    p = getattr(meta, "police", None)
    _POLICE_PERSO = str(p) if p and Path(p).is_file() else None


def _charger_police(taille: int, gras: bool = False) -> ImageFont.FreeTypeFont:
    """Charge la police à la taille demandée (police perso prioritaire)."""
    if _POLICE_PERSO:
        try:
            return ImageFont.truetype(_POLICE_PERSO, taille)
        except Exception:
            pass
    for chemin in (_FONTS_BOLD if gras else _FONTS_REGULAR):
        try:
            return ImageFont.truetype(chemin, taille)
        except Exception:
            continue
    return ImageFont.load_default(size=taille)


def _hex_rgb(couleur: str) -> tuple[int, int, int]:
    """Convertit '#RRGGBB' en tuple RGB."""
    c = couleur.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore


def _luminosite(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def _cover(img: Image.Image, size) -> Image.Image:
    """Redimensionne `img` pour couvrir `size` puis recadre au centre."""
    w, h = size
    r = max(w / img.width, h / img.height)
    nw, nh = max(1, int(img.width * r)), max(1, int(img.height * r))
    img = img.resize((nw, nh), Image.LANCZOS)
    x, y = (nw - w) // 2, (nh - h) // 2
    return img.crop((x, y, x + w, y + h))


def _degrade(c1, c2, size, sens="vertical") -> Image.Image:
    """Génère un dégradé linéaire entre deux couleurs RGB."""
    import numpy as np
    w, h = size
    if sens == "horizontal":
        t = np.repeat(np.linspace(0, 1, w)[None, :], h, axis=0)
    elif sens == "diagonal":
        t = (np.linspace(0, 1, w)[None, :] + np.linspace(0, 1, h)[:, None]) / 2
    else:  # vertical
        t = np.repeat(np.linspace(0, 1, h)[:, None], w, axis=1)
    t = t[..., None]
    arr = (np.array(c1) * (1 - t) + np.array(c2) * t).astype("uint8")
    return Image.fromarray(arr, "RGB")


def _fond(meta: Meta, size) -> Image.Image:
    """Construit l'image de fond selon le réglage (couleur / dégradé / image)."""
    t = getattr(meta, "fond_type", "couleur")
    if t == "image":
        img_path = getattr(meta, "fond_image", None)
        if img_path and Path(img_path).is_file():
            try:
                return _cover(imaging.ouvrir(img_path).convert("RGB"), size)
            except Exception:
                pass
    if t == "degrade":
        return _degrade(_hex_rgb(meta.couleur_fond),
                        _hex_rgb(getattr(meta, "couleur_fond2", "#1B4332")),
                        size, getattr(meta, "degrade_sens", "vertical"))
    return Image.new("RGB", size, _hex_rgb(meta.couleur_fond))


def _wrap(draw, texte, police, largeur_max):
    """Découpe `texte` en lignes tenant dans `largeur_max` pixels."""
    lignes, courante = [], ""
    for mot in texte.split():
        essai = f"{courante} {mot}".strip()
        if draw.textlength(essai, font=police) <= largeur_max:
            courante = essai
        else:
            if courante:
                lignes.append(courante)
            courante = mot
    if courante:
        lignes.append(courante)
    return lignes or [""]


def _texte_centre(draw, lignes, police, cx, y_depart, couleur, interligne=1.25):
    """Dessine des lignes centrées horizontalement autour de `cx`."""
    bbox = police.getbbox("Ay")
    hauteur_ligne = (bbox[3] - bbox[1]) * interligne
    y = y_depart
    for ligne in lignes:
        w = draw.textlength(ligne, font=police)
        draw.text((cx - w / 2, y), ligne, font=police, fill=couleur)
        y += hauteur_ligne
    return y


def _bloc_bbox(draw, lignes, police, cx, cy, interligne=1.25):
    """Boîte (x, y, w, h) d'un bloc de lignes centré sur (cx, cy)."""
    bbox = police.getbbox("Ay")
    hauteur_ligne = (bbox[3] - bbox[1]) * interligne
    total = hauteur_ligne * len(lignes)
    largeur = max((draw.textlength(l, font=police) for l in lignes), default=0)
    return (cx - largeur / 2, cy - total / 2, largeur, total)


def _bloc_centre(draw, lignes, police, cx, cy, couleur, interligne=1.25):
    """Dessine des lignes centrées horizontalement sur `cx` et verticalement
    autour de `cy` (cy = centre vertical du bloc)."""
    bbox = police.getbbox("Ay")
    hauteur_ligne = (bbox[3] - bbox[1]) * interligne
    y = cy - hauteur_ligne * len(lignes) / 2
    for ligne in lignes:
        w = draw.textlength(ligne, font=police)
        draw.text((cx - w / 2, y), ligne, font=police, fill=couleur)
        y += hauteur_ligne


def _bloc_aligne(draw, lignes, police, cx, cy, couleur, align="center",
                 interligne=1.25):
    """Comme `_bloc_centre` mais avec alignement gauche/centre/droite, le bloc
    restant centré horizontalement et verticalement sur (cx, cy)."""
    bbox = police.getbbox("Ay")
    hauteur_ligne = (bbox[3] - bbox[1]) * interligne
    widths = [draw.textlength(l, font=police) for l in lignes]
    bloc_w = max(widths, default=0)
    y = cy - hauteur_ligne * len(lignes) / 2
    for ligne, lw in zip(lignes, widths):
        if align == "left":
            x = cx - bloc_w / 2
        elif align == "right":
            x = cx + bloc_w / 2 - lw
        else:
            x = cx - lw / 2
        draw.text((x, y), ligne, font=police, fill=couleur)
        y += hauteur_ligne


# Ratios des rôles de texte par rapport à la taille de base de la slide.
RATIOS_ROLE = {"titre": 2.0, "sous_titre": 1.15, "paragraphe": 0.85}


def taille_effective(meta: Meta, tx: TexteLibre) -> float:
    """Taille de police effective (% de hauteur) d'un texte : dérivée de la
    taille de base si un rôle est défini, sinon la taille absolue du texte."""
    r = RATIOS_ROLE.get(getattr(tx, "role", "libre"))
    if r is not None:
        return max(1.0, getattr(meta, "taille_base", 3.8) * r)
    return tx.taille


def _taille_px(meta: Meta, role: str) -> int:
    """Taille de police en pixels du titre/sous-titre d'une slide, dérivée de
    la « taille de base » globale (réglage) et du ratio du rôle."""
    base = getattr(meta, "taille_base", 3.8) or 3.8
    ratio = RATIOS_ROLE.get(role, 1.0)
    _w, h = meta.resolution
    return max(8, int(h * base * ratio / 100))


def _dessiner_titres(draw, meta: Meta, scene: Scene, couleur) -> None:
    """Dessine le titre et le sous-titre positionnables d'une slide.

    Tailles dérivées de `meta.taille_base`, position = centre du bloc en % de
    la slide (`titre_x/y`, `sous_titre_x/y`). Partagé par les deux layouts.
    """
    w, h = meta.resolution
    if scene.titre:
        police_titre = _charger_police(_taille_px(meta, "titre"), gras=True)
        lignes = _wrap(draw, scene.titre, police_titre, w * 0.8)
        _bloc_centre(draw, lignes, police_titre,
                     w * scene.titre_x / 100, h * scene.titre_y / 100, couleur)
    if scene.sous_titre:
        police_sous = _charger_police(_taille_px(meta, "sous_titre"))
        lignes_s = _wrap(draw, scene.sous_titre, police_sous, w * 0.7)
        _bloc_centre(draw, lignes_s, police_sous,
                     w * scene.sous_titre_x / 100,
                     h * scene.sous_titre_y / 100, couleur)


def textes_actifs(scene: Scene, t: float) -> list[TexteLibre]:
    """Textes libres visibles à l'instant `t`."""
    return [tx for tx in scene.textes
            if tx.texte.strip() and tx.debut <= t
            and (tx.fin is None or t < tx.fin)]


def _dessiner_textes(img: Image.Image, meta: Meta, scene: Scene,
                     t: float) -> Image.Image:
    """Dessine les paragraphes de texte libre actifs par-dessus la slide."""
    if not scene.textes:
        return img
    w, h = meta.resolution
    d = ImageDraw.Draw(img)
    for tx in textes_actifs(scene, t):
        taille = taille_effective(meta, tx)
        police = _charger_police(max(8, int(h * taille / 100)), gras=tx.gras)
        lignes = _wrap(d, tx.texte, police, w * max(5.0, tx.largeur) / 100)
        _bloc_aligne(d, lignes, police, w * tx.x / 100, h * tx.y / 100,
                     _hex_rgb(tx.couleur), tx.align)
    return img


def zones_textes(scene: Scene, meta: Meta) -> list[tuple[int, tuple]]:
    """Boîtes (idx, (x, y, w, h)) en px-slide des textes libres (pour le drag)."""
    if not scene.textes:
        return []
    _appliquer_police(meta)
    w, h = meta.resolution
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    out = []
    for i, tx in enumerate(scene.textes):
        if not tx.texte.strip():
            continue
        taille = taille_effective(meta, tx)
        police = _charger_police(max(8, int(h * taille / 100)), gras=tx.gras)
        lignes = _wrap(d, tx.texte, police, w * max(5.0, tx.largeur) / 100)
        out.append((i, _bloc_bbox(d, lignes, police,
                                  w * tx.x / 100, h * tx.y / 100)))
    return out


# --------------------------------------------------------------------------
# Placeholders
# --------------------------------------------------------------------------

def _placeholder_screenshot(taille, titre) -> Image.Image:
    """Génère un faux screenshot (rectangle clair + barre + libellé)."""
    w, h = taille
    img = Image.new("RGB", (w, h), (245, 247, 250))
    d = ImageDraw.Draw(img)
    # Barre de fenêtre façon navigateur
    d.rectangle([0, 0, w, int(h * 0.10)], fill=(225, 229, 235))
    for i, col in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([20 + i * 34, int(h * 0.035), 44 + i * 34, int(h * 0.035) + 24],
                  fill=col)
    # Cadre
    d.rectangle([0, 0, w - 1, h - 1], outline=(200, 205, 212), width=3)
    # Libellé centré
    police = _charger_police(max(28, w // 22), gras=True)
    txt = titre or "Screenshot"
    tw = d.textlength(txt, font=police)
    d.text(((w - tw) / 2, h * 0.5), txt, font=police, fill=(120, 130, 145))
    sous = _charger_police(max(18, w // 40))
    note = "(placeholder — fournissez un vrai screenshot)"
    nw = d.textlength(note, font=sous)
    d.text(((w - nw) / 2, h * 0.5 + max(28, w // 22) + 16), note,
           font=sous, fill=(160, 168, 180))
    return img


def _placeholder_logo(taille, app, couleur_accent) -> Image.Image:
    """Génère un logo placeholder : pastille ronde avec l'initiale de l'app."""
    s = taille
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, s - 1, s - 1], fill=_hex_rgb(couleur_accent))
    lettre = (app or "T")[0].upper()
    police = _charger_police(int(s * 0.6), gras=True)
    bbox = d.textbbox((0, 0), lettre, font=police)
    lw, lh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((s - lw) / 2 - bbox[0], (s - lh) / 2 - bbox[1]), lettre,
           font=police, fill=(40, 40, 40))
    return img


def _charger_logo(meta: Meta, taille: int) -> Image.Image:
    """Charge le logo (avec transparence) ou génère un placeholder."""
    if meta.logo and Path(meta.logo).is_file():
        logo = imaging.ouvrir(meta.logo).convert("RGBA")
        ratio = taille / max(logo.size)
        logo = logo.resize(
            (int(logo.width * ratio), int(logo.height * ratio)),
            Image.LANCZOS,
        )
        return logo
    return _placeholder_logo(taille, meta.app, meta.couleur_accent)


# Cache des captures décodées : éviter de re-décoder le PNG à chaque appel
# (composition + zone_screenshot, et à chaque frame d'un zoom « capture » dans
# l'aperçu). Clé = (chemin|None, mtime|titre, taille_zone). Borné en taille.
_CAPTURE_CACHE: dict[tuple, Image.Image] = {}
_CAPTURE_CACHE_MAX = 8


def _charger_capture(chemin, titre: str, taille_zone) -> Image.Image:
    """Charge un fichier de capture (transparence préservée), ou un placeholder.

    Renvoie une image RGBA : les zones transparentes laisseront voir le fond de
    la slide au moment du collage (au lieu d'un aplat noir). Le résultat n'est
    jamais modifié en place par les appelants — il peut donc être mis en cache."""
    if chemin and Path(chemin).is_file():
        try:
            mtime = Path(chemin).stat().st_mtime_ns
        except OSError:
            mtime = 0
        cle = (str(chemin), mtime, tuple(taille_zone))
        img = _CAPTURE_CACHE.get(cle)
        if img is None:
            img = imaging.ouvrir(chemin).convert("RGBA")
            if len(_CAPTURE_CACHE) >= _CAPTURE_CACHE_MAX:
                _CAPTURE_CACHE.pop(next(iter(_CAPTURE_CACHE)))
            _CAPTURE_CACHE[cle] = img
        return img
    w = taille_zone[0]
    h = int(w * 9 / 16)
    return _placeholder_screenshot((w, h), titre).convert("RGBA")


# --------------------------------------------------------------------------
# Sélection des éléments actifs à un instant t
# --------------------------------------------------------------------------

def capture_active(scene: Scene, t: float):
    """Renvoie la capture affichée à l'instant `t` (la plus récente active)."""
    if not scene.captures:
        return None
    candidats = [c for c in scene.captures
                 if c.debut <= t and (c.fin is None or t < c.fin)]
    if candidats:
        return candidats[-1]
    # En cas de trou temporel : dernière capture déjà commencée, sinon la 1re
    passees = [c for c in scene.captures if c.debut <= t]
    return passees[-1] if passees else scene.captures[0]


def _sous_titre_de(n) -> str:
    """Sous-titre affiché pour une narration : texte perso sinon texte parlé,
    chaîne vide si l'affichage est désactivé pour ce segment."""
    if not n.afficher_sous_titre:
        return ""
    return n.sous_titre.strip() or n.texte.strip()


def narration_active(scene: Scene, t: float) -> str:
    """Renvoie le sous-titre affiché en footer à l'instant `t` (respecte le
    drapeau d'affichage et le texte personnalisé de chaque narration)."""
    candidats = [n for n in scene.narrations
                 if _sous_titre_de(n) and n.debut <= t
                 and (n.fin is None or t < n.fin)]
    if candidats:
        return _sous_titre_de(candidats[-1])
    passees = [n for n in scene.narrations if _sous_titre_de(n) and n.debut <= t]
    return _sous_titre_de(passees[-1]) if passees else ""


def annotations_actives(scene: Scene, t: float) -> list[Annotation]:
    """Renvoie les annotations visibles à l'instant `t`."""
    return [a for a in scene.annotations
            if a.debut <= t and (a.fin is None or t < a.fin)]


# --------------------------------------------------------------------------
# Annotations
# --------------------------------------------------------------------------

def _pct_vers_px(pct, origine, taille):
    """Convertit une coordonnée en % (0-100) en pixels dans la boîte donnée."""
    ox, oy = origine
    bw, bh = taille
    return (ox + bw * pct[0] / 100.0, oy + bh * pct[1] / 100.0)


# Le rendu des flèches/formes (PNG de assets/images) vit dans `fleches`.
# Sur-échantillonnage du calque d'annotations pour des bords nets.
_SS = 3


def _appliquer_annotations(base: Image.Image, annotations, origine, taille):
    """Dessine flèches et highlights sur la boîte screenshot de `base`."""
    if not annotations:
        return base

    # On dessine sur un calque sur-échantillonné (×_SS) puis on réduit : les
    # bords des flèches/highlights deviennent nets et anti-aliasés.
    ss = _SS
    sw, sh = base.size[0] * ss, base.size[1] * ss
    so = (origine[0] * ss, origine[1] * ss)
    st = (taille[0] * ss, taille[1] * ss)
    calque = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    d = ImageDraw.Draw(calque)

    for a in annotations:
        couleur = _hex_rgb(a.couleur)
        if a.type == "highlight" and a.zone:
            x1, y1 = _pct_vers_px((a.zone[0], a.zone[1]), so, st)
            x2, y2 = _pct_vers_px((a.zone[2], a.zone[3]), so, st)
            alpha = int(max(0.0, min(1.0, a.opacite)) * 255)
            d.rectangle([x1, y1, x2, y2], fill=(*couleur, alpha))
            d.rectangle([x1, y1, x2, y2], outline=(*couleur, 255), width=4 * ss)
        elif a.type == "arrow" and a.de and a.vers:
            p0 = _pct_vers_px(a.de, so, st)
            p1 = _pct_vers_px(a.vers, so, st)
            fleches.poser(calque, p0, p1, (*couleur, 255),
                          taille=getattr(a, "taille", 100),
                          style=getattr(a, "style", fleches._DEFAUT),
                          rotation=getattr(a, "rotation", 0.0))

    calque = calque.resize(base.size, Image.LANCZOS)
    return Image.alpha_composite(base.convert("RGBA"), calque).convert("RGB")


# --------------------------------------------------------------------------
# Layouts
# --------------------------------------------------------------------------

def _composer_title(scene: Scene, meta: Meta, t: float = 0.0) -> Image.Image:
    w, h = meta.resolution
    fond = _hex_rgb(meta.couleur_fond)
    img = _fond(meta, (w, h))
    d = ImageDraw.Draw(img)

    # Couleur de texte lisible selon la luminosité du fond
    texte_clair = _hex_rgb(meta.couleur_accent)
    texte = texte_clair if _luminosite(fond) < 0.6 else (30, 30, 30)

    # Logo positionnable (centre en % de slide + échelle)
    taille_logo = max(8, int(h * 0.18 * scene.logo_echelle / 100))
    logo = _charger_logo(meta, taille_logo)
    img.paste(logo, (int(w * scene.logo_x / 100 - logo.width / 2),
                     int(h * scene.logo_y / 100 - logo.height / 2)), logo)

    # Titre + sous-titre, positionnés librement (centre du bloc en % de slide),
    # taille pilotée par le réglage global « taille de base ».
    _dessiner_titres(d, meta, scene, texte)

    img = _dessiner_textes(img, meta, scene, t)
    return img


def zones_titre(scene: Scene, meta: Meta) -> dict | None:
    """Boîtes (x, y, w, h) en pixels-slide du logo, du titre et du sous-titre.
    Utile pour le glisser-déposer dans l'éditeur. Le logo est déplaçable sur les
    slides 'title' et 'screenshot' ; None pour les autres types."""
    if scene.type not in ("title", "screenshot"):
        return None
    _appliquer_police(meta)
    w, h = meta.resolution
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    zones: dict = {}
    # Logo positionnable sur les slides titre ET capture.
    taille_logo = max(8, int(h * 0.18 * scene.logo_echelle / 100))
    logo = _charger_logo(meta, taille_logo)
    zones["logo"] = (w * scene.logo_x / 100 - logo.width / 2,
                     h * scene.logo_y / 100 - logo.height / 2,
                     logo.width, logo.height)
    if scene.titre:
        police_titre = _charger_police(_taille_px(meta, "titre"), gras=True)
        lignes = _wrap(d, scene.titre, police_titre, w * 0.8)
        zones["titre"] = _bloc_bbox(
            d, lignes, police_titre,
            w * scene.titre_x / 100, h * scene.titre_y / 100)
    if scene.sous_titre:
        police_sous = _charger_police(_taille_px(meta, "sous_titre"))
        lignes_s = _wrap(d, scene.sous_titre, police_sous, w * 0.7)
        zones["sous_titre"] = _bloc_bbox(
            d, lignes_s, police_sous,
            w * scene.sous_titre_x / 100, h * scene.sous_titre_y / 100)
    return zones


def _dispo_main(scene: Scene, meta: Meta) -> tuple[int, int, int, int, int]:
    """Géométrie stable de la zone principale : (marge, h_header, h_footer,
    zone_w, zone_h). Le footer existe dès que la scène a une narration."""
    w, h = meta.resolution
    h_header = int(h * 0.15)
    h_footer = int(h * 0.10) if scene.a_sous_titre() else 0
    h_main = h - h_header - h_footer
    pad = int(min(w, h_main) * 0.05)
    return (int(w * 0.03), h_header, h_footer, w - 2 * pad, h_main - 2 * pad)


def _placement_capture(scene: Scene, meta: Meta, cap,
                       src_size) -> tuple[int, int, int, int]:
    """Position/taille (ox, oy, nw, nh) d'une capture sur la slide, en tenant
    compte de son zoom (`echelle`) et de son décalage (`decalage_x/y`)."""
    w, h = meta.resolution
    _marge, h_header, h_footer, zone_w, zone_h = _dispo_main(scene, meta)
    h_main = h - h_header - h_footer
    sw, sh = src_size
    ech = (cap.echelle / 100.0) if cap else 1.0
    ratio = min(zone_w / sw, zone_h / sh) * max(0.05, ech)
    nw, nh = max(1, int(sw * ratio)), max(1, int(sh * ratio))
    dx = (cap.decalage_x if cap else 0.0) / 100.0 * w
    dy = (cap.decalage_y if cap else 0.0) / 100.0 * h
    ox = int((w - nw) / 2 + dx)
    oy = int(h_header + (h_main - nh) / 2 + dy)
    return ox, oy, nw, nh


def _composer_screenshot(scene: Scene, meta: Meta, t: float = 0.0) -> Image.Image:
    w, h = meta.resolution
    fond = _hex_rgb(meta.couleur_fond)
    img = _fond(meta, (w, h))
    d = ImageDraw.Draw(img)

    texte = _hex_rgb(meta.couleur_accent) if _luminosite(fond) < 0.6 else (30, 30, 30)

    marge, h_header, h_footer, zone_w, zone_h = _dispo_main(scene, meta)
    h_main = h - h_header - h_footer

    # --- Logo positionnable (centre en % de slide + échelle), même logique
    #     que sur les slides titre. Le titre et le sous-titre sont des blocs
    #     positionnables dessinés en fin de composition (cf. _dessiner_titres). ---
    taille_logo = max(8, int(h * 0.18 * scene.logo_echelle / 100))
    logo = _charger_logo(meta, taille_logo)
    img.paste(logo, (int(w * scene.logo_x / 100 - logo.width / 2),
                     int(h * scene.logo_y / 100 - logo.height / 2)), logo)

    # --- Zone principale : capture active à l'instant t ---
    cap = capture_active(scene, t)
    chemin = cap.chemin if cap else None
    screen = _charger_capture(chemin, scene.titre, (zone_w, zone_h))

    ox, oy, nw, nh = _placement_capture(scene, meta, cap,
                                        (screen.width, screen.height))
    screen = screen.resize((nw, nh), Image.LANCZOS)
    alpha = screen.getchannel("A")
    opaque = alpha.getextrema()[0] >= 250  # pas de transparence notable

    # Ombre portée seulement pour une capture pleine (sinon l'ombre
    # rectangulaire dépasse d'une image transparente non rectangulaire).
    if opaque:
        ombre = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(ombre).rectangle(
            [ox + 8, oy + 10, ox + screen.width + 8, oy + screen.height + 10],
            fill=(0, 0, 0, 70),
        )
        img = Image.alpha_composite(img.convert("RGBA"), ombre).convert("RGB")
    # Collage en respectant l'alpha → les zones transparentes laissent voir
    # le fond de la slide au lieu d'un aplat noir.
    img.paste(screen, (ox, oy), alpha)

    # Annotations actives à l'instant t (coordonnées en % de la boîte)
    img = _appliquer_annotations(
        img, annotations_actives(scene, t), (ox, oy),
        (screen.width, screen.height)
    )
    d = ImageDraw.Draw(img)

    # --- Footer : sous-titre (légende) actif à l'instant t ---
    if h_footer:
        narr = narration_active(scene, t)
        if narr:
            y_footer = h - h_footer
            # Bande de fond réglable (couleur + opacité), composée en alpha.
            op = max(0.0, min(1.0, getattr(meta, "sous_titre_fond_opacite", 0.55)))
            fond_st = _hex_rgb(getattr(meta, "sous_titre_fond", "#000000"))
            if op > 0:
                bande = Image.new("RGBA", img.size, (0, 0, 0, 0))
                ImageDraw.Draw(bande).rectangle(
                    [0, y_footer, w, h], fill=(*fond_st, int(op * 255)))
                img = Image.alpha_composite(img.convert("RGBA"), bande).convert("RGB")
                d = ImageDraw.Draw(img)
            # Texte lisible selon la luminosité de la bande.
            txt_st = (245, 245, 245) if _luminosite(fond_st) < 0.6 else (20, 20, 20)
            police_n = _charger_police(int(h_footer * 0.32))
            retrait = int(h_footer * 0.45)
            extrait = " ".join(narr.split())
            lignes = _wrap(d, extrait, police_n, w - 2 * marge - retrait)[:2]
            bbox = police_n.getbbox("Ay")
            hl = (bbox[3] - bbox[1]) * 1.2
            y = y_footer + (h_footer - hl * len(lignes)) / 2
            tr = int(h_footer * 0.16)
            ty = y + (bbox[3] - bbox[1]) / 2
            d.polygon([(marge, ty - tr), (marge, ty + tr),
                       (marge + tr * 1.4, ty)], fill=txt_st)
            for ligne in lignes:
                d.text((marge + retrait, y), ligne, font=police_n, fill=txt_st)
                y += hl

    # Titre + sous-titre positionnables (taille pilotée par le réglage global)
    _dessiner_titres(d, meta, scene, texte)

    img = _dessiner_textes(img, meta, scene, t)
    return img


def composer_scene(scene: Scene, meta: Meta, t: float = 0.0) -> Image.Image:
    """Point d'entrée : compose l'image d'une scène à l'instant `t`."""
    _appliquer_police(meta)
    if scene.type == "title":
        return _composer_title(scene, meta, t)
    return _composer_screenshot(scene, meta, t)


def _smoothstep(x: float) -> float:
    """Interpolation douce (ease-in-out) sur [0, 1]."""
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def zoom_progression(zoom, t: float, duree: float) -> float:
    """Avancement [0, 1] du zoom à l'instant `t` (0 = vue pleine, 1 = zone).

    Rampe d'entrée (`entree` s) → maintien → rampe de sortie (`sortie` s) sur
    la fenêtre [debut, fin]. Hors fenêtre : 0 (pas d'effet).
    """
    debut = zoom.debut
    fin = zoom.fin if zoom.fin is not None else duree
    span = fin - debut
    if span <= 1e-6 or t <= debut or t >= fin:
        return 0.0
    local = t - debut
    # Bornage : entrée + sortie ne peuvent dépasser la fenêtre.
    in_d = max(0.0, zoom.entree)
    out_d = max(0.0, zoom.sortie)
    if in_d + out_d > span:
        ratio = span / (in_d + out_d)
        in_d *= ratio
        out_d *= ratio
    if in_d > 0 and local < in_d:
        return _smoothstep(local / in_d)
    if out_d > 0 and local > span - out_d:
        return _smoothstep((span - local) / out_d)
    return 1.0


def _zoom_actif(scene: Scene, t: float, duree: float):
    """Zoom le plus « avancé » à l'instant `t`, et son avancement (zoom, p)."""
    best, best_p = None, 0.0
    for z in (getattr(scene, "zooms", None) or []):
        p = zoom_progression(z, t, duree)
        if p > best_p:
            best_p, best = p, z
    return best, best_p


def _cadrage_cible(base: tuple[float, float, float, float],
                   zone: tuple[float, float, float, float]
                   ) -> tuple[float, float, float, float]:
    """Rectangle cible (px) dans `base`, à partir de `zone` (% de `base`).

    Ramené au format de `base` (pas de déformation), la zone restant visible.
    """
    bx, by, bw, bh = base
    x1, y1, x2, y2 = zone
    zx1 = bx + min(x1, x2) / 100.0 * bw
    zy1 = by + min(y1, y2) / 100.0 * bh
    zx2 = bx + max(x1, x2) / 100.0 * bw
    zy2 = by + max(y1, y2) / 100.0 * bh
    cx, cy = (zx1 + zx2) / 2.0, (zy1 + zy2) / 2.0
    zw = max(1.0, zx2 - zx1)
    zh = max(1.0, zy2 - zy1)
    ar = bw / bh
    if zw / zh < ar:
        zw = zh * ar
    else:
        zh = zw / ar
    zw = min(zw, bw)
    zh = min(zh, bh)
    ox = min(max(cx - zw / 2.0, bx), bx + bw - zw)
    oy = min(max(cy - zh / 2.0, by), by + bh - zh)
    return (ox, oy, zw, zh)


def zoom_transform(scene: Scene, meta: Meta, t: float, duree: float):
    """Transformation du zoom actif à `t` : (src, dst) en pixels, ou None.

    `src` = rectangle à recadrer dans l'image composée ; `dst` = rectangle où
    recoller (après redimensionnement). En `cible="slide"`, dst = toute la
    slide. En `cible="capture"`, dst = la zone du screenshot (le reste reste
    fixe). None s'il n'y a aucun zoom actif.
    """
    z, p = _zoom_actif(scene, t, duree)
    if z is None or p <= 0.0:
        return None
    W, H = meta.resolution
    base = (0.0, 0.0, float(W), float(H))
    if getattr(z, "cible", "slide") == "capture":
        zs = zone_screenshot(scene, meta, t)
        if zs is not None:
            base = (float(zs[0]), float(zs[1]), float(zs[2]), float(zs[3]))
    target = _cadrage_cible(base, z.zone)
    # Interpole entre la vue pleine (base, p=0) et le cadrage cible (p=1).
    src = tuple(b + (c - b) * p for b, c in zip(base, target))
    dst = base
    src = tuple(int(round(v)) for v in src)
    dst = tuple(int(round(v)) for v in dst)
    return (src, dst)


def appliquer_zoom(img: Image.Image, src, dst) -> Image.Image:
    """Recadre `img` sur `src`, redimensionne, puis recolle sur `dst`.

    Si `dst` couvre toute l'image (zoom « slide »), renvoie le recadrage
    redimensionné. Sinon (zoom « capture »), recolle le recadrage dans `dst`,
    le reste de l'image restant inchangé.
    """
    sx, sy, sw, sh = src
    dx, dy, dw, dh = dst
    sx = max(0, min(sx, img.width - 1))
    sy = max(0, min(sy, img.height - 1))
    sw = max(1, min(sw, img.width - sx))
    sh = max(1, min(sh, img.height - sy))
    dw = max(1, dw)
    dh = max(1, dh)
    if (src, dst) == ((0, 0, img.width, img.height),
                      (0, 0, img.width, img.height)):
        return img
    crop = img.crop((sx, sy, sx + sw, sy + sh)).resize((dw, dh), Image.LANCZOS)
    if (dx, dy, dw, dh) == (0, 0, img.width, img.height):
        return crop
    out = img.copy()
    out.paste(crop, (dx, dy))
    return out


def zone_screenshot(scene: Scene, meta: Meta,
                    t: float = 0.0) -> tuple[int, int, int, int] | None:
    """Retourne (ox, oy, sw, sh) de la zone capture dans la slide (pixels slide).

    Reproduit la logique de placement de `_composer_screenshot` pour la capture
    active à l'instant `t` — utile pour mapper les coordonnées souris de
    l'aperçu. Retourne None pour les slides de type 'title'.
    """
    if scene.type != "screenshot":
        return None
    _marge, h_header, h_footer, zone_w, zone_h = _dispo_main(scene, meta)
    cap = capture_active(scene, t)
    chemin = cap.chemin if cap else None
    screen = _charger_capture(chemin, scene.titre, (zone_w, zone_h))
    return _placement_capture(scene, meta, cap, (screen.width, screen.height))
