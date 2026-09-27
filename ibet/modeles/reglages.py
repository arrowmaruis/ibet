"""Reglages partages par tous les modeles d'evenement.

Ces valeurs ne sont propres a aucun evenement : elles reglent le moteur commun
(regularisation, anciennete, dependance des petits scores...). Ce qui est
propre a un evenement -- sa dispersion, sa correlation, son recalage, ses
seuils -- vit dans le fichier de son modele.
"""

from __future__ import annotations

from typing import NamedTuple

# Regularisation des forces vers la moyenne du championnat ("shrinkage").
#
#     force_ajustee = (n * force_observee + SHRINKAGE * 1) / (n + SHRINKAGE)
#
# Maher estime attaque et defense sur une saison complete. Sur quatre ou cinq
# matchs, un ratio de 1.5 tient autant du bruit que du signal, et le modele en
# multiplie deux : les ecarts se composent et donnent des probabilites que rien
# ne justifie (84 % de victoire exterieure sur cinq matchs, la ou un marche en
# donnerait 55 a 60).
#
# Ramener les estimations vers 1 proportionnellement a la taille de
# l'echantillon est la reponse classique a ce probleme (estimateur a retrecis-
# sement, ou empirical Bayes : Efron et Morris, 1975, "Data Analysis Using
# Stein's Estimator and its Generalizations", JASA 70). SHRINKAGE s'interprete
# comme un nombre de matchs fictifs joues au niveau moyen : a n = SHRINKAGE,
# observation et moyenne pesent autant.
#
# La valeur etait 10, heritee d'un classement sur 149 matchs ou k = 14 et k = 20
# faisaient deja mieux sans sortir du bruit (t = -0.6 et -0.3). Mesuree sur le
# corpus entier, en walk-forward et sur le SEUIL -- ce qu'une fiche vend
# vraiment, et non l'issue --, la reponse est nette :
#
#   grandeur         n      pente a k=10   Brier k=10 -> k=22          t
#   buts          6 316         0.94         0.2472 -> 0.2447        -4.9
#   corners       1 750         0.43         0.2530 -> 0.2505        -2.9
#   tirs cadres   1 748         0.62         0.2476 -> 0.2460        -1.6
#   cartons       1 651         0.66         0.2470 -> 0.2433        -3.8
#
# La pente est celle de la regression du reel sur le prevu. A 0.43, le modele
# SUR-etale : quand il annonce un corner de plus, la realite n'en fait que 0.43.
# Des lambdas trop ecartes de la moyenne poussent les probabilites de seuil trop
# loin vers la certitude -- exactement la forme du defaut que les fiches
# montraient ("annonce 80 %, observe 57 %"), et dont la cause etait cherchee
# depuis longtemps du cote de la calibration. Elle etait dans l'etalement.
#
# Le reglage a ete verifie sur TOUTES les lignes proposables, pas seulement la
# plus courante : 16 lignes sur 16 s'ameliorent, en Brier ET en log-loss. Un
# reglage qui ne gagnerait qu'au voisinage de la moyenne aurait degrade les
# extremes ; aucun ne l'est. Le log-loss suit, donc le gain n'est pas achete en
# s'autorisant des annonces plus sures.
#
# 22 plutot qu'un optimum par grandeur (26, 18, 22, 22) : les ecarts entre 18 et
# 26 sont dans le bruit pour chacune, et quatre constantes reglees chacune sur
# son echantillon s'ajusteraient au passe bien plus qu'une seule que quatre
# mesures independantes designent.
SHRINKAGE = 22.0

# Demi-vie de la ponderation par anciennete, en jours : un match deux fois plus
# vieux que la demi-vie compte pour un quart d'un match d'aujourd'hui. Zero
# desactive la ponderation, et c'est la valeur retenue : sur 149 matchs, les
# cinq demi-vies essayees (14 a 60 jours) font toutes moins bien que son
# absence, et l'ecart croit quand la demi-vie raccourcit (README). Le mecanisme
# reste en place pour que `backtest.tune` puisse le reprendre sur un echantillon
# plus large.
HALF_LIFE = 0.0

# Facteur de dependance des petits scores (Dixon et Coles). Zero = scores
# independants, soit le modele de Maher pur.
RHO = 0.0

# Part de l'avantage du terrain propre a l'equipe, entre 0 et 1.
#
# Le modele donne aujourd'hui le meme avantage a toutes les equipes : celui de
# la competition (`moyenne_domicile` / `moyenne_exterieur`). Or il varie d'un
# club a l'autre -- certains stades pesent, d'autres non. A 0, l'avantage reste
# celui de la competition et le modele est inchange ; a 1, il est celui que
# l'equipe a montre, regularise vers celui de la competition selon la taille de
# son echantillon.
#
# A ne pas confondre avec la "correction du lieu" essayee puis ecartee (README),
# qui neutralisait le lieu DANS l'historique avant d'en faire la moyenne. Ici
# l'historique n'est pas touche : c'est le multiplicateur final qui est module.
HOME_EDGE = 0.0

# Part de la dispersion due a l'INCERTITUDE D'ESTIMATION, en plus de celle du
# processus lui-meme. Zero = le modele ne tient pas compte de cette incertitude,
# ce qu'il faisait jusqu'ici.
#
# La `dispersion` d'un modele mesure combien sa grandeur varie autour d'un
# lambda CONNU. Mais lambda n'est pas connu : il est estime sur une poignee de matchs. La loi
# predictive vraie est donc un melange -- Gamma-Poisson -- plus large que
# Poisson(lambda estime), d'autant plus large que l'echantillon est court :
#
#     variance = lambda + variance(lambda estime) ~= lambda * (1 + 1 / n)
#
# d'ou un terme en 1/n ajoute au rapport variance/moyenne. Le poids reste un
# parametre parce que lambda ne sort pas d'une moyenne simple mais du modele de
# Maher, dont la variance d'estimation n'a pas d'expression fermee ici.
#
# Ce terme repond a un constat de mesure : sur deux echantillons independants,
# les propositions annoncees entre 70 et 90 % se realisent 2,5 a 3 points en
# dessous de ce qu'elles annoncent, et ce sont les grandeurs estimees sur le
# moins de matchs -- corners, tirs, cartons -- qui portent l'ecart.
ESTIMATION_DISPERSION = 0.0

# Retrecissement du nombre attendu lui-meme vers la moyenne de la competition,
# en matchs fictifs. Zero = pas de retrecissement.
#
# La regularisation existante porte sur les FORCES (attaque, defense) prises
# separement. Le modele en multiplie deux, et deux estimations chacune
# raisonnable peuvent composer un lambda extreme. Ce second niveau borne le
# produit, la ou le premier borne ses facteurs.
LAMBDA_SHRINK = 0.0

# Poids de l'echantillon dans le melange des buts attendus, en matchs fictifs.
# Zero = le melange garde le poids constant du critere 11, et rien ne change.
#
# Le critere 11 melange deux estimations de la meme quantite avec un poids FIXE
# (0.25) : `lambda = 0.75 * buts + 0.25 * xG`. Or les deux estimations ne se
# degradent pas au meme rythme.
#
# Les buts sont un comptage tres faible -- environ 1.35 par equipe et par match.
# Leur moyenne sur n matchs a donc une variance d'ordre 1.35 / n. Les buts
# attendus mesurent la meme production offensive avec une variance par match
# trois fois moindre, mais ils ne sont pas la meme grandeur : la finition existe,
# et un xG ne la voit pas. Le xG porte donc un BIAIS constant la ou les buts
# portent une VARIANCE en 1 / n.
#
#     erreur(buts) ~= variance / n        erreur(xG) ~= biais^2
#
# Le poids optimal est celui qui equilibre les deux, et il DEPEND de n : sur
# trois matchs la variance ecrase le biais et le xG doit peser lourd ; sur
# trente c'est l'inverse. Un poids fixe est la moyenne de deux regimes qui
# n'ont rien a voir.
#
# D'ou la forme retenue, celle-la meme que la regularisation (`_shrink`) :
#
#     melange(n) = poids + (1 - poids) * XG_ECHANTILLON / (n + XG_ECHANTILLON)
#
# `XG_ECHANTILLON` s'interprete comme le nombre de matchs a partir duquel les
# buts observes valent le xG. A zero, `melange` vaut le poids du critere et le
# modele est exactement celui d'avant -- ce que les tests verifient.
#
# Ce reglage repond a un constat de mesure deja au dossier : a 0.50 et 0.75 le
# melange fixe AMELIORE les propositions mais DEGRADE l'issue (README). Les deux
# mesures ne portent pas sur les memes echantillons -- les propositions sont
# dominees par les grandeurs estimees sur peu de matchs, l'issue vient des buts
# qui en ont davantage. Un poids qui suit l'echantillon peut donc, en principe,
# gagner sur les deux. C'est une hypothese : elle n'est adoptee que si
# `backtest.tune` la confirme, et le defaut reste zero jusque-la.
XG_ECHANTILLON = 0.0


class Params(NamedTuple):
    """Reglages du modele, groupes pour pouvoir etre compares entre eux.

    `backtest.tune` fait varier ces trois valeurs sur les memes matchs et les
    classe par log-vraisemblance : c'est ainsi qu'elles ont ete choisies, et
    c'est la seule facon honnete d'en changer. Les valeurs par defaut sont
    celles retenues ; le detail de la mesure est dans le README.

    Elles sont enregistrees dans chaque fiche de prevision : une prevision qui
    ne dit pas avec quels reglages elle a ete produite n'est pas verifiable.
    """

    rho: float = RHO
    half_life: float = HALF_LIFE
    shrinkage: float = SHRINKAGE
    home_edge: float = HOME_EDGE
    lambda_shrink: float = LAMBDA_SHRINK
    estimation_dispersion: float = ESTIMATION_DISPERSION
    xg_echantillon: float = XG_ECHANTILLON


DEFAULT_PARAMS = Params()


class NotEnoughData(RuntimeError):
    """Echantillon insuffisant pour calculer une prevision honnete."""
