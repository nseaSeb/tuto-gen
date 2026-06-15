"""Numéro de build de tuto-gen, affiché dans la popup ⚙ Réglages.

Incrémenté automatiquement de 0.01 à chaque build PyInstaller (voir tuto-gen.spec).
Pour figer une version précise : mettre la valeur voulue dans BUILD_VERSION et
passer FIGER_VERSION à True. Le prochain build utilisera la valeur telle quelle
(sans incrément) puis remettra FIGER_VERSION à False.
"""
BUILD_VERSION = "1.01"   # dernière version buildée (1er build -> 1.01)
FIGER_VERSION = False    # True = demande explicite : ne pas incrémenter
