#!/bin/bash
# Débloque tuto-gen.app : retire la quarantaine macOS appliquée aux apps non
# signées téléchargées (sinon Piper / les voix peuvent faire planter l'app).
#
# Utilisation : placez ce fichier DANS LE MÊME DOSSIER que tuto-gen.app,
# puis double-cliquez dessus.

cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"

echo "────────────────────────────────────────────"
echo "   Déblocage de tuto-gen (quarantaine macOS)"
echo "────────────────────────────────────────────"
echo

# Localise l'app à côté de ce script.
APP=""
if [ -d "$DIR/tuto-gen.app" ]; then
  APP="$DIR/tuto-gen.app"
else
  APP="$(/usr/bin/find "$DIR" -maxdepth 1 -name '*.app' -print -quit 2>/dev/null)"
fi

if [ -z "$APP" ] || [ ! -d "$APP" ]; then
  echo "✗ tuto-gen.app est introuvable à côté de ce script."
  echo "  Mettez « débloquer.command » dans le même dossier que tuto-gen.app,"
  echo "  puis double-cliquez à nouveau."
  echo
  read -n 1 -s -r -p "Appuyez sur une touche pour fermer…"
  echo
  exit 1
fi

echo "Application : $APP"
echo "Retrait de la quarantaine et des attributs étendus…"
xattr -cr "$APP"

# Signature ad-hoc de secours (sans effet si déjà signé).
codesign --force --deep --sign - "$APP" >/dev/null 2>&1

if xattr -rl "$APP" 2>/dev/null | grep -qi "com.apple.quarantine"; then
  echo "⚠ Des attributs de quarantaine subsistent."
  echo "  Réessayez, ou vérifiez que l'app n'est pas lancée depuis un .zip / un disque monté."
  echo
  read -n 1 -s -r -p "Appuyez sur une touche pour fermer…"
  echo
  exit 1
fi

echo "✓ Quarantaine retirée. Lancement de l'application…"
open "$APP"
echo
echo "Terminé — vous pouvez fermer cette fenêtre."
