"""Parsing et validation du fichier de configuration `tuto.yaml`.

Le YAML est converti en objets typés (dataclasses) pour que le reste du
pipeline manipule des structures claires plutôt que des dictionnaires.
Les chemins relatifs (logo, screenshot) sont résolus par rapport au
dossier qui contient le fichier YAML.

Modèle « sous-séquences » : une scène peut contenir plusieurs captures et
plusieurs narrations, chacune avec un début/fin. La vidéo est ensuite
découpée en sous-segments temporels (cf. `assembler`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .fleches import styles_disponibles as _styles_disponibles

# Types de scènes supportés
TYPES_SCENE = {"title", "screenshot"}

# Positions par défaut (centre du bloc, en % de la slide) du titre et du
# sous-titre selon le type de slide. Les slides "title" centrent le bloc ; les
# slides "screenshot" placent titre/sous-titre dans le bandeau du haut pour ne
# pas masquer la capture.
DEF_POS = {
    "title": {"titre": (50.0, 53.0), "sous_titre": (50.0, 62.0)},
    "screenshot": {"titre": (50.0, 7.0), "sous_titre": (50.0, 13.0)},
}

# Logo par défaut selon le type de slide : (x, y en % de slide, échelle %).
# 'title' : grand logo centré au-dessus du titre.
# 'screenshot' : petit logo en haut à gauche (reproduit l'ancien header).
DEF_LOGO = {
    "title": (50.0, 31.0, 100.0),
    "screenshot": (6.0, 7.5, 50.0),
}

# Styles de flèches disponibles (gabarits SVG, cf. module `fleches`)
STYLES_FLECHE = _styles_disponibles()


class ConfigError(Exception):
    """Erreur de configuration (YAML invalide ou champ manquant)."""


@dataclass
class Annotation:
    """Une flèche ou un highlight dessiné par-dessus un screenshot.

    Les coordonnées sont exprimées en pourcentage (0-100) de la zone
    occupée par le screenshot dans la slide, ce qui rend les annotations
    indépendantes de la résolution réelle de l'image.
    """

    type: str                                  # "arrow" ou "highlight"
    couleur: str = "#FF6B35"
    # Spécifique aux flèches
    de: tuple[float, float] | None = None      # point de départ [x%, y%]
    vers: tuple[float, float] | None = None    # point d'arrivée [x%, y%]
    taille: int = 100                          # échelle de la flèche en % (100 = ajustée)
    style: str = "Fleche1"                     # cf. STYLES_FLECHE
    rotation: float = 0.0                      # rotation supplémentaire en degrés (sens horaire)
    # Spécifique aux highlights
    zone: tuple[float, float, float, float] | None = None  # [x1%, y1%, x2%, y2%]
    opacite: float = 0.4
    # Timing dans la scène (secondes depuis le début)
    debut: float = 0.0
    fin: float | None = None  # None = jusqu'à la fin de la scène


@dataclass
class Narration:
    """Un segment de narration parlé, placé à un instant de la scène."""

    texte: str = ""
    debut: float = 0.0
    fin: float | None = None  # None = la durée du clip audio détermine la fin
    # Surcharges voix par segment (None = hérite des valeurs globales `Meta`).
    vitesse: float | None = None
    expressivite: float | None = None
    # Sous-titre affiché en footer. `afficher_sous_titre=False` masque le
    # sous-titre de ce segment ; `sous_titre` (s'il est non vide) remplace le
    # texte parlé à l'écran (sinon on retombe sur `texte`).
    afficher_sous_titre: bool = True
    sous_titre: str = ""


@dataclass
class Capture:
    """Un screenshot affiché pendant une plage de temps de la scène.

    `decalage_x/y` déplacent la capture (en % de la slide, 0 = centrée dans la
    zone) et `echelle` la zoome (% ; 100 = ajustée à la zone disponible).
    """

    chemin: Path | None = None
    debut: float = 0.0
    fin: float | None = None  # None = jusqu'à la fin de la scène
    decalage_x: float = 0.0
    decalage_y: float = 0.0
    echelle: float = 100.0


@dataclass
class TexteLibre:
    """Un paragraphe de texte libre, positionnable sur n'importe quelle slide.

    Position = centre du bloc, en % de la slide. `taille` est la taille de
    police en % de la hauteur de la slide ; `largeur` la largeur max (%) avant
    retour à la ligne.
    """

    texte: str = ""
    x: float = 50.0
    y: float = 75.0
    taille: float = 3.5              # taille absolue (% slide) si role = "libre"
    role: str = "libre"             # libre | titre | sous_titre | paragraphe
    gras: bool = False
    couleur: str = "#ffffff"
    align: str = "center"            # left | center | right
    largeur: float = 70.0
    debut: float = 0.0
    fin: float | None = None


@dataclass
class Zoom:
    """Mouvement de caméra : zoom progressif sur une zone de la slide.

    `zone` est le cadrage *cible* (entièrement zoomé) [x1, y1, x2, y2], en % de
    la **slide** (`cible="slide"`) ou de la **capture** (`cible="capture"`).
    L'effet occupe la fenêtre [debut, fin] de la scène : rampe d'entrée de
    `entree` s (vue pleine → zone), maintien sur la zone, puis rampe de sortie
    de `sortie` s (zone → vue pleine). Le cadrage est automatiquement ramené au
    format de la cible pour éviter toute déformation : la zone reste toujours
    entièrement visible.

    `cible="slide"` : caméra sur toute l'image composée (capture + textes +
    flèches). `cible="capture"` : seul le screenshot est zoomé, le reste de la
    slide (titre, sous-titre, logo, footer) reste fixe par-dessus.
    """

    zone: tuple[float, float, float, float] = (25.0, 25.0, 75.0, 75.0)
    debut: float = 0.0
    fin: float | None = None  # None = jusqu'à la fin de la scène
    entree: float = 0.6       # durée (s) de la rampe d'entrée (vue → zone)
    sortie: float = 0.6       # durée (s) de la rampe de sortie (zone → vue)
    cible: str = "slide"      # "slide" (toute la slide) | "capture"


@dataclass
class SampleAudio:
    """Un fichier audio joué à un instant précis dans une scène."""

    chemin: Path
    debut: float = 0.0
    volume: float = 1.0
    fin: float | None = None  # None = jouer jusqu'à la fin du fichier


@dataclass
class Scene:
    """Une scène de la vidéo (slide de titre ou slide avec screenshot)."""

    id: str
    type: str
    titre: str = ""
    sous_titre: str = ""
    # Position (centre du bloc, en % de la slide) du titre et du sous-titre
    # sur les slides "title". Les valeurs par défaut reproduisent la mise en
    # page historique (titre centré, sous-titre juste en dessous).
    titre_x: float = 50.0
    titre_y: float = 53.0
    sous_titre_x: float = 50.0
    sous_titre_y: float = 62.0
    # Logo de la slide titre : position (centre, % slide) + échelle (%)
    logo_x: float = 50.0
    logo_y: float = 31.0
    logo_echelle: float = 100.0
    narrations: list[Narration] = field(default_factory=list)
    captures: list[Capture] = field(default_factory=list)
    duree_min: float = 0.0
    annotations: list[Annotation] = field(default_factory=list)
    samples: list[SampleAudio] = field(default_factory=list)
    textes: list[TexteLibre] = field(default_factory=list)
    zooms: list[Zoom] = field(default_factory=list)

    # -- Accès « legacy » pratiques (premier élément) --------------------
    @property
    def narration(self) -> str:
        return self.narrations[0].texte if self.narrations else ""

    @property
    def screenshot(self) -> Path | None:
        return self.captures[0].chemin if self.captures else None

    def a_narration(self) -> bool:
        return any(n.texte.strip() for n in self.narrations)

    def a_sous_titre(self) -> bool:
        """Vrai si au moins une narration affiche un sous-titre (texte parlé
        ou texte personnalisé). Détermine la présence du footer."""
        return any(n.afficher_sous_titre and (n.sous_titre.strip() or n.texte.strip())
                   for n in self.narrations)


@dataclass
class Meta:
    """Métadonnées globales de la vidéo."""

    titre: str
    app: str = ""
    logo: Path | None = None
    couleur_fond: str = "#2D6A4F"
    couleur_accent: str = "#ffffff"
    # Fond : "couleur" | "degrade" | "image"
    fond_type: str = "couleur"
    couleur_fond2: str = "#1B4332"          # 2e couleur du dégradé
    degrade_sens: str = "vertical"          # vertical | horizontal | diagonal
    fond_image: Path | None = None          # image de fond (mode "image")
    resolution: tuple[int, int] = (1920, 1080)
    fps: int = 30
    voix: str = "fr_FR-siwis-medium"
    voix_reference: Path | None = None      # WAV de référence (clonage XTTS)
    voix_speaker: str = ""                  # speaker XTTS intégré ("" = défaut)
    voix_vitesse: float = 1.0               # débit XTTS (speed)
    voix_expressivite: float = 0.75         # variation/expressivité (temperature)
    voix_fluidite: bool = False             # découpage phrase par phrase (prosodie)
    # Dictionnaire de prononciation : terme écrit -> forme à prononcer.
    prononciations: dict[str, str] = field(default_factory=dict)
    police: Path | None = None              # fichier de police global (.ttf/.otf)
    taille_base: float = 3.8                # taille de texte de référence (% h)
    # Bande de sous-titres (légende synchronisée à la narration, en bas)
    sous_titre_fond: str = "#000000"        # couleur de fond de la bande
    sous_titre_fond_opacite: float = 0.55   # opacité 0 (transparent) → 1 (plein)


@dataclass
class Config:
    """Configuration complète issue d'un fichier `tuto.yaml`."""

    meta: Meta
    scenes: list[Scene]
    base_dir: Path  # dossier du YAML, pour résoudre les chemins relatifs


def params_voix(meta: Meta, narration: "Narration | None" = None) -> dict:
    """Paramètres voix effectifs pour une narration, prêts à passer à `tts`.

    Les surcharges de segment (`Narration.vitesse`/`expressivite`) priment sur
    les valeurs globales de `Meta`. Renvoie un dict dont les clés correspondent
    aux paramètres nommés de `tts.synthetiser`/`prechauffer`/`en_cache`/…
    """
    speed = meta.voix_vitesse
    temperature = meta.voix_expressivite
    if narration is not None:
        if narration.vitesse is not None:
            speed = narration.vitesse
        if narration.expressivite is not None:
            temperature = narration.expressivite
    return {
        "ref_voix": meta.voix_reference,
        "speaker": meta.voix_speaker or None,
        "speed": speed,
        "temperature": temperature,
        "fluidite": meta.voix_fluidite,
        "dico": meta.prononciations or None,
    }


def _resolve(base_dir: Path, value: str | None) -> Path | None:
    """Résout un chemin relatif par rapport au dossier du YAML."""
    if not value:
        return None
    p = Path(value)
    return p if p.is_absolute() else (base_dir / p)


def _fin_or_none(value) -> float | None:
    return float(value) if value is not None else None


def charger(chemin_yaml: str | Path) -> Config:
    """Charge et valide un fichier `tuto.yaml`, renvoie un objet `Config`."""
    chemin = Path(chemin_yaml).expanduser().resolve()
    if not chemin.is_file():
        raise ConfigError(f"Fichier introuvable : {chemin}")

    with chemin.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    base_dir = chemin.parent

    # --- meta ---
    meta_brut = data.get("meta") or {}
    if not meta_brut.get("titre"):
        raise ConfigError("meta.titre est obligatoire")

    resolution = meta_brut.get("resolution", [1920, 1080])
    if not (isinstance(resolution, (list, tuple)) and len(resolution) == 2):
        raise ConfigError("meta.resolution doit être [largeur, hauteur]")

    meta = Meta(
        titre=str(meta_brut["titre"]),
        app=str(meta_brut.get("app", "")),
        logo=_resolve(base_dir, meta_brut.get("logo")),
        couleur_fond=str(meta_brut.get("couleur_fond", "#2D6A4F")),
        couleur_accent=str(meta_brut.get("couleur_accent", "#ffffff")),
        fond_type=str(meta_brut.get("fond_type", "couleur")),
        couleur_fond2=str(meta_brut.get("couleur_fond2", "#1B4332")),
        degrade_sens=str(meta_brut.get("degrade_sens", "vertical")),
        fond_image=_resolve(base_dir, meta_brut.get("fond_image")),
        resolution=(int(resolution[0]), int(resolution[1])),
        fps=int(meta_brut.get("fps", 30)),
        voix=str(meta_brut.get("voix", "fr_FR-siwis-medium")),
        voix_reference=_resolve(base_dir, meta_brut.get("voix_reference")),
        voix_speaker=str(meta_brut.get("voix_speaker", "")),
        voix_vitesse=float(meta_brut.get("voix_vitesse", 1.0)),
        voix_expressivite=float(meta_brut.get("voix_expressivite", 0.75)),
        voix_fluidite=bool(meta_brut.get("voix_fluidite", False)),
        prononciations={str(k): str(v) for k, v in
                        (meta_brut.get("prononciations") or {}).items()},
        police=_resolve(base_dir, meta_brut.get("police")),
        taille_base=float(meta_brut.get("taille_base", 3.8)),
        sous_titre_fond=str(meta_brut.get("sous_titre_fond", "#000000")),
        sous_titre_fond_opacite=float(
            meta_brut.get("sous_titre_fond_opacite", 0.55)),
    )

    # --- scenes ---
    scenes_brut = data.get("scenes") or []
    if not scenes_brut:
        raise ConfigError("Au moins une scène est requise dans `scenes`")

    scenes: list[Scene] = []
    for i, s in enumerate(scenes_brut):
        sid = str(s.get("id", f"scene_{i + 1}"))
        stype = str(s.get("type", "")).strip()
        if stype not in TYPES_SCENE:
            raise ConfigError(
                f"Scène '{sid}' : type '{stype}' invalide "
                f"(attendu : {sorted(TYPES_SCENE)})"
            )

        annotations = [
            Annotation(
                type=str(a.get("type", "")),
                couleur=str(a.get("couleur", "#FF6B35")),
                de=tuple(a["de"]) if a.get("de") else None,
                vers=tuple(a["vers"]) if a.get("vers") else None,
                taille=int(a.get("taille", 100)),
                style=str(a.get("style", "Fleche1")),
                rotation=float(a.get("rotation", 0.0)),
                zone=tuple(a["zone"]) if a.get("zone") else None,
                opacite=float(a.get("opacite", 0.4)),
                debut=float(a.get("debut", 0.0)),
                fin=_fin_or_none(a.get("fin")),
            )
            for a in (s.get("annotations") or [])
        ]

        # Narrations : nouvelle liste, ou repli sur l'ancien champ `narration`.
        narrations = [
            Narration(
                texte=str(n.get("texte", "")).strip(),
                debut=float(n.get("debut", 0.0)),
                fin=_fin_or_none(n.get("fin")),
                vitesse=(float(n["vitesse"]) if n.get("vitesse") is not None
                         else None),
                expressivite=(float(n["expressivite"])
                              if n.get("expressivite") is not None else None),
                afficher_sous_titre=bool(n.get("afficher_sous_titre", True)),
                sous_titre=str(n.get("sous_titre", "")).strip(),
            )
            for n in (s.get("narrations") or [])
        ]
        if not narrations and s.get("narration"):
            narrations = [Narration(texte=str(s["narration"]).strip())]

        # Captures : nouvelle liste, ou repli sur l'ancien champ `screenshot`.
        captures = [
            Capture(
                chemin=_resolve(base_dir, c.get("chemin")),
                debut=float(c.get("debut", 0.0)),
                fin=_fin_or_none(c.get("fin")),
                decalage_x=float(c.get("decalage_x", 0.0)),
                decalage_y=float(c.get("decalage_y", 0.0)),
                echelle=float(c.get("echelle", 100.0)),
            )
            for c in (s.get("captures") or [])
        ]
        if not captures and s.get("screenshot"):
            captures = [Capture(chemin=_resolve(base_dir, s["screenshot"]))]

        samples = [
            SampleAudio(
                chemin=_resolve(base_dir, sa.get("chemin")),
                debut=float(sa.get("debut", 0.0)),
                volume=float(sa.get("volume", 1.0)),
                fin=_fin_or_none(sa.get("fin")),
            )
            for sa in (s.get("samples") or [])
            if sa.get("chemin")
        ]

        textes = [
            TexteLibre(
                texte=str(tx.get("texte", "")),
                x=float(tx.get("x", 50.0)),
                y=float(tx.get("y", 75.0)),
                taille=float(tx.get("taille", 3.5)),
                role=str(tx.get("role", "libre")),
                gras=bool(tx.get("gras", False)),
                couleur=str(tx.get("couleur", "#ffffff")),
                align=str(tx.get("align", "center")),
                largeur=float(tx.get("largeur", 70.0)),
                debut=float(tx.get("debut", 0.0)),
                fin=_fin_or_none(tx.get("fin")),
            )
            for tx in (s.get("textes") or [])
        ]

        zooms = [
            Zoom(
                zone=tuple(z["zone"]) if z.get("zone") else (25.0, 25.0, 75.0, 75.0),
                debut=float(z.get("debut", 0.0)),
                fin=_fin_or_none(z.get("fin")),
                entree=float(z.get("entree", 0.6)),
                sortie=float(z.get("sortie", 0.6)),
                cible=str(z.get("cible", "slide")),
            )
            for z in (s.get("zooms") or [])
        ]

        dpos = DEF_POS.get(stype, DEF_POS["title"])
        dlogo = DEF_LOGO.get(stype, DEF_LOGO["title"])
        scenes.append(Scene(
            id=sid,
            type=stype,
            titre=str(s.get("titre", "")),
            sous_titre=str(s.get("sous_titre", "")),
            titre_x=float(s.get("titre_x", dpos["titre"][0])),
            titre_y=float(s.get("titre_y", dpos["titre"][1])),
            sous_titre_x=float(s.get("sous_titre_x", dpos["sous_titre"][0])),
            sous_titre_y=float(s.get("sous_titre_y", dpos["sous_titre"][1])),
            logo_x=float(s.get("logo_x", dlogo[0])),
            logo_y=float(s.get("logo_y", dlogo[1])),
            logo_echelle=float(s.get("logo_echelle", dlogo[2])),
            narrations=narrations,
            captures=captures,
            duree_min=float(s.get("duree_min", 0.0)),
            annotations=annotations,
            samples=samples,
            textes=textes,
            zooms=zooms,
        ))

    return Config(meta=meta, scenes=scenes, base_dir=base_dir)


def _rel(base_dir: Path, p: Path | None) -> str | None:
    """Exprime un chemin relativement au YAML si possible, sinon en absolu."""
    if p is None:
        return None
    p = Path(p)
    try:
        return str(p.relative_to(base_dir))
    except ValueError:
        return str(p)


def vers_dict(cfg: Config, base_dir: Path | None = None) -> dict:
    """Sérialise un `Config` en dictionnaire prêt pour `yaml.safe_dump`."""
    base = base_dir or cfg.base_dir
    meta = {
        "titre": cfg.meta.titre,
        "app": cfg.meta.app,
        "logo": _rel(base, cfg.meta.logo),
        "couleur_fond": cfg.meta.couleur_fond,
        "couleur_accent": cfg.meta.couleur_accent,
        "resolution": list(cfg.meta.resolution),
        "fps": cfg.meta.fps,
        "voix": cfg.meta.voix,
    }
    if cfg.meta.fond_type != "couleur":
        meta["fond_type"] = cfg.meta.fond_type
    if cfg.meta.fond_type == "degrade":
        meta["couleur_fond2"] = cfg.meta.couleur_fond2
        meta["degrade_sens"] = cfg.meta.degrade_sens
    if cfg.meta.fond_image:
        meta["fond_image"] = _rel(base, cfg.meta.fond_image)
    if cfg.meta.voix_reference:
        meta["voix_reference"] = _rel(base, cfg.meta.voix_reference)
    if cfg.meta.voix_speaker:
        meta["voix_speaker"] = cfg.meta.voix_speaker
    if cfg.meta.prononciations:
        meta["prononciations"] = dict(cfg.meta.prononciations)
    if cfg.meta.voix_vitesse != 1.0:
        meta["voix_vitesse"] = round(cfg.meta.voix_vitesse, 2)
    if cfg.meta.voix_expressivite != 0.75:
        meta["voix_expressivite"] = round(cfg.meta.voix_expressivite, 2)
    if cfg.meta.voix_fluidite:
        meta["voix_fluidite"] = True
    if cfg.meta.sous_titre_fond != "#000000":
        meta["sous_titre_fond"] = cfg.meta.sous_titre_fond
    if cfg.meta.sous_titre_fond_opacite != 0.55:
        meta["sous_titre_fond_opacite"] = round(cfg.meta.sous_titre_fond_opacite, 2)
    if cfg.meta.police:
        meta["police"] = _rel(base, cfg.meta.police)
    if cfg.meta.taille_base != 3.8:
        meta["taille_base"] = cfg.meta.taille_base
    scenes = []
    for s in cfg.scenes:
        d: dict = {"id": s.id, "type": s.type, "titre": s.titre}
        # Titre / sous-titre et leurs positions : communs à tous les types de
        # slide (les défauts de position dépendent du type, cf. DEF_POS).
        dpos = DEF_POS.get(s.type, DEF_POS["title"])
        if s.sous_titre:
            d["sous_titre"] = s.sous_titre
        if (round(s.titre_x, 1), round(s.titre_y, 1)) != dpos["titre"]:
            d["titre_x"] = round(s.titre_x, 1)
            d["titre_y"] = round(s.titre_y, 1)
        if (s.sous_titre and (round(s.sous_titre_x, 1),
                              round(s.sous_titre_y, 1)) != dpos["sous_titre"]):
            d["sous_titre_x"] = round(s.sous_titre_x, 1)
            d["sous_titre_y"] = round(s.sous_titre_y, 1)
        # Logo positionnable sur les slides titre ET capture ; on ne sérialise
        # que les écarts au défaut propre au type (cf. DEF_LOGO).
        dlogo = DEF_LOGO.get(s.type, DEF_LOGO["title"])
        if (round(s.logo_x, 1), round(s.logo_y, 1)) != dlogo[:2]:
            d["logo_x"] = round(s.logo_x, 1)
            d["logo_y"] = round(s.logo_y, 1)
        if round(s.logo_echelle, 1) != dlogo[2]:
            d["logo_echelle"] = round(s.logo_echelle, 1)
        if s.type == "screenshot" and s.captures:
            d["captures"] = [
                {"chemin": _rel(base, c.chemin), "debut": c.debut,
                 **({"fin": c.fin} if c.fin is not None else {}),
                 **({"decalage_x": round(c.decalage_x, 1)} if c.decalage_x else {}),
                 **({"decalage_y": round(c.decalage_y, 1)} if c.decalage_y else {}),
                 **({"echelle": round(c.echelle, 1)} if c.echelle != 100.0 else {})}
                for c in s.captures
            ]
        if s.narrations:
            d["narrations"] = [
                {"texte": n.texte, "debut": n.debut,
                 **({"fin": n.fin} if n.fin is not None else {}),
                 **({"vitesse": round(n.vitesse, 2)} if n.vitesse is not None else {}),
                 **({"expressivite": round(n.expressivite, 2)}
                    if n.expressivite is not None else {}),
                 **({} if n.afficher_sous_titre else {"afficher_sous_titre": False}),
                 **({"sous_titre": n.sous_titre} if n.sous_titre.strip() else {})}
                for n in s.narrations
            ]
        if s.duree_min:
            d["duree_min"] = s.duree_min
        if s.annotations:
            annos = []
            for a in s.annotations:
                ad: dict = {"type": a.type, "couleur": a.couleur}
                if a.type == "arrow":
                    ad["de"] = list(a.de) if a.de else None
                    ad["vers"] = list(a.vers) if a.vers else None
                    if a.taille != 100:
                        ad["taille"] = a.taille
                    if a.style != "skitch":
                        ad["style"] = a.style
                    if getattr(a, "rotation", 0.0):
                        ad["rotation"] = round(a.rotation, 1)
                elif a.type == "highlight":
                    ad["zone"] = list(a.zone) if a.zone else None
                    ad["opacite"] = a.opacite
                if a.debut:
                    ad["debut"] = a.debut
                if a.fin is not None:
                    ad["fin"] = a.fin
                annos.append(ad)
            d["annotations"] = annos
        if s.samples:
            d["samples"] = [
                {"chemin": _rel(base, sa.chemin), "debut": sa.debut,
                 **({"volume": sa.volume} if sa.volume != 1.0 else {}),
                 **({"fin": sa.fin} if sa.fin is not None else {})}
                for sa in s.samples
            ]
        if s.textes:
            d["textes"] = [
                {"texte": tx.texte, "x": round(tx.x, 1), "y": round(tx.y, 1),
                 "taille": tx.taille,
                 **({"role": tx.role} if tx.role != "libre" else {}),
                 **({"gras": True} if tx.gras else {}),
                 "couleur": tx.couleur, "align": tx.align,
                 "largeur": tx.largeur, "debut": tx.debut,
                 **({"fin": tx.fin} if tx.fin is not None else {})}
                for tx in s.textes
            ]
        if s.zooms:
            d["zooms"] = [
                {"zone": [round(v, 1) for v in z.zone], "debut": z.debut,
                 **({"fin": z.fin} if z.fin is not None else {}),
                 **({"entree": round(z.entree, 2)} if z.entree != 0.6 else {}),
                 **({"sortie": round(z.sortie, 2)} if z.sortie != 0.6 else {}),
                 **({"cible": z.cible} if z.cible != "slide" else {})}
                for z in s.zooms
            ]
        scenes.append(d)
    return {"meta": meta, "scenes": scenes}


def sauver(cfg: Config, chemin: str | Path) -> None:
    """Enregistre un `Config` dans un fichier `tuto.yaml`."""
    chemin = Path(chemin)
    data = vers_dict(cfg, base_dir=chemin.parent)
    with chemin.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
