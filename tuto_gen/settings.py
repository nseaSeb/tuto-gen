"""Réglages globaux persistés de tuto-gen.

Stockés dans `~/.tuto-gen/settings.json` et chargés au démarrage de
l'interface. Contient notamment le logo par défaut et le dossier de
bibliothèque de samples sonores.

Arborescence créée à la demande :
    ~/.tuto-gen/
        settings.json
        samples/          ← bibliothèque de samples fournie par l'utilisateur
        piper/            ← modèles de voix Piper téléchargés
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

DOSSIER = Path.home() / ".tuto-gen"
FICHIER = DOSSIER / "settings.json"
SAMPLES_DIR = DOSSIER / "samples"
PIPER_DIR = DOSSIER / "piper"
AUTOSAVE = DOSSIER / "autosave.yaml"      # projet en cours (récupération)
SESSION = DOSSIER / "session.json"        # état : scène courante, chemin projet

# Extensions audio reconnues pour la bibliothèque de samples
EXT_AUDIO = (".wav", ".mp3", ".aiff", ".aif", ".m4a", ".ogg", ".flac")


def samples_livres() -> Path | None:
    """Dossier de samples livrés avec l'appli (./assets/samples).

    Résolu en mode source (racine du dépôt) comme en bundle PyInstaller
    (sys._MEIPASS). Renvoie None s'il n'existe pas.
    """
    candidats = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidats.append(Path(base) / "assets" / "samples")
    candidats.append(Path(__file__).resolve().parent.parent / "assets" / "samples")
    for c in candidats:
        if c.is_dir():
            return c
    return None


@dataclass
class Reglages:
    """Réglages persistés entre deux sessions."""

    logo: str | None = None          # chemin absolu du logo par défaut
    voix: str = "fr_FR-siwis-medium" # (héritage, inutilisé : XTTS uniquement)
    voix_reference: str | None = None # WAV de référence pour le clonage XTTS
    voix_speaker: str = ""           # speaker XTTS intégré ("" = défaut)
    voix_vitesse: float = 1.0        # débit XTTS (speed)
    voix_expressivite: float = 0.75  # variation/expressivité (temperature)
    voix_fluidite: bool = False      # découpage phrase par phrase (prosodie)
    prononciations: dict = field(default_factory=dict)  # dico de prononciation
    sous_titre_fond: str = "#000000"        # couleur de la bande de sous-titres
    sous_titre_fond_opacite: float = 0.55   # opacité 0 → 1
    couleur_fond: str = "#2D6A4F"
    couleur_accent: str = "#ffffff"
    fond_type: str = "couleur"       # couleur | degrade | image
    couleur_fond2: str = "#1B4332"   # 2e couleur du dégradé
    degrade_sens: str = "vertical"   # vertical | horizontal | diagonal
    fond_image: str | None = None    # image de fond par défaut
    samples_dir: str | None = None   # dossier bibliothèque (défaut: SAMPLES_DIR)
    police: str | None = None        # fichier de police (.ttf/.otf) global
    taille_base: float = 3.8         # taille de texte de référence (% hauteur)
    # Taille (%) mémorisée par style de flèche : {style: taille}
    tailles_fleche: dict = field(default_factory=dict)


def assurer_dossiers() -> None:
    """Crée l'arborescence ~/.tuto-gen/ si nécessaire."""
    for d in (DOSSIER, SAMPLES_DIR, PIPER_DIR):
        d.mkdir(parents=True, exist_ok=True)


def charger() -> Reglages:
    """Charge les réglages persistés (valeurs par défaut si absent/illisible)."""
    assurer_dossiers()
    if FICHIER.is_file():
        try:
            data = json.loads(FICHIER.read_text(encoding="utf-8"))
            champs = {k: data[k] for k in asdict(Reglages()).keys() if k in data}
            return Reglages(**champs)
        except Exception:
            pass
    return Reglages()


def sauver(r: Reglages) -> None:
    """Persiste les réglages dans ~/.tuto-gen/settings.json."""
    assurer_dossiers()
    try:
        FICHIER.write_text(
            json.dumps(asdict(r), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def dossier_samples(r: Reglages) -> Path:
    """Renvoie le dossier de bibliothèque de samples effectif."""
    if r.samples_dir:
        p = Path(r.samples_dir)
        if p.is_dir():
            return p
    return SAMPLES_DIR


def _sources_samples(r: Reglages) -> list[Path]:
    """Dossiers scannés : samples livrés (./assets/samples) + dossier utilisateur."""
    sources = []
    livres = samples_livres()
    if livres:
        sources.append(livres)
    user = dossier_samples(r)
    if user.is_dir() and user not in sources:
        sources.append(user)
    return sources


def lister_samples(r: Reglages) -> list[Path]:
    """Liste les fichiers audio de la bibliothèque (livrés + utilisateur,
    sous-dossiers inclus), sans doublon de chemin."""
    vus: set[Path] = set()
    fichiers: list[Path] = []
    for d in _sources_samples(r):
        for p in d.rglob("*"):
            if p.is_file() and p.suffix.lower() in EXT_AUDIO and p not in vus:
                vus.add(p)
                fichiers.append(p)
    return sorted(fichiers, key=lambda p: str(p).lower())


def label_sample(r: Reglages, p: Path) -> str:
    """Nom affichable d'un sample, relatif à son dossier source.

    Préfixe « ★ » pour les samples livrés avec l'appli.
    """
    livres = samples_livres()
    if livres:
        try:
            return "★ " + str(p.relative_to(livres))
        except ValueError:
            pass
    try:
        return str(p.relative_to(dossier_samples(r)))
    except ValueError:
        return p.name
