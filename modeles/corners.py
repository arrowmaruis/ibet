"""Modele des corners."""

from __future__ import annotations

from .base import ModeleParEquipe

CORNERS_LINE = 9.5

# Rapport variance / moyenne : 1.615, la grandeur la plus sur-dispersee --
# Poisson y est trop etroite et sous-estime les deux queues, d'ou la binomiale
# negative.
DISPERSION = 1.615

# Correlation des residus entre les deux equipes : -0.149 +/- 0.017 (t = -9.0,
# n = 4 097). Les corners sont partiellement a somme nulle : une equipe qui
# domine en prend au detriment de l'autre. Une correlation negative resserre la
# loi du TOTAL sous ce que l'independance predit ; les lois par equipe sont
# inchangees -- c'est pourquoi la famille « equipe » etait deja bien calibree
# (-1.2 point) et la famille « total » non (-8.8 points).
CORRELATION = -0.149

# Les corners ont longtemps porte 1.07, mesure sur cent matchs ou le modele
# annoncait 9.48 pour 10.14 reels. Ce recalage ne tient plus, et il faisait
# du mal : sur les 26 fiches tranchees, il portait la prevision a 10.23 pour
# 9.54 observes -- un exces de 0.69 but, la ou l'estimation brute tombait a
# +0.02. L'erreur absolue passait de 3.07 a 3.27.
#
# Une seconde mesure, independante, dit la meme chose : en walk-forward sur
# 1 322 matchs, le modele sous-annonce les corners de 0.24 sur une moyenne
# de 9.6, soit 2.5 % -- pas 7 %.
#
# Un facteur mesure une fois ne se garde pas indefiniment : il vieillit avec
# le modele qu'il corrige. Remis a 1, et a remesurer quand une centaine de
# fiches seront tranchees.
CALIBRATION = 1.0


class ModeleCorners(ModeleParEquipe):
    cle = "corners"
    libelle = "Corners"
    champ = "corners"
    seuil = CORNERS_LINE
    seuils_equipe = (2.5, 3.5, 4.5, 5.5, 6.5)
    seuils_total = (7.5, 8.5, 9.5, 10.5, 11.5)
    # Le depassement est mis en avant (choix de presentation, pas de calcul).
    prefere = "plus"
    dispersion = DISPERSION
    correlation = CORRELATION
    calibration = CALIBRATION
