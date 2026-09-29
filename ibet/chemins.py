"""Emplacement de toutes les donnees du projet.

Le code vit dans `ibet/`, les donnees dans `donnees/`, et ce module est le seul
a savoir ou. Un module qui a besoin d'un fichier l'importe d'ici plutot que de
reconstruire un chemin a partir de son propre emplacement : deplacer un module
ne doit jamais deplacer ses donnees.

    donnees/
      ibet.db                     previsions, resultats, cotes, feuilles, joueurs,
                                  arbitres (`python -m ibet arbitres`)
      entraineurs.db              base des entraineurs (`python -m ibet entraineurs`)
      forces.json                 notes attaque / defense (`python -m ibet forces`)
      predictions_ouvertes.json   fichier historique, avant la base SQLite
      cache/                      reponses des sources, pour economiser le quota
      exports/                    CSV / JSON exportes
      sauvegardes/                copies datees de la base
      certificats/bundle.pem      certificats (`python -m ibet certificats`)

Tout le dossier est hors de git : ce sont des donnees locales, pas du code.
"""

from __future__ import annotations

from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DONNEES = RACINE / "donnees"

BASE = DONNEES / "ibet.db"
BASE_ENTRAINEURS = DONNEES / "entraineurs.db"
FORCES = DONNEES / "forces.json"
HISTORIQUE_JSON = DONNEES / "predictions_ouvertes.json"
CACHE = DONNEES / "cache"
EXPORTS = DONNEES / "exports"
SAUVEGARDES = DONNEES / "sauvegardes"
CERTIFICATS = DONNEES / "certificats" / "bundle.pem"
