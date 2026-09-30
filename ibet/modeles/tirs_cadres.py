"""Modele des tirs cadres."""

from __future__ import annotations

from .apports import AvecApports
from .base import ModeleParEquipe

SHOTS_LINE = 7.5

# Rapport variance / moyenne.
#
# 1.396 jusqu'a la 2.1.0 : mesure sur les residus du modele de forme SEUL. Les
# nombres attendus corriges par les cotes du marche (2.x) expliquent une part de
# la variance que ce chiffre attribuait au hasard : garde tel quel, il rendait
# toutes les probabilites trop prudentes, dans les deux sens (« plus »
# annonces 71.6 %, realises 75.0 % sur 7 406 matchs de test).
#
# 2.2.0 : 1.10, choisi sur les saisons jusqu'a 2022-23 (log-vraisemblance des
# comptes par equipe, grille 1.0 a 1.615), juge sur 2023-24 et apres :
# log-vraisemblance +0.027 par match (t = +11.1), « plus » 72.2 -> 72.2 %,
# « moins » 71.9 -> 72.2 %.
DISPERSION = 1.10

# Correlation des residus entre les deux equipes : +0.010 +/- 0.017 (t = +0.6),
# nulle, laissee a zero.
CORRELATION = 0.0

# Biais observe sur 100 matchs : -0.40, mais une calibration qui monte et
# descend sans direction nette. Pas de recalage.
CALIBRATION = 1.0

# Styles des joueurs alignes (`styles.py`) : facteur « onze aligne / onzes de
# reference » sur les tirs cadres par 90 minutes. Mesure (`mesure_styles.py`,
# 928 matchs de test) : +0.0037 a poids 0.25, t = +1.2 -- dans le bruit.
# Calcule et affiche dans la fiche, sans rien deplacer.
POIDS_STYLES = 0.0

# --- 2.0.0 : cotes du marche (voir `apports.py`) ------------------------------
#
# Meme correction que les corners, coefficients propres. Mesure
# (`mesure_historique.py`, 7 406 matchs de test) : log-vraisemblance +0.0827
# (t = +17.0), Brier du total 0.1965 -> 0.1959, par equipe 0.2000 -> 0.1899.
# Avec la cote plus / moins 2,5 buts, le gain monterait a +0.0999 ; elle n'est
# pas relevee avant les matchs (seul le 1X2 l'est), d'ou la variante 1X2 seul.
COEFS_MARCHE = {
    "domicile": (0.1812, 0.8189, 0.4983),
    "exterieur": (0.2536, 0.8456, 0.4721),
}

# --- 2.1.0 : avec la cote plus / moins 2,5 buts -------------------------------
#
# Jeu complet de la meme mesure : 4e coefficient sur p(+2,5) - 0,5.
# Log-vraisemblance +0.0999 contre +0.0827 pour le 1X2 seul. Employe quand la
# cote 2,5 est relevee avant le match (the-odds-api, consensus des operateurs) ;
# sinon, repli sur COEFS_MARCHE. Coefficients ajustes sur la cote MOYENNE
# d'OUVERTURE (football-data, Avg>2.5, marge retiree par normalisation) ; le
# consensus en service retire la marge operateur par operateur, puis moyenne,
# et il est releve a l'emission : ecart faible, mais pas nul.
COEFS_MARCHE_TOTAL = {
    "domicile": (0.4546, 0.6421, 0.4787, 0.6146),
    "exterieur": (0.4248, 0.7180, 0.4997, 0.4599),
}


class ModeleTirsCadres(AvecApports, ModeleParEquipe):
    cle = "tirs_cadres"
    version = "2.2.0"
    libelle = "Tirs cadres"
    champ = "tirs_cadres"
    seuil = SHOTS_LINE
    seuils_equipe = (2.5, 3.5, 4.5, 5.5)
    seuils_total = (5.5, 6.5, 7.5, 8.5, 9.5)
    # Lignes des bookmakers : total de 4.5 a 12.5, par equipe de 1.5 a 7.5.
    gamme_total = (4.5, 12.5)
    gamme_equipe = (1.5, 7.5)
    ecart_total = 2
    ecart_equipe = 2
    # Le depassement est mis en avant (choix de presentation, pas de calcul).
    prefere = "plus"
    dispersion = DISPERSION
    correlation = CORRELATION
    calibration = CALIBRATION
    poids_styles = POIDS_STYLES
    coefs_marche = COEFS_MARCHE
    coefs_marche_total = COEFS_MARCHE_TOTAL
