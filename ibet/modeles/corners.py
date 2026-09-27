"""Modele des corners."""

from __future__ import annotations

from .apports import AvecApports
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

# --- 2.0.0 : styles des joueurs alignes (voir `styles.py`) -------------------
#
# Facteur « onze aligne / onzes de reference » sur un indice de gestes qui
# font les corners (centres, tirs contres, touches dans la surface, dribbles,
# tirs), eleve a ce poids. N'agit que si la composition est publiee.
#
# Mesure (`mesure_styles.py`, 2 318 matchs de sept championnats rejoues,
# reglage sur les 60 % anciens, verdict sur 928 matchs recents) :
#   onze aligne, taux par 90, poids 0.25   log-vraisemblance +0.0074 (t = +2.7),
#                                          Brier du total 0.2244 -> 0.2236
#   onze habituel                          -0.0005 (t = -0.2) : n'agit pas
#   poids 1                                +0.0050 (t = +0.5) : trop fort
POIDS_STYLES = 0.25

# --- 3.0.0 : cotes du marche (voir `apports.py`) ------------------------------
#
# log lambda' = a + b.log lambda + c.(p_equipe - p_adversaire), probabilites 1X2
# du marche, marge retiree. Regression de Poisson sur l'historique long
# (football-data.co.uk, sept championnats, 22 988 matchs de 2012-13 a 2022-23).
#
# Mesure (`mesure_historique.py`, 7 406 matchs de 2023-24 a 2026-27, jamais
# vus au reglage), contre un estimateur calque sur le moteur :
#   log-vraisemblance +0.0688 par match (t = +16.0), Brier du total
#   0.2224 -> 0.2219, Brier par equipe 0.2008 -> 0.1930, pente du reel sur le
#   prevu 0.78 -> 1.08. La cote plus / moins 2,5 buts n'ajoute rien (+0.0009).
# b < 1 resserre les ecarts de dix matchs d'historique ; c > 0 donne au favori
# les corners que sa domination promet.
COEFS_MARCHE = {
    "domicile": (0.6189, 0.5875, 0.4368),
    "exterieur": (0.5398, 0.6695, 0.3950),
}


class ModeleCorners(AvecApports, ModeleParEquipe):
    cle = "corners"
    version = "3.0.0"
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
    poids_styles = POIDS_STYLES
    coefs_marche = COEFS_MARCHE
