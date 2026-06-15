"""Export/import d'un tuto sous forme de **paquet autonome** `.tuto`.

Un `.tuto` est une simple archive ZIP qui embarque *tout* ce qu'il faut pour
rejouer un tutoriel sur une autre machine :

    tuto.yaml              # config, chemins réécrits relatifs à la racine
    media/                 # logo, captures, fond, samples, voix de réf, police
    audio/manifest.json    # { sig: {texte, speed, temperature, fluidite} }
    audio/<sig>.wav        # audio pré-généré de chaque narration

Contrairement au simple `tuto.yaml` (qui ne contient que des *références* vers
des fichiers externes), le paquet est déplaçable et partageable tel quel.

Le cache TTS (`~/.tuto-gen/tts_cache/`) est indexé par une clé qui dépend du
`mtime`/taille du fichier de voix de référence (`tts._cache_key`) : il n'est
donc pas portable. On contourne cela en indexant l'audio embarqué par une
**signature indépendante de la voix** (texte + paramètres) ; à l'import, on
recalcule la vraie clé de cache locale (après extraction de la voix) et on
ré-amorce le cache — le build suivant retrouve l'audio sans resynthèse, et même
sans XTTS installé.
"""

from __future__ import annotations

import copy
import filecmp
import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from . import config, tts


def _emit(log, msg: str) -> None:
    """Émet un message vers le callback `log` (sinon `print`)."""
    if log is not None:
        log(msg)
    else:
        print(msg)


def _abspath(base_dir: Path, p) -> Path | None:
    """Chemin absolu d'un asset (résolu relativement au YAML si besoin)."""
    if p is None:
        return None
    p = Path(p)
    return p if p.is_absolute() else (base_dir / p)


def _sig(texte: str, speed: float, temperature: float, fluidite: bool) -> str:
    """Signature d'une narration, **indépendante** de la voix de référence.

    Sert de nom de fichier audio dans le paquet et de clé de manifeste. La vraie
    clé de cache TTS (dépendante de la voix) est recalculée à l'import."""
    norm = " ".join((texte or "").split())
    raw = "|".join([norm, f"{speed:.3f}", f"{temperature:.3f}", str(bool(fluidite))])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Collecte des médias dans un dossier (modèle « projet autonome »)
# --------------------------------------------------------------------------

def _memes_fichiers(a: Path, b: Path) -> bool:
    try:
        return filecmp.cmp(a, b, shallow=False)
    except Exception:
        return False


def adopter_fichier(media_dir, src) -> Path:
    """Copie `src` dans `media_dir` (dédupliqué) et renvoie le chemin résultat.

    Si `src` est déjà dans `media_dir`, ou si un fichier identique y existe déjà,
    aucune copie n'est faite. En cas de collision de nom avec un fichier
    *différent*, le nom est suffixé (`-1`, `-2`, …)."""
    media_dir = Path(media_dir)
    src = Path(src)
    media_dir.mkdir(parents=True, exist_ok=True)
    try:
        src.resolve().relative_to(media_dir.resolve())
        return src  # déjà dans le dossier média
    except ValueError:
        pass
    cible = media_dir / src.name
    i = 1
    while cible.exists() and not _memes_fichiers(cible, src):
        cible = media_dir / f"{src.stem}-{i}{src.suffix}"
        i += 1
    if not cible.exists():
        shutil.copyfile(src, cible)
    return cible


def collecter(cfg: config.Config, media_dir, log=None) \
        -> tuple[config.Config, int, int]:
    """Rassemble tous les assets d'un `Config` dans `media_dir`.

    Renvoie `(Config réécrit, nb_assets, nb_manquants)`. Les chemins du Config
    renvoyé pointent (en absolu) dans `media_dir` ; les assets introuvables
    conservent leur chemin d'origine (avertissement émis)."""
    media_dir = Path(media_dir)
    cfg_out = copy.deepcopy(cfg)
    vus: dict[str, Path] = {}
    manquants = 0

    def adopt(p):
        nonlocal manquants
        absp = _abspath(cfg.base_dir, p)
        if absp is None:
            return None
        key = str(absp)
        if key in vus:
            return vus[key]
        if not absp.is_file():
            _emit(log, f"⚠️  Asset introuvable, non copié : {absp}")
            manquants += 1
            return absp
        dest = adopter_fichier(media_dir, absp)
        vus[key] = dest
        return dest

    cfg_out.meta.logo = adopt(cfg.meta.logo)
    cfg_out.meta.fond_image = adopt(cfg.meta.fond_image)
    cfg_out.meta.voix_reference = adopt(cfg.meta.voix_reference)
    cfg_out.meta.police = adopt(cfg.meta.police)
    for s in cfg_out.scenes:
        for c in s.captures:
            c.chemin = adopt(c.chemin)
        for sa in s.samples:
            sa.chemin = adopt(sa.chemin)
    return cfg_out, len(vus), manquants


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

def exporter(cfg: config.Config, dest, log=None) -> Path:
    """Crée un paquet `.tuto` autonome depuis un `Config`.

    Copie tous les médias référencés, pré-génère l'audio des narrations (via le
    cache TTS, en synthétisant ce qui manque si XTTS est disponible) et zippe le
    tout. Renvoie le chemin du `.tuto` écrit."""
    dest = Path(dest)
    if dest.suffix != ".tuto":
        dest = dest.with_suffix(".tuto")

    tmp = Path(tempfile.mkdtemp(prefix="tutopack_"))
    try:
        # 1) Médias : copie dédupliquée dans media/ + chemins réécrits ---------
        cfg_out, n_medias, manquants = collecter(cfg, tmp / "media", log=log)

        # 2) YAML (base_dir = racine du paquet → chemins relatifs media/...) ---
        config.sauver(cfg_out, tmp / "tuto.yaml")

        # 3) Audio des narrations pré-généré ----------------------------------
        audio = tmp / "audio"
        audio.mkdir()
        speed = cfg.meta.voix_vitesse
        temp = cfg.meta.voix_expressivite
        fluid = cfg.meta.voix_fluidite
        ref = _abspath(cfg.base_dir, cfg.meta.voix_reference) \
            or tts._voix_reference_bundle()
        xtts_ok = tts._xtts_disponible()
        manifest: dict[str, dict] = {}
        embarques = 0
        ignores = 0
        for s in cfg.scenes:
            for n in s.narrations:
                texte = (n.texte or "").strip()
                if not texte:
                    continue
                sig = _sig(texte, speed, temp, fluid)
                if sig in manifest:
                    continue
                deja = tts.en_cache(texte, ref_voix=cfg.meta.voix_reference,
                                    speed=speed, temperature=temp, fluidite=fluid)
                if not deja and not xtts_ok:
                    ignores += 1
                    continue
                try:
                    cached, _ = tts._synth_cached(texte, ref, speed, temp, fluid)
                except Exception as e:
                    _emit(log, f"⚠️  Audio non généré ({texte[:30]}…) : {e}")
                    ignores += 1
                    continue
                shutil.copyfile(cached, audio / f"{sig}.wav")
                manifest[sig] = {"texte": texte, "speed": speed,
                                 "temperature": temp, "fluidite": fluid}
                embarques += 1
        (audio / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        # 4) Zip --------------------------------------------------------------
        archive = shutil.make_archive(str(dest.with_suffix("")), "zip",
                                      root_dir=tmp)
        dest.unlink(missing_ok=True)
        Path(archive).replace(dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    detail = f"{n_medias} média(s), {embarques} narration(s) audio"
    if ignores:
        detail += f", {ignores} audio non embarqué(s)"
    if manquants:
        detail += f", {manquants} asset(s) manquant(s)"
    _emit(log, f"📦 Paquet créé : {dest} ({detail})")
    return dest


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------

def importer(paquet, dest_dir, log=None) -> tuple[config.Config, Path]:
    """Extrait un paquet `.tuto` dans `dest_dir` et ré-amorce le cache TTS.

    Renvoie `(Config, chemin_du_yaml_extrait)`. Après cet appel, le build/lecture
    du tuto retrouve l'audio des narrations dans le cache local, sans resynthèse.
    """
    paquet = Path(paquet)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(paquet) as z:
        z.extractall(dest_dir)

    yaml_path = dest_dir / "tuto.yaml"
    cfg = config.charger(yaml_path)

    # Ré-amorçage du cache TTS : on recalcule la clé locale (qui dépend du
    # fichier de voix extrait) et on y range l'audio embarqué.
    manifest_path = dest_dir / "audio" / "manifest.json"
    reseedes = 0
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        ref = _abspath(cfg.base_dir, cfg.meta.voix_reference) \
            or tts._voix_reference_bundle()
        for sig, info in manifest.items():
            wav = dest_dir / "audio" / f"{sig}.wav"
            if not wav.is_file():
                continue
            key = tts._cache_key(info["texte"], ref, info["speed"],
                                 info["temperature"], info["fluidite"])
            cible = tts._cache_dir() / f"{key}.wav"
            if not cible.is_file():
                shutil.copyfile(wav, cible)
                reseedes += 1

    _emit(log, f"📥 Paquet importé dans {dest_dir} "
               f"({len(cfg.scenes)} scène(s), {reseedes} audio mis en cache)")
    return cfg, yaml_path
