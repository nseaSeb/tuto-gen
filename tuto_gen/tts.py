"""Génération audio (Text-To-Speech) à partir des narrations.

Backend unique : **XTTS-v2 (Coqui)** — voix française clonable, qualité
maximale, 100 % hors-ligne (modèle embarqué dans `assets/xtts/`). Approche
« qualité ou rien » : aucun repli dégradé. Si XTTS est indisponible ou échoue,
la synthèse échoue explicitement.

Chaque scène avec narration produit un fichier audio et on renvoie sa durée
exacte pour synchroniser la slide correspondante.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf

from . import voix_texte


@dataclass
class ClipAudio:
    """Résultat d'une synthèse : chemin du fichier audio + durée (s)."""

    chemin: Path
    duree: float
    backend: str


# --------------------------------------------------------------------------
# Backend XTTS-v2 (Coqui) — qualité maximale, voix clonable, 100 % local
# --------------------------------------------------------------------------

# Modèle Coqui XTTS-v2 (téléchargé à la demande si non embarqué).
XTTS_MODELE = "tts_models/multilingual/multi-dataset/xtts_v2"
# Voix intégrée par défaut quand aucune voix de référence n'est fournie.
XTTS_SPEAKER_DEFAUT = "Claribel Dervla"

# Speakers studio intégrés à XTTS-v2. Liste figée pour ce modèle : on la code
# en dur pour peupler l'interface sans charger le modèle (~1,8 Go). Vérifiable
# au runtime via `_charger_xtts().speakers`.
XTTS_SPEAKERS: tuple[str, ...] = (
    "Claribel Dervla", "Daisy Studious", "Gracie Wise", "Tammie Ema",
    "Alison Dietlinde", "Ana Florence", "Annmarie Nele", "Asya Anara",
    "Brenda Stern", "Gitta Nikolina", "Henriette Usha", "Sofia Hellen",
    "Tammy Grit", "Tanja Adelina", "Vjollca Johnnie", "Andrew Chipper",
    "Badr Odhiambo", "Dionisio Schuyler", "Royston Min", "Viktor Eka",
    "Abrahan Mack", "Adde Michal", "Baldur Sanjin", "Craig Gutsy",
    "Damien Black", "Gilberto Mathias", "Ilkin Urabella", "Kazuhiko Atallah",
    "Ludvig Milivoj", "Suad Qasim", "Torcull Diarmuid", "Viktor Menelaos",
    "Zacharie Aimilios", "Nova Hogarth", "Maja Ruoho", "Uta Obando",
    "Lidiya Szekeres", "Chandra MacFarland", "Szofi Granger",
    "Camilla Holmström", "Lilya Stainthorpe", "Zofija Kendrick",
    "Narelle Moon", "Barbora MacLean", "Alexandra Hisakawa", "Alma María",
    "Rosemary Okafor", "Ige Behringer", "Filip Traverse", "Damjan Chapman",
    "Wulf Carlevaro", "Aaron Dreschner", "Kumar Dahl", "Eugenio Mataracı",
    "Ferran Simen", "Xavier Hayasaka", "Luis Moray", "Marcos Rudaski",
)

_xtts_cache = None  # modèle TTS chargé (chargement lent → mis en cache)


def _preparer_compat_xtts() -> None:
    """Compatibilité coqui-tts ↔ transformers récents.

    transformers 5.x a retiré `isin_mps_friendly` de `pytorch_utils`, encore
    importé par les couches Tortoise/XTTS de coqui-tts. On réinjecte un
    équivalent avant tout import de `TTS` (sans effet si déjà présent)."""
    try:
        import transformers.pytorch_utils as _pu
        if not hasattr(_pu, "isin_mps_friendly"):
            import torch
            _pu.isin_mps_friendly = lambda elements, test_elements: torch.isin(
                elements, test_elements)
    except Exception:
        pass
    # Silence les avertissements bénins de transformers (bos/eos_token_id du GPT
    # interne de XTTS), sans incidence sur l'audio produit.
    try:
        import transformers
        transformers.logging.set_verbosity_error()
    except Exception:
        pass


def _xtts_disponible() -> bool:
    try:
        _preparer_compat_xtts()
        import TTS  # noqa: F401
        return True
    except Exception:
        return False


def backend_actif() -> str:
    """Renvoie le nom du backend qui sera utilisé (XTTS uniquement)."""
    return "xtts" if _xtts_disponible() else "aucun"


def modele_local_disponible() -> bool:
    """True si le modèle XTTS est utilisable **sans réseau** : soit embarqué
    (assets/xtts), soit déjà téléchargé dans le cache utilisateur de coqui-tts.

    Le modèle n'étant plus embarqué dans le build, il est récupéré au 1er
    lancement ; ce booléen permet à l'UI de prévenir l'utilisateur du
    téléchargement initial (~1,8 Go). Renvoie False par prudence si le chemin du
    cache ne peut être résolu (au pire, on affiche le message à tort)."""
    if _xtts_dossier_bundle() is not None:
        return True
    try:
        from trainer.io import get_user_data_dir
        d = (Path(get_user_data_dir("tts"))
             / "tts_models--multilingual--multi-dataset--xtts_v2")
        return (d / "model.pth").is_file()
    except Exception:
        return False


# Taille de référence du modèle XTTS-v2 complet (octets), pour estimer la
# progression du téléchargement initial : la lib Coqui télécharge chaque fichier
# en streaming dans le dossier cible mais n'expose pas de callback. On suit donc
# la taille du dossier cache rapportée à ce total (model.pth + speakers + vocab
# + config + hash).
XTTS_TAILLE_TOTALE = 1_876_500_000  # ~1,88 Go


def dossier_modele_cache() -> Path | None:
    """Dossier où Coqui télécharge XTTS-v2 (cache utilisateur), ou None.

    Indépendant du bundle : c'est là qu'atterrit le modèle quand il n'est pas
    embarqué (cas de l'app distribuée)."""
    try:
        from trainer.io import get_user_data_dir
        return (Path(get_user_data_dir("tts"))
                / "tts_models--multilingual--multi-dataset--xtts_v2")
    except Exception:
        return None


def taille_modele_cache() -> int:
    """Octets actuellement présents dans le cache du modèle (0 si absent).

    Croît de façon monotone pendant le téléchargement → sert de jauge de
    progression."""
    d = dossier_modele_cache()
    if d is None or not d.is_dir():
        return 0
    total = 0
    try:
        for f in d.iterdir():
            if f.is_file():
                total += f.stat().st_size
    except OSError:
        pass
    return total


def etat_moteur() -> dict:
    """État du moteur vocal pour l'UI, sans import lourd de `TTS`.

    Clés : `pret` (modèle utilisable hors-ligne), `embarque` (livré dans le
    bundle), `octets` (présents dans le cache), `octets_total` (référence)."""
    return {
        "pret": modele_local_disponible(),
        "embarque": _xtts_dossier_bundle() is not None,
        "octets": taille_modele_cache(),
        "octets_total": XTTS_TAILLE_TOTALE,
    }


def telecharger_modele() -> None:
    """Télécharge le modèle XTTS-v2 dans le cache utilisateur (sans le charger
    en RAM). Bloquant : à lancer dans un thread, en suivant la progression via
    `taille_modele_cache()`. Idempotent : si le modèle est déjà présent et
    valide, Coqui n'effectue aucun nouveau téléchargement.

    Lève une exception si le téléchargement échoue (réseau indisponible, etc.).
    """
    os.environ.setdefault("COQUI_TOS_AGREED", "1")
    _preparer_compat_xtts()
    from TTS.utils.manage import ModelManager
    ModelManager(progress_bar=False).download_model(XTTS_MODELE)


def _xtts_dossier_bundle() -> Path | None:
    """Dossier du modèle XTTS livré avec l'appli (assets/xtts), si présent.

    Résolu en mode source (racine du dépôt) comme en bundle PyInstaller
    (sys._MEIPASS). Permet un fonctionnement 100 % hors-ligne.
    """
    import sys
    cands = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cands.append(Path(base) / "assets" / "xtts")
    cands.append(Path(__file__).resolve().parent.parent / "assets" / "xtts")
    for c in cands:
        if (c / "config.json").is_file():
            return c
    return None


def _voix_reference_bundle() -> Path | None:
    """Première voix de référence livrée (assets/voices/*.wav), si présente."""
    import sys
    cands = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cands.append(Path(base) / "assets" / "voices")
    cands.append(Path(__file__).resolve().parent.parent / "assets" / "voices")
    for c in cands:
        if c.is_dir():
            wavs = sorted(c.glob("*.wav"))
            if wavs:
                return wavs[0]
    return None


def _charger_xtts():
    global _xtts_cache
    if _xtts_cache is None:
        import os
        # Évite l'invite interactive d'acceptation de licence Coqui (CPML).
        os.environ.setdefault("COQUI_TOS_AGREED", "1")
        _preparer_compat_xtts()
        from TTS.api import TTS as _TTSApi
        dossier = _xtts_dossier_bundle()
        if dossier is not None:
            api = _TTSApi(model_path=str(dossier),
                          config_path=str(dossier / "config.json"),
                          progress_bar=False)
        else:
            api = _TTSApi(XTTS_MODELE, progress_bar=False)
        # Périphérique : CPU par défaut (sûr) ; MPS/CUDA via TUTO_XTTS_DEVICE.
        device = os.environ.get("TUTO_XTTS_DEVICE", "cpu")
        if device != "cpu":
            try:
                api.to(device)
            except Exception as e:
                print(f"    ⚠ XTTS device '{device}' indisponible ({e}); CPU.")
        _xtts_cache = api
    return _xtts_cache


def _synth_xtts(texte: str, out: Path, ref_wav=None, speaker: str | None = None,
                speed: float = 1.0, temperature: float = 0.75,
                enable_text_splitting: bool = False) -> ClipAudio:
    api = _charger_xtts()
    wav = out.with_suffix(".wav")
    kwargs = {"text": texte, "language": "fr", "file_path": str(wav),
              "speed": speed, "temperature": temperature,
              "enable_text_splitting": enable_text_splitting}
    if ref_wav and Path(ref_wav).is_file():
        kwargs["speaker_wav"] = str(ref_wav)
        backend = f"xtts (clone: {Path(ref_wav).stem})"
    else:
        kwargs["speaker"] = speaker or XTTS_SPEAKER_DEFAUT
        backend = f"xtts ({kwargs['speaker']})"
    api.tts_to_file(**kwargs)
    info = sf.info(str(wav))
    return ClipAudio(chemin=wav, duree=info.duration, backend=backend)


# --------------------------------------------------------------------------
# Cache audio (clé = texte + voix de référence + paramètres)
# --------------------------------------------------------------------------

# Version du moteur : invalide le cache si le modèle/format change.
_CACHE_VERSION = "xtts_v2"

_prewarm_lock = threading.Lock()
_prewarm_inflight: set[str] = set()


def _cache_dir() -> Path:
    d = Path.home() / ".tuto-gen" / "tts_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ref_signature(ref, speaker: str | None) -> str:
    """Empreinte de la voix : wav de référence (clone) ou speaker intégré."""
    if ref and Path(ref).is_file():
        st = Path(ref).stat()
        return f"{Path(ref).name}:{st.st_size}:{int(st.st_mtime)}"
    return f"speaker:{speaker or XTTS_SPEAKER_DEFAUT}"


def _cache_key(texte: str, ref, speed: float, temperature: float,
               fluidite: bool, speaker: str | None = None) -> str:
    norm = " ".join(texte.split())
    raw = "|".join([_CACHE_VERSION, norm, _ref_signature(ref, speaker),
                    f"{speed:.3f}", f"{temperature:.3f}", str(bool(fluidite))])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def en_cache(texte: str, ref_voix=None, speed: float = 1.0,
             temperature: float = 0.75, fluidite: bool = False,
             speaker: str | None = None, dico=None) -> bool:
    """True si l'audio de cette narration (avec ces paramètres) est déjà en cache."""
    texte = voix_texte.normaliser(texte, dico)
    if not texte:
        return False
    ref = ref_voix or _voix_reference_bundle()
    key = _cache_key(texte, ref, speed, temperature, fluidite, speaker)
    return (_cache_dir() / f"{key}.wav").is_file()


def duree_cache(texte: str, ref_voix=None, speed: float = 1.0,
                temperature: float = 0.75, fluidite: bool = False,
                speaker: str | None = None, dico=None) -> float | None:
    """Durée (s) de l'audio en cache pour cette narration, ou `None` si absent.

    Permet à la timeline d'afficher la piste narration à sa longueur audio réelle
    (sans resynthèse) dès que l'audio a été pré-généré."""
    texte = voix_texte.normaliser(texte, dico)
    if not texte:
        return None
    ref = ref_voix or _voix_reference_bundle()
    key = _cache_key(texte, ref, speed, temperature, fluidite, speaker)
    f = _cache_dir() / f"{key}.wav"
    if not f.is_file():
        return None
    try:
        return float(sf.info(str(f)).duration)
    except Exception:
        return None


def _synth_cached(texte: str, ref, speed: float, temperature: float,
                  fluidite: bool, speaker: str | None = None) -> tuple[Path, bool]:
    """Renvoie (chemin_cache, hit). Synthétise dans le cache si absent.

    `texte` doit déjà être normalisé (cf. `voix_texte.normaliser`)."""
    cached = _cache_dir() / f"{_cache_key(texte, ref, speed, temperature, fluidite, speaker)}.wav"
    if cached.is_file():
        return cached, True
    fd, tmpname = tempfile.mkstemp(suffix=".wav", dir=_cache_dir())
    os.close(fd)
    tmp = Path(tmpname)
    try:
        clip = _synth_xtts(texte, tmp, ref_wav=ref, speaker=speaker, speed=speed,
                           temperature=temperature, enable_text_splitting=fluidite)
        os.replace(clip.chemin, cached)
    finally:
        tmp.unlink(missing_ok=True)
    return cached, False


def prechauffer(texte: str, ref_voix=None, speed: float = 1.0,
                temperature: float = 0.75, fluidite: bool = False,
                speaker: str | None = None, dico=None) -> bool:
    """Pré-génère (en arrière-plan) l'audio d'une narration dans le cache.

    Appelé au « blur » du champ narration pour que la génération de la vidéo
    réutilise un audio déjà prêt. Renvoie True si une synthèse a réellement eu
    lieu, False si rien à faire (texte vide, moteur absent, déjà en cache ou en
    cours de synthèse).

    **Lève une exception si la synthèse elle-même échoue** : l'appelant peut
    ainsi afficher la raison de l'échec à l'utilisateur (la synthèse silencieuse
    masquait jadis l'erreur en renvoyant simplement False)."""
    texte = voix_texte.normaliser(texte, dico)
    if not texte or not _xtts_disponible():
        return False
    ref = ref_voix or _voix_reference_bundle()
    key = _cache_key(texte, ref, speed, temperature, fluidite, speaker)
    if (_cache_dir() / f"{key}.wav").is_file():
        return False
    with _prewarm_lock:
        if key in _prewarm_inflight:
            return False
        _prewarm_inflight.add(key)
    try:
        _synth_cached(texte, ref, speed, temperature, fluidite, speaker)
        return True
    finally:
        with _prewarm_lock:
            _prewarm_inflight.discard(key)


def regenerer(texte: str, ref_voix=None, speed: float = 1.0,
              temperature: float = 0.75, fluidite: bool = False,
              speaker: str | None = None, dico=None) -> float | None:
    """Force une **nouvelle** synthèse (re-take) en invalidant le cache.

    XTTS échantillonne de façon stochastique : supprimer l'entrée de cache puis
    resynthétiser produit une prise différente — pratique pour écarter un
    artefact ponctuel. Renvoie la durée du nouvel audio, ou `None` si rien à
    faire (texte vide / moteur absent)."""
    texte = voix_texte.normaliser(texte, dico)
    if not texte or not _xtts_disponible():
        return None
    ref = ref_voix or _voix_reference_bundle()
    key = _cache_key(texte, ref, speed, temperature, fluidite, speaker)
    (_cache_dir() / f"{key}.wav").unlink(missing_ok=True)
    cached, _ = _synth_cached(texte, ref, speed, temperature, fluidite, speaker)
    try:
        return float(sf.info(str(cached)).duration)
    except Exception:
        return None


# --------------------------------------------------------------------------
# API publique
# --------------------------------------------------------------------------

def synthetiser(texte: str, voix: str | None, out: Path, ref_voix=None,
                speed: float = 1.0, temperature: float = 0.75,
                fluidite: bool = False, speaker: str | None = None,
                dico=None) -> ClipAudio | None:
    """Synthétise `texte` vers un fichier audio avec XTTS (via le cache).

    Renvoie un `ClipAudio` (chemin + durée), ou `None` si le texte est vide.
    Approche « qualité ou rien » : si XTTS est indisponible ou échoue, une
    exception est levée (aucun repli dégradé). `voix` est ignoré (la voix est
    déterminée par `ref_voix` ou la voix de référence livrée).

    L'audio est mis en cache (clé = texte + voix de référence + paramètres) :
    une narration inchangée n'est jamais resynthétisée d'une génération à
    l'autre. Le fichier rendu est copié vers `out` (le cache reste intact).
    """
    texte = voix_texte.normaliser(texte, dico)
    if not texte:
        return None

    if not _xtts_disponible():
        raise RuntimeError(
            "Moteur vocal XTTS indisponible : installez `coqui-tts` "
            "(+ torchaudio, torchcodec) et embarquez le modèle dans assets/xtts."
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    ref = ref_voix or _voix_reference_bundle()
    cached, hit = _synth_cached(texte, ref, speed, temperature, fluidite, speaker)
    out_wav = out.with_suffix(".wav")
    shutil.copyfile(cached, out_wav)
    info = sf.info(str(out_wav))
    return ClipAudio(chemin=out_wav, duree=info.duration,
                     backend="xtts (cache)" if hit else "xtts")


def _ffmpeg_exe() -> str:
    """Chemin de l'exécutable ffmpeg (bundled via imageio_ffmpeg, sinon système)."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg") or "ffmpeg"


def exporter_mp3(texte: str, voix: str | None, out_mp3: Path,
                 bitrate: str = "192k", ref_voix=None, speed: float = 1.0,
                 temperature: float = 0.75, fluidite: bool = False,
                 speaker: str | None = None, dico=None) -> ClipAudio | None:
    """Synthétise `texte` et écrit directement un fichier MP3.

    Pratique pour réutiliser les narrations dans un logiciel de montage.
    Renvoie un `ClipAudio` pointant sur le MP3, ou `None` si le texte est vide.
    """
    texte = (texte or "").strip()
    if not texte:
        return None

    out_mp3 = Path(out_mp3)
    out_mp3.parent.mkdir(parents=True, exist_ok=True)

    # 1) synthèse vers un fichier temporaire
    tmp = out_mp3.with_suffix(".tmp.wav")
    clip = synthetiser(texte, voix, tmp, ref_voix=ref_voix, speed=speed,
                       temperature=temperature, fluidite=fluidite,
                       speaker=speaker, dico=dico)
    if clip is None:
        return None

    # 2) conversion en MP3 via ffmpeg
    subprocess.run(
        [_ffmpeg_exe(), "-y", "-i", str(clip.chemin),
         "-codec:a", "libmp3lame", "-b:a", bitrate, str(out_mp3)],
        check=True,
        capture_output=True,
    )

    # 3) nettoyage du temporaire
    try:
        Path(clip.chemin).unlink(missing_ok=True)
    except Exception:
        pass

    return ClipAudio(chemin=out_mp3, duree=clip.duree, backend=clip.backend)
