# Prompt Claude Code — Projet `tuto-gen`

## Contexte

Je construis un pipeline Python local pour générer des vidéos de tutoriels pour mes applications SaaS (Verde, TIM). Le principe : je fournis des screenshots de mon app + un script texte, et le pipeline produit une vidéo MP4 narée par une voix IA naturelle, sans avatar, sans SaaS externe.

Référence visuelle : fond coloré avec logo, screenshot annoté avec flèches, texte narré synchronisé, produit 100% localement.

## Objectif du projet

Créer un CLI Python `tuto-gen` qui prend en entrée un fichier `tuto.yaml` et produit un `output.mp4`.

## Stack technique

- **Python 3.11+**
- **Kokoro TTS** (`kokoro` pip package) — voix française locale, offline, Apache 2.0
- **Pillow** — composition des slides (fond, logo, screenshot, flèches)
- **moviepy** — assemblage audio + image → clips → MP4 final
- **ffmpeg** — sous-jacent moviepy
- **PyYAML** — parsing du fichier de configuration
- **whisper** (optionnel, phase 2) — génération sous-titres .srt

## Structure du projet à créer

```
tuto-gen/
├── README.md
├── requirements.txt
├── tuto_gen/
│   ├── __init__.py
│   ├── cli.py          # entry point : `python -m tuto_gen build tuto.yaml`
│   ├── config.py       # parsing et validation du YAML
│   ├── tts.py          # génération audio via Kokoro
│   ├── composer.py     # composition visuelle des slides (Pillow)
│   └── assembler.py    # assemblage vidéo (moviepy)
├── assets/
│   └── fonts/          # police pour les titres (ex: Inter ou Nunito)
└── examples/
    ├── tuto.yaml       # exemple complet
    └── screenshots/    # screenshots placeholder
```

## Format du fichier `tuto.yaml`

```yaml
# Métadonnées de la vidéo
meta:
  titre: "Activer la double authentification"
  app: "Verde"
  logo: "assets/logo_verde.png"
  couleur_fond: "#2D6A4F"      # vert Verde
  couleur_accent: "#ffffff"
  resolution: [1920, 1080]
  fps: 30
  voix: "ff_siwis"             # voix Kokoro française naturelle

# Scènes
scenes:
  - id: intro
    type: title                 # slide de titre plein écran
    titre: "Activer la double authentification"
    sous_titre: "Sécurisez votre compte en 3 étapes"
    duree_min: 3               # durée minimale en secondes (peut s'allonger avec l'audio)

  - id: etape_1
    type: screenshot            # slide avec screenshot annoté
    titre: "Accédez aux paramètres de sécurité"
    screenshot: "screenshots/settings_security.png"
    narration: |
      Dans votre tableau de bord, cliquez sur votre avatar en haut à droite,
      puis sélectionnez Paramètres, et enfin l'onglet Sécurité.
    annotations:               # flèches et highlights optionnels
      - type: arrow
        de: [120, 80]          # coordonnées relatives au screenshot [x%, y%]
        vers: [340, 210]
        couleur: "#FF6B35"
      - type: highlight
        zone: [300, 190, 420, 240]   # [x1%, y1%, x2%, y2%]
        couleur: "#FFD166"
        opacite: 0.4

  - id: etape_2
    type: screenshot
    titre: "Scannez le QR Code"
    screenshot: "screenshots/2fa_qrcode.png"
    narration: |
      Un QR Code s'affiche. Ouvrez votre application d'authentification,
      Aegis ou Google Authenticator, puis scannez ce code.
      Un code à 6 chiffres apparaît dans votre app.

  - id: outro
    type: title
    titre: "Double authentification activée ✓"
    sous_titre: "Votre compte est maintenant sécurisé"
    duree_min: 4
```

## Comportement attendu du CLI

```bash
# Commande principale
python -m tuto_gen build examples/tuto.yaml

# Sortie attendue
[1/4] Parsing tuto.yaml...          ✓
[2/4] Génération audio (Kokoro)...  ✓  3 clips générés (14.2s total)
[3/4] Composition des slides...     ✓  4 images 1920x1080
[4/4] Assemblage vidéo...           ✓
→ output/activer_double_auth.mp4 (18.2s, 1920x1080, 30fps)

# Options utiles
python -m tuto_gen build tuto.yaml --output ma_video.mp4
python -m tuto_gen build tuto.yaml --preview    # ouvre la vidéo après génération
python -m tuto_gen voices                        # liste les voix Kokoro disponibles
```

## Composition visuelle des slides (type: screenshot)

Layout à reproduire :

```
┌─────────────────────────────────────────────────┐
│  [LOGO]                          couleur_fond    │  ← header 15% hauteur
│  Titre de l'étape                                │
├─────────────────────────────────────────────────┤
│                                                  │
│         [SCREENSHOT centré avec padding]         │  ← zone principale 75%
│         [+ annotations flèches/highlights]       │
│                                                  │
├─────────────────────────────────────────────────┤
│  ▶ Texte de narration en cours (optionnel)      │  ← footer 10% hauteur
└─────────────────────────────────────────────────┘
```

## Composition visuelle des slides (type: title)

```
┌─────────────────────────────────────────────────┐
│                                                  │
│              [LOGO centré grand]                 │
│                                                  │
│         Titre principal (bold, large)            │
│         Sous-titre (regular, smaller)            │
│                                                  │
│                                    couleur_fond  │
└─────────────────────────────────────────────────┘
```

## Notes d'implémentation

**TTS (tts.py) :**
- Utiliser `kokoro` : `from kokoro import KPipeline`
- Voix française recommandée : `ff_siwis` (naturelle, claire)
- Générer un fichier `.wav` par scène avec narration
- Retourner la durée exacte pour synchroniser la slide

**Composer (composer.py) :**
- Fond plein avec `couleur_fond` du meta
- Logo PNG avec transparence en haut à gauche
- Screenshot redimensionné pour tenir dans la zone centrale avec padding 5%
- Flèches dessinées avec Pillow `ImageDraw` : trait épais, style "hand-drawn" si possible (légère courbure)
- Highlights : rectangle semi-transparent avec `Image.alpha_composite`

**Assembler (assembler.py) :**
- Chaque scène = `ImageClip(image).set_duration(audio_duration)` + `AudioFileClip(wav)`
- `concatenate_videoclips(clips)` pour le montage final
- Export : `write_videofile(output, fps=30, codec='libx264', audio_codec='aac')`

**Gestion des durées :**
- Pour `type: screenshot` : durée = durée de l'audio généré + 0.5s de silence fin
- Pour `type: title` : durée = max(duree_min, durée audio si narration présente)

## Ce que je veux en premier

1. Le projet complet avec tous les fichiers listés ci-dessus
2. Un `tuto.yaml` d'exemple fonctionnel avec 3 scènes (intro + 1 screenshot + outro)
3. Des screenshots placeholder générés par le code lui-même (rectangles colorés avec texte) pour que le pipeline tourne sans vrais assets
4. Le pipeline doit produire un MP4 réel de bout en bout

## Contraintes

- Tout doit tourner **offline** après install initiale de Kokoro
- Pas de dépendance à un service cloud
- macOS compatible (Ghostty terminal, Python 3.11+)
- Code commenté en français
- Logs clairs avec progression étape par étape
