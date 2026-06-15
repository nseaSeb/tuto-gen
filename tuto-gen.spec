# -*- mode: python ; coding: utf-8 -*-
"""Spec PyInstaller pour le binaire macOS `tuto-gen`.

Embarque XTTS-v2 (Coqui, modèle dans assets/xtts) et ffmpeg, pour une synthèse
vocale 100 % hors-ligne sans installation. Approche « qualité ou rien » : pas
de moteur vocal de repli.

Build : pyinstaller --noconfirm tuto-gen.spec
Sortie : dist/tuto-gen  (binaire CLI autonome, macOS arm64)
"""

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    copy_metadata,
)

datas = []
binaries = []
hiddenimports = []

# --- Métadonnées de distribution (.dist-info) -----------------------------
# Plusieurs paquets lisent leur propre version via importlib.metadata au
# moment de l'import (ex: imageio, moviepy). Sans le .dist-info, l'app
# plante au démarrage avec PackageNotFoundError. On les copie explicitement.
for dist in [
    "imageio", "imageio_ffmpeg", "moviepy", "numpy", "pillow",
    "torch", "torchaudio", "torchcodec",
    "transformers", "tokenizers", "safetensors", "huggingface_hub",
    "coqui-tts", "coqui-tts-trainer", "librosa", "numba", "num2words",
    "tqdm", "regex", "filelock", "pyyaml", "soundfile",
    "flatbuffers", "protobuf", "pathvalidate",
]:
    try:
        datas += copy_metadata(dist)
    except Exception as e:
        print(f"[spec] copy_metadata({dist}) ignoré : {e}")

# --- Paquets à embarquer intégralement (code + data + dylibs) -------------
# collect_all récupère sous-modules, fichiers de données et bibliothèques
# dynamiques, ce qui couvre la plupart des imports dynamiques de la stack ML.
_paquets = [
    "TTS",                  # XTTS-v2 (Coqui) — backend TTS prioritaire
    "torch",
    "torchaudio",           # IO audio requis par coqui-tts (PyTorch ≥ 2.9)
    "torchcodec",           # décodage audio requis par coqui-tts (PyTorch ≥ 2.9)
    "transformers",         # couches GPT/XTTS
    "tokenizers",
    "safetensors",
    "huggingface_hub",
    "librosa",              # traitement audio coqui-tts
    "numba",                # accélération librosa
    "num2words",            # normalisation des nombres (coqui-tts)
    "ko_speech_tools",      # dépendance coqui-tts (sous-modules + data)
    "coqpit",               # config coqui-tts (paquet coqpit-config)
    "trainer",              # coqui-tts-trainer (importé par TTS)
    "imageio_ffmpeg",       # ffmpeg portable
    # Formats d'image étendus (logo / fond / captures) — voir tuto_gen.imaging.
    "pillow_heif",          # HEIC/HEIF (iPhone)
    "cairosvg",             # rastérisation SVG
    "cairocffi",            # binding cairo utilisé par cairosvg
]

for pkg in _paquets:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:  # un paquet absent ne doit pas bloquer le build
        print(f"[spec] collect_all({pkg}) ignoré : {e}")

# --- Assets livrés avec l'appli (samples, images, fonts, voix de référence) -
# NB : le modèle XTTS (assets/xtts, ~1,7 Go) n'est PLUS embarqué pour alléger
# le téléchargement. tts.py le récupère automatiquement au 1er lancement
# (fallback `_TTSApi(XTTS_MODELE)` quand `_xtts_dossier_bundle()` → None), puis
# il reste en cache local (100 % hors-ligne ensuite).
import os as _os
for _sub in ("samples", "images", "fonts", "voices"):
    _ad = _os.path.join(_os.getcwd(), "assets", _sub)
    if _os.path.isdir(_ad):
        datas += [(_ad, _os.path.join("assets", _sub))]

# --- Notre package ---------------------------------------------------------
hiddenimports += [
    "tuto_gen", "tuto_gen.cli", "tuto_gen.config", "tuto_gen.imaging",
    "tuto_gen.tts", "tuto_gen.voix_texte", "tuto_gen.composer", "tuto_gen.assembler",
    "tuto_gen.gui", "tuto_gen.gui.app", "tuto_gen.gui.common",
    "tuto_gen.gui.panels", "tuto_gen.gui.apercu", "tuto_gen.gui.timeline",
    "tuto_gen.gui.playback", "tuto_gen.gui.project",
    "tuto_gen.settings",
    "soundfile", "numpy", "yaml", "PIL",
    # Interface graphique (importée paresseusement → à déclarer explicitement)
    "tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox",
    "tkinter.colorchooser", "_tkinter",
    # Pont Pillow ↔ Tk pour l'aperçu live des slides
    "PIL.ImageTk", "PIL._tkinter_finder", "PIL._imagingtk",
]


a = Analysis(
    ["packaging/launcher.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Seule exclusion sûre : le backend Piper (non utilisé — XTTS-only) et son
    # moteur onnxruntime (~62 Mo). NB : on NE peut PAS élaguer matplotlib /
    # sklearn / babel : coqui-tts (TTS) les importe au chargement via des boucles
    # d'import dynamiques (ex. TTS.vocoder.configs importe tous les configs →
    # wavegrad → generic_utils → matplotlib ; librosa.decompose → sklearn).
    excludes=["pytest", "kokoro", "misaki", "spacy", "thinc", "blis",
              "piper_tts", "onnxruntime"],
    noarchive=False,
    # NB : pas d'optimize=2 / -OO — cela supprime les docstrings, dont
    # transformers (XTTS) a besoin au runtime (sinon l'import de TTS échoue).
    # Certaines libs lisent leur propre code source au runtime via
    # inspect.getsource() — impossible sans les .py. On embarque donc le source :
    #  • transformers : auto_docstring à l'import des modèles ;
    #  • inflect : décorateur @typechecked (typeguard) à l'import.
    module_collection_mode={
        "transformers": "pyz+py",
        "inflect": "pyz+py",
        "typeguard": "pyz+py",
    },
)

pyz = PYZ(a.pure)

# Mode onedir : la stack PyTorch pèse ~2 Go ; un binaire onefile devrait
# tout réextraire dans /tmp à chaque lancement (lent et fragile). On produit
# donc un dossier dist/tuto-gen/ contenant l'exécutable dist/tuto-gen/tuto-gen.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="tuto-gen",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,   # retire les symboles de debug (dylibs torch/scipy) → plus léger
    upx=False,    # UPX déconseillé sur macOS (signature/Gatekeeper)
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=True,
    upx=False,
    name="tuto-gen",
)

# Bundle macOS .app : double-clic dans le Finder → ouvre l'interface
# graphique (lancement sans argument). L'exécutable CLI reste accessible
# dans tuto-gen.app/Contents/MacOS/tuto-gen.
app = BUNDLE(
    coll,
    name="tuto-gen.app",
    icon="assets/icon.icns",
    bundle_identifier="com.interne.tutogen",
    info_plist={
        "CFBundleName": "tuto-gen",
        "CFBundleDisplayName": "tuto-gen",
        "CFBundleShortVersionString": "0.1.0",
        "NSHighResolutionCapable": True,
        # App graphique : pas d'icône dans le Dock pendant les imports lourds
        "LSBackgroundOnly": False,
    },
)
