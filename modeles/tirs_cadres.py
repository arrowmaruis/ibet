"""Modele des tirs cadres."""

from __future__ import annotations

from .base import ModeleParEquipe

SHOTS_LINE = 7.5

# Rapport variance / moyenne : 1.396.
DISPERSION = 1.396

# Correlation des residus entre les deux equipes : +0.010 +/- 0.017 (t = +0.6),
# nulle, laissee a zero.
CORRELATION = 0.0

# Biais observe sur 100 matchs : -0.40, mais une calibration qui monte et
# descend sans direction nette. Pas de recalage.
CALIBRATION = 1.0


class ModeleTirsCadres(ModeleParEquipe):
    cle = "tirs_cadres"
    libelle = "Tirs cadres"
    champ = "tirs_cadres"
    seuil = SHOTS_LINE
    seuils_equipe = (2.5, 3.5, 4.5, 5.5)
    seuils_total = (5.5, 6.5, 7.5, 8.5, 9.5)
    # Le depassement est mis en avant (choix de presentation, pas de calcul).
    prefere = "plus"
    dispersion = DISPERSION
    correlation = CORRELATION
    calibration = CALIBRATION
