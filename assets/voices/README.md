# Voix de référence (clonage XTTS)

Déposez ici **un fichier WAV** pour définir la voix par défaut du projet : XTTS
clonera son timbre pour toute la narration. Le premier `*.wav` (ordre
alphabétique) est utilisé automatiquement (cf. `tts._voix_reference_bundle`).

On peut aussi choisir un WAV par projet depuis l'interface
(Réglages → « Voix (XTTS) » → *Choisir un WAV…*), ce qui prime sur ce dossier.

## Format recommandé

- **WAV**, mono de préférence ;
- **6 à 15 secondes** de parole continue ;
- voix seule, **sans musique, bruit ni réverbération** ;
- 2-3 phrases neutres, bien articulées, dans la langue cible (français).

Le taux d'échantillonnage importe peu (XTTS rééchantillonne).

## Où trouver des voix

- **S'enregistrer soi-même** (meilleur résultat, droits garantis).
- **Common Voice** (CC0, clips FR), **LibriVox** (domaine public).

⚠️ Ne clonez que des voix que vous possédez, libres de droits ou avec
consentement explicite.

## Sans WAV ici

Si ce dossier est vide et qu'aucun WAV n'est choisi, XTTS utilise un **speaker
studio intégré** (réglable dans Réglages → « Voix intégrée » ; défaut :
« Claribel Dervla »). Ces voix n'ont pas l'accent français natif : un WAV de
référence FR donne un bien meilleur rendu.
