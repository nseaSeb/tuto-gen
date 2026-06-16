"""Assemblage vidéo final avec moviepy (API 2.x).

Modèle « sous-séquences » : chaque scène est découpée en sous-segments
temporels aux points de changement (apparition/disparition d'une capture
ou d'une annotation). Un `ImageClip` est rendu pour chaque sous-segment,
puis la piste audio de la scène (narrations + samples, chacun placé à son
`debut`) est composée par-dessus.

Règles de durée d'une scène :
- `duree_min` fait foi si défini ; on l'étend si l'audio/les éléments la
  dépassent.
- à défaut, on dérive la durée des éléments présents (audio, captures,
  annotations, samples), avec un repli de `DUREE_DEFAUT`.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from moviepy import (
    AudioFileClip,
    CompositeAudioClip,
    ImageClip,
    VideoClip,
    concatenate_videoclips,
)

from . import composer
from .config import Meta, Scene
from .tts import ClipAudio

SILENCE_FIN = 0.4   # marge ajoutée quand la durée est dérivée de l'audio
DUREE_DEFAUT = 3.0  # durée d'une scène sans narration ni duree_min


@dataclass
class NarrationRendue:
    """Un segment de narration synthétisé, avec son instant de départ."""

    clip: ClipAudio
    debut: float = 0.0


@dataclass
class SceneRendue:
    """Une scène prête à assembler : audio des narrations synthétisées."""

    scene: Scene
    narrations: list[NarrationRendue] = field(default_factory=list)


def _sample_duree(chemin) -> float:
    try:
        import soundfile as sf
        return sf.info(str(chemin)).duration
    except Exception:
        return 2.0


def duree_scene(rendu: SceneRendue) -> float:
    """Durée d'affichage d'une scène (couvre toujours ses éléments)."""
    scene = rendu.scene
    besoin = 0.0
    for nr in rendu.narrations:
        besoin = max(besoin, nr.debut + nr.clip.duree)
    for c in scene.captures:
        if c.fin is not None:
            besoin = max(besoin, c.fin)
    for a in scene.annotations:
        if a.fin is not None:
            besoin = max(besoin, a.fin)
    for sa in scene.samples:
        fin = sa.fin if sa.fin is not None else sa.debut + _sample_duree(sa.chemin)
        besoin = max(besoin, fin)
    for tx in scene.textes:
        if tx.fin is not None:
            besoin = max(besoin, tx.fin)
    for z in scene.zooms:
        if z.fin is not None:
            besoin = max(besoin, z.fin)

    if scene.duree_min and scene.duree_min > 0:
        return max(scene.duree_min, besoin)
    if besoin > 0:
        return besoin + SILENCE_FIN
    return DUREE_DEFAUT


def _points_de_coupe(scene: Scene, duree: float) -> list[float]:
    """Instants où l'image change (bornes des captures et annotations)."""
    pts = {0.0, duree}
    for c in scene.captures:
        pts.add(max(0.0, min(duree, c.debut)))
        pts.add(max(0.0, min(duree, c.fin if c.fin is not None else duree)))
    for a in scene.annotations:
        pts.add(max(0.0, min(duree, a.debut)))
        pts.add(max(0.0, min(duree, a.fin if a.fin is not None else duree)))
    for tx in scene.textes:
        pts.add(max(0.0, min(duree, tx.debut)))
        pts.add(max(0.0, min(duree, tx.fin if tx.fin is not None else duree)))
    for z in scene.zooms:
        pts.add(max(0.0, min(duree, z.debut)))
        pts.add(max(0.0, min(duree, z.fin if z.fin is not None else duree)))
    return sorted(pts)


def _zoom_actif(scene: Scene, t0: float, t1: float, duree: float) -> bool:
    """Vrai si un zoom est en mouvement pendant l'intervalle ]t0, t1[."""
    for z in scene.zooms:
        fin = z.fin if z.fin is not None else duree
        if z.debut < t1 and fin > t0:
            return True
    return False


def _segment_zoom(scene: Scene, meta: Meta, base, t0: float, t1: float,
                  duree: float):
    """Clip animé d'un sous-segment traversé par un zoom.

    Le contenu est figé sur l'intervalle (bornes posées aux points de coupe) :
    on compose l'image une seule fois et on ne fait que recadrer/redimensionner
    par frame selon l'avancement du zoom — peu coûteux."""
    def frame_function(tl):
        tr = composer.zoom_transform(scene, meta, t0 + tl, duree)
        if tr is None:
            return np.asarray(base)
        return np.asarray(composer.appliquer_zoom(base, *tr))

    return VideoClip(frame_function=frame_function, duration=t1 - t0)


def _clip_video_scene(rendu: SceneRendue, meta: Meta, duree: float):
    """Construit le clip vidéo (sans audio) d'une scène par sous-segments."""
    scene = rendu.scene
    if scene.type == "title" and not scene.zooms:
        frame = np.asarray(composer.composer_scene(scene, meta, 0.0).convert("RGB"))
        return ImageClip(frame).with_duration(duree)

    pts = _points_de_coupe(scene, duree)
    segments = []
    for t0, t1 in zip(pts, pts[1:]):
        if t1 - t0 < 1e-3:
            continue
        milieu = (t0 + t1) / 2
        base = composer.composer_scene(scene, meta, milieu).convert("RGB")
        if scene.zooms and _zoom_actif(scene, t0, t1, duree):
            segments.append(_segment_zoom(scene, meta, base, t0, t1, duree))
        else:
            segments.append(ImageClip(np.asarray(base)).with_duration(t1 - t0))

    if not segments:
        frame = np.asarray(composer.composer_scene(scene, meta, 0.0).convert("RGB"))
        return ImageClip(frame).with_duration(duree)
    if len(segments) == 1:
        return segments[0]
    return concatenate_videoclips(segments, method="chain")


def _audio_scene(rendu: SceneRendue, duree: float, a_fermer: list):
    """Compose la piste audio d'une scène (narrations + samples placés)."""
    pistes = []
    for nr in rendu.narrations:
        ac = AudioFileClip(str(nr.clip.chemin)).with_start(nr.debut)
        a_fermer.append(ac)
        pistes.append(ac)

    for sa in rendu.scene.samples:
        if not sa.chemin or not Path(sa.chemin).is_file():
            if sa.chemin:
                print(f"   ⚠ sample introuvable, ignoré : {sa.chemin}")
            continue
        ac = AudioFileClip(str(sa.chemin))
        a_fermer.append(ac)
        if sa.fin is not None:
            fin = min(sa.fin, sa.debut + ac.duration)
            if fin > sa.debut:
                ac = ac.subclipped(0, min(ac.duration, fin - sa.debut))
        if sa.volume != 1.0:
            ac = ac.with_volume_scaled(max(0.0, sa.volume))
        ac = ac.with_start(sa.debut)
        pistes.append(ac)

    if not pistes:
        return None
    comp = CompositeAudioClip(pistes)
    a_fermer.append(comp)
    return comp


def assembler(
    rendus: list[SceneRendue],
    meta: Meta,
    sortie: Path,
    fps: int,
    verbose: bool = False,
) -> tuple[float, tuple[int, int]]:
    """Assemble les scènes en un MP4. Renvoie (durée totale, résolution)."""
    clips = []
    a_fermer = []

    for rendu in rendus:
        duree = duree_scene(rendu)
        clip = _clip_video_scene(rendu, meta, duree)
        audio = _audio_scene(rendu, duree, a_fermer)
        if audio is not None:
            clip = clip.with_audio(audio)
        clips.append(clip)

    final = concatenate_videoclips(clips, method="chain")
    sortie.parent.mkdir(parents=True, exist_ok=True)

    # MoviePy écrit un fichier audio temporaire ; par défaut dans le dossier
    # courant, qui est en lecture seule quand on tourne depuis un bundle .app.
    # On le force dans le dossier temporaire système, toujours inscriptible.
    temp_audio = Path(tempfile.gettempdir()) / f"{sortie.stem}_TEMP_MPY_snd.m4a"

    final.write_videofile(
        str(sortie),
        fps=fps,
        codec="libx264",
        audio_codec="aac",
        temp_audiofile=str(temp_audio),
        logger="bar" if verbose else None,
    )

    duree_totale = final.duration
    resolution = (final.w, final.h)

    final.close()
    for c in a_fermer:
        try:
            c.close()
        except Exception:
            pass

    return duree_totale, resolution
