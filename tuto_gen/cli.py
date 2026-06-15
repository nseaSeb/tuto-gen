"""Point d'entrée CLI de tuto-gen.

Usage :
    python -m tuto_gen build examples/tuto.yaml [--output video.mp4] [--preview]
    python -m tuto_gen voices

Le pipeline `build` enchaîne 4 étapes avec une progression claire :
    [1/4] Parsing du YAML
    [2/4] Génération audio (TTS)
    [3/4] Composition des slides
    [4/4] Assemblage vidéo
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import __version__
from . import config, paquet, tts
from .assembler import NarrationRendue, SceneRendue, assembler

OK = "✓"


def _slug(texte: str) -> str:
    """Transforme un titre en nom de fichier sûr (sans accents ni espaces)."""
    accents = str.maketrans("àâäéèêëîïôöùûüç", "aaaeeeeiioouuuc")
    t = texte.lower().translate(accents)
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    return t or "tuto"


def _cmd_build(args: argparse.Namespace) -> int:
    t0 = time.time()

    # [1/4] Parsing -------------------------------------------------------
    print("[1/4] Parsing tuto.yaml...", end="", flush=True)
    try:
        cfg = config.charger(args.yaml)
    except config.ConfigError as e:
        print(f"  ✗\n  Erreur de configuration : {e}")
        return 1
    print(f"          {OK}")

    sortie = Path(args.output) if args.output else None
    sortie = construire(cfg, sortie, verbose=args.verbose, t0=t0)

    if args.preview and sortie:
        _ouvrir(sortie)
    return 0


def construire(cfg: config.Config, sortie: Path | None = None,
               verbose: bool = False, t0: float | None = None) -> Path:
    """Exécute les étapes 2→4 (audio, composition, assemblage) sur un projet.

    Réutilisable par le CLI (après parsing du YAML) comme par l'éditeur
    graphique (projet en mémoire). Affiche la progression et renvoie le
    chemin du MP4 produit.
    """
    if t0 is None:
        t0 = time.time()

    # [2/4] Audio ---------------------------------------------------------
    print(f"[2/4] Génération audio ({tts.backend_actif()})...", flush=True)
    tmp = Path(tempfile.mkdtemp(prefix="tutogen_"))
    rendus: list[SceneRendue] = []
    total_audio = 0.0
    n_clips = 0
    for scene in cfg.scenes:
        narrs: list[NarrationRendue] = []
        for j, n in enumerate(scene.narrations):
            if not n.texte.strip():
                continue
            clip = tts.synthetiser(n.texte, cfg.meta.voix,
                                   tmp / f"{scene.id}_{j}.wav",
                                   **config.params_voix(cfg.meta, n))
            if clip:
                narrs.append(NarrationRendue(clip=clip, debut=n.debut))
                n_clips += 1
                total_audio += clip.duree
                print(f"      • {scene.id:<12} #{j} {clip.duree:4.1f}s @ {n.debut:.1f}s")
        rendus.append(SceneRendue(scene=scene, narrations=narrs))
    print(f"      {OK}  {n_clips} clip(s) audio générés ({total_audio:.1f}s total)")

    # [3/4] Composition (rendu par sous-segments dans l'assembleur) --------
    print("[3/4] Composition des slides...", end="", flush=True)
    w, h = cfg.meta.resolution
    print(f"     {OK}  {len(rendus)} scènes {w}x{h}")

    # [4/4] Assemblage ----------------------------------------------------
    if sortie is None:
        sortie = Path("output") / f"{_slug(cfg.meta.titre)}.mp4"

    print("[4/4] Assemblage vidéo...", flush=True)
    duree, (vw, vh) = assembler(rendus, cfg.meta, sortie, cfg.meta.fps,
                                verbose=verbose)
    print(f"      {OK}")

    print(f"→ {sortie} ({duree:.1f}s, {vw}x{vh}, {cfg.meta.fps}fps) "
          f"— terminé en {time.time() - t0:.1f}s")
    return sortie


def _cmd_pack(args: argparse.Namespace) -> int:
    """Crée un paquet `.tuto` autonome depuis un tuto.yaml."""
    try:
        cfg = config.charger(args.yaml)
    except config.ConfigError as e:
        print(f"  ✗  Erreur de configuration : {e}")
        return 1
    dest = Path(args.output) if args.output else \
        Path(args.yaml).with_name(f"{_slug(cfg.meta.titre)}.tuto")
    paquet.exporter(cfg, dest)
    return 0


def _cmd_unpack(args: argparse.Namespace) -> int:
    """Extrait un paquet `.tuto` (et le construit avec --build)."""
    dest = Path(args.into) if args.into else \
        Path(args.paquet).with_suffix("")
    cfg, _yaml = paquet.importer(args.paquet, dest)
    if args.build:
        construire(cfg)
    return 0


def _cmd_gui(_args: argparse.Namespace) -> int:
    from .gui import main as gui_main
    return gui_main()


def _cmd_selftest(_args: argparse.Namespace) -> int:
    """Vérifie que le moteur de voix XTTS se charge (libs natives incluses).

    Conçu pour être lancé dans un sous-processus : si une bibliothèque native
    est bloquée par la quarantaine macOS et fait planter le processus, seul ce
    sous-processus meurt — l'app appelante reste vivante.
    """
    try:
        tts._preparer_compat_xtts()
        import TTS  # noqa: F401  (charge la stack XTTS / torch)
        print("ok")
        return 0
    except Exception as e:
        import traceback
        print(f"fail: {e}")
        traceback.print_exc()
        return 2


def _cmd_voices(_args: argparse.Namespace) -> int:
    print(f"Backend TTS actif : {tts.backend_actif()}\n")
    if not tts._xtts_disponible():
        print("Moteur XTTS indisponible (installez coqui-tts).")
        return 1
    modele = tts._xtts_dossier_bundle()
    ref = tts._voix_reference_bundle()
    print("Moteur : XTTS-v2 (Coqui)")
    print(f"  • modèle embarqué : {modele or '(téléchargé à la demande)'}")
    print(f"  • voix de référence livrée : {ref or '(voix intégrée par défaut)'}")
    print("  → voix personnalisée : fournir un WAV de référence (clonage).")
    return 0


def _ouvrir(chemin: Path) -> None:
    """Ouvre le fichier vidéo dans le lecteur par défaut (macOS/Linux)."""
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(chemin)], check=False)
        else:
            subprocess.run(["xdg-open", str(chemin)], check=False)
    except Exception:
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tuto_gen",
        description="Génère des vidéos de tutoriels narées, 100 % en local.",
    )
    parser.add_argument("--version", action="version",
                        version=f"tuto-gen {__version__}")
    sub = parser.add_subparsers(dest="commande", required=True)

    p_build = sub.add_parser("build", help="Génère la vidéo depuis un tuto.yaml")
    p_build.add_argument("yaml", help="Chemin du fichier tuto.yaml")
    p_build.add_argument("--output", "-o", help="Chemin du MP4 de sortie")
    p_build.add_argument("--preview", action="store_true",
                         help="Ouvre la vidéo après génération")
    p_build.add_argument("--verbose", "-v", action="store_true",
                         help="Affiche la barre de progression d'encodage")
    p_build.set_defaults(func=_cmd_build)

    p_pack = sub.add_parser(
        "pack", help="Crée un paquet .tuto autonome (YAML + médias + audio)")
    p_pack.add_argument("yaml", help="Chemin du fichier tuto.yaml")
    p_pack.add_argument("--output", "-o", help="Chemin du paquet .tuto de sortie")
    p_pack.set_defaults(func=_cmd_pack)

    p_unpack = sub.add_parser(
        "unpack", help="Extrait un paquet .tuto et ré-amorce le cache audio")
    p_unpack.add_argument("paquet", help="Chemin du paquet .tuto")
    p_unpack.add_argument("--into", help="Dossier d'extraction (défaut: nom du paquet)")
    p_unpack.add_argument("--build", action="store_true",
                          help="Génère la vidéo après extraction")
    p_unpack.set_defaults(func=_cmd_unpack)

    p_voices = sub.add_parser("voices", help="Liste les voix disponibles")
    p_voices.set_defaults(func=_cmd_voices)

    p_gui = sub.add_parser("gui", help="Ouvre l'interface graphique")
    p_gui.set_defaults(func=_cmd_gui)

    p_self = sub.add_parser("selftest", help="Vérifie le moteur de voix Piper")
    p_self.set_defaults(func=_cmd_selftest)

    return parser


def main(argv: list[str] | None = None) -> int:
    import sys as _sys
    argv = _sys.argv[1:] if argv is None else argv
    # Filtre l'argument -psn_... passé par le Finder au lancement d'une .app
    argv = [a for a in argv if not a.startswith("-psn")]
    # Sans argument (ex: double-clic sur l'app) → interface graphique
    if not argv:
        return _cmd_gui(argparse.Namespace())
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
