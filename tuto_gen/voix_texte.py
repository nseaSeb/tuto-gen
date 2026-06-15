"""Normalisation du texte de narration avant synthèse vocale (XTTS).

XTTS reçoit aujourd'hui le texte brut : certains éléments sont mal prononcés
(symboles, abréviations, noms propres ou jargon récurrents). Ce module applique,
juste avant la synthèse :

    1. une normalisation typographique légère (guillemets, espaces) ;
    2. un **dictionnaire de prononciation** fourni par l'utilisateur
       (terme écrit → forme à prononcer), insensible à la casse et borné aux
       mots entiers ;
    3. l'expansion de quelques symboles courants en toutes lettres.

Le résultat est ce qui est réellement synthétisé *et* ce qui sert de clé de
cache — un même texte normalisé n'est donc jamais resynthétisé deux fois.
"""

from __future__ import annotations

import re

# Symboles courants → forme parlée (français). Volontairement minimal.
_SYMBOLES = (
    ("n°", "numéro "),
    ("N°", "numéro "),
    ("%", " pour cent"),
    ("€", " euros"),
    ("&", " et "),
)

# Guillemets/apostrophes typographiques → ASCII (XTTS gère mieux le simple).
_TYPO = str.maketrans({
    "“": '"', "”": '"', "«": '"', "»": '"',
    "’": "'", "‘": "'", "…": "...",
})


def _appliquer_dico(texte: str, dico: dict[str, str]) -> str:
    """Remplace chaque terme du dictionnaire (mot entier, casse ignorée).

    Les termes les plus longs sont traités d'abord pour éviter qu'un terme
    court n'en ampute un plus long. Le remplacement est inséré littéralement
    (pas d'interprétation des séquences `\\1` éventuelles)."""
    for terme, remplacement in sorted(dico.items(), key=lambda kv: -len(kv[0])):
        terme = str(terme).strip()
        if not terme:
            continue
        motif = re.compile(rf"(?<!\w){re.escape(terme)}(?!\w)", re.IGNORECASE)
        texte = motif.sub(lambda _m, r=str(remplacement): r, texte)
    return texte


def normaliser(texte: str, dico: dict[str, str] | None = None) -> str:
    """Normalise `texte` pour la synthèse vocale. Renvoie une chaîne nettoyée."""
    if not texte:
        return ""
    t = texte.translate(_TYPO)
    if dico:
        t = _appliquer_dico(t, dico)
    for symbole, mot in _SYMBOLES:
        t = t.replace(symbole, mot)
    return re.sub(r"\s+", " ", t).strip()
