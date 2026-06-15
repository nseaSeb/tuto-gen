"""Point d'entrée du binaire PyInstaller `tuto-gen`.

Avant tout import lourd (moviepy, kokoro), on configure l'environnement
pour que les ressources embarquées dans le bundle soient trouvées :
- ffmpeg portable (fourni par imageio-ffmpeg) ;
- la bibliothèque et les données espeak-ng (utilisées par la G2P française
  de Kokoro/misaki).
"""

import os
import stat
import sys
from pathlib import Path


def _base() -> Path:
    """Dossier racine des ressources (bundle PyInstaller ou exécution normale)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def _configurer_ffmpeg(base: Path) -> None:
    """Pointe moviepy/imageio vers le ffmpeg embarqué et le rend exécutable."""
    binaires = base / "imageio_ffmpeg" / "binaries"
    if not binaires.is_dir():
        return
    candidats = sorted(binaires.glob("ffmpeg*"))
    if not candidats:
        return
    ffmpeg = candidats[0]
    # PyInstaller peut retirer le bit exécutable des fichiers data
    try:
        ffmpeg.chmod(ffmpeg.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass
    os.environ.setdefault("IMAGEIO_FFMPEG_EXE", str(ffmpeg))
    os.environ.setdefault("FFMPEG_BINARY", str(ffmpeg))


def _configurer_espeak(base: Path) -> None:
    """Indique à espeakng_loader où trouver la dylib et les données embarquées."""
    lib = base / "espeakng_loader" / "libespeak-ng.dylib"
    data = base / "espeakng_loader" / "espeak-ng-data"
    if lib.is_file():
        os.environ.setdefault("ESPEAK_LIBRARY", str(lib))
        os.environ.setdefault("PHONEMIZER_ESPEAK_LIBRARY", str(lib))
    if data.is_dir():
        os.environ.setdefault("ESPEAK_DATA_PATH", str(data))


def main() -> int:
    base = _base()
    _configurer_ffmpeg(base)
    _configurer_espeak(base)

    from tuto_gen.cli import main as cli_main

    return cli_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
