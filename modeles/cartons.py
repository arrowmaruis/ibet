"""Modele des cartons jaunes."""

from __future__ import annotations

from .base import ModeleParEquipe

CARDS_LINE = 4.5

# Rapport variance / moyenne : 0.847. Seule grandeur SOUS-dispersee -- il s'en
# donne un nombre remarquablement regulier --, d'ou une loi binomiale : un
# nombre limite de situations ou l'arbitre peut en donner un.
DISPERSION = 0.847

# Correlation des residus entre les deux equipes : +0.083 +/- 0.017 (t = +4.9,
# n = 3 976). Les cartons vont ensemble : un match tendu en donne aux deux. Une
# correlation positive elargit la loi du total.
CORRELATION = 0.083

# Biais observe sur 100 matchs : +0.09. Pas de recalage.
CALIBRATION = 1.0


class ModeleCartonsJaunes(ModeleParEquipe):
    cle = "cartons_jaunes"
    libelle = "Cartons jaunes"
    champ = "cartons_jaunes"
    seuil = CARDS_LINE
    seuils_equipe = (0.5, 1.5, 2.5, 3.5)
    seuils_total = (2.5, 3.5, 4.5, 5.5)
    prefere = "les deux"
    dispersion = DISPERSION
    correlation = CORRELATION
    calibration = CALIBRATION
