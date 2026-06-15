"""Constantes et utilitaires partagés par les modules de l'éditeur."""

from __future__ import annotations

import queue

VERT = "#2D6A4F"

# ── Timeline ────────────────────────────────────────────────────────────────
TL_LEFT = 116     # largeur zone labels
TL_TOP = 24       # hauteur règle
TL_ROW_H = 32     # hauteur d'une piste
TL_GAP = 3        # espace entre pistes
TL_HANDLE = 12    # largeur des poignées
TL_MIN_BODY = 170 # hauteur plancher du corps (commune timeline / génération)

SETTINGS_W = 300
LIST_W = 205

COUL = {
    "narration": "#1a5f9f",
    "capture": "#1a7a3a",
    "arrow": "#FF6B35",
    "highlight": "#FFD166",
    "sample": "#5a4f9f",
    "texte": "#9f5a8a",
    "scene_dur": "#3a3a6a",
}

# Styles de texte : libellé affiché -> (role, gras). La taille dérive de la
# « taille de base » globale via composer.RATIOS_ROLE.
PRESETS_TEXTE = {
    "Titre": ("titre", True),
    "Sous-titre": ("sous_titre", False),
    "Paragraphe": ("paragraphe", False),
}
ROLE_LABEL = {"titre": "Titre", "sous_titre": "Sous-titre",
              "paragraphe": "Paragraphe", "libre": "Personnalisé"}


# ── Utilitaires ─────────────────────────────────────────────────────────────
def _to_float(s: str, defaut: float = 0.0) -> float:
    try:
        return float(str(s).replace(",", "."))
    except (TypeError, ValueError):
        return defaut


def _lighten(hex_color: str, amount: float = 0.2) -> str:
    """Éclaircit une couleur '#RRGGBB' vers le blanc (Tk ne gère pas l'alpha)."""
    try:
        c = hex_color.lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return hex_color
    r = int(r + (255 - r) * amount)
    g = int(g + (255 - g) * amount)
    b = int(b + (255 - b) * amount)
    return f"#{r:02x}{g:02x}{b:02x}"


def _parse_fin(s: str) -> float | None:
    """'fin' / '' → None, sinon float."""
    s = str(s).strip().lower().rstrip("s")
    if not s or s in ("fin", "end", "~", "-"):
        return None
    try:
        return float(s.replace(",", "."))
    except (TypeError, ValueError):
        return None


class _Q:
    def __init__(self, q: queue.Queue):
        self.q = q
    def write(self, s: str):
        if s:
            self.q.put(s)
    def flush(self):
        pass

