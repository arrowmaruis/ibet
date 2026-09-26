"""Lois de comptage et loi jointe des deux equipes.

Mathematiques pures, sans connaissance d'aucun evenement : chaque modele
(buts, corners...) leur passe ses propres parametres -- nombre attendu,
dispersion, correlation.
"""

from __future__ import annotations

import math

from .reglages import RHO, NotEnoughData


# Taille minimale de la matrice des scores. Elle suffit pour des buts, ou
# lambda depasse rarement 3 ; `_grid_size` l'elargit pour les grandeurs a
# lambda eleve (corners), ou la queue tronquee ne serait plus negligeable.
MAX_EVENTS = 12


def dispersion_du_total(
    lam_home: float, lam_away: float, dispersion: float, correlation: float
) -> float:
    """Rapport variance / moyenne du TOTAL des deux equipes.

        Var(T) = phi.lam_dom + phi.lam_ext + 2.rho.racine(phi.lam_dom . phi.lam_ext)

    A correlation nulle, on retrouve la dispersion par equipe : la loi du total
    est alors celle que donne la somme de deux lois independantes, et rien ne
    change. C'est ce qui rend la correction sure -- elle ne fait quelque chose
    que la ou une correlation a ete mesuree.
    """
    total = lam_home + lam_away
    if total <= 0:
        return max(dispersion, 0.05)
    variance = dispersion * total + 2 * correlation * math.sqrt(
        max(dispersion * lam_home, 0.0) * max(dispersion * lam_away, 0.0)
    )
    # Une variance nulle ou negative n'a pas de sens : on borne au cas le plus
    # resserre qu'une loi de comptage puisse representer.
    return max(variance / total, 0.05)


def poisson_pmf(k: int, lam: float) -> float:
    """P(X = k) pour X ~ Poisson(lam)."""
    if lam < 0 or k < 0:
        return 0.0
    if lam == 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * lam ** k / math.factorial(k)


def poisson_distribution(lam: float, size: int = MAX_EVENTS) -> list[float]:
    return [poisson_pmf(k, lam) for k in range(size + 1)]


def _binomiale_pmf(k: int, mean: float, dispersion: float) -> float:
    """Loi binomiale de moyenne `mean` et de dispersion donnee, sous 1.

    `n` n'est pas entier en general : on interpole entre les deux entiers qui
    l'encadrent plutot que d'arrondir, sans quoi la moyenne obtenue ne serait
    plus celle demandee -- et c'est la moyenne que le modele a estimee avec
    soin.
    """
    p = 1.0 - dispersion
    if p <= 0.0:
        return poisson_pmf(k, mean)
    n_reel = mean / p
    bas = int(math.floor(n_reel))
    part = n_reel - bas

    def _terme(n: int) -> float:
        if n <= 0 or k > n or k < 0:
            return 1.0 if (n <= 0 and k == 0) else 0.0
        # Probabilite ajustee pour que la moyenne reste `mean` a n entier fixe.
        q = min(1.0, mean / n)
        if q <= 0.0:
            return 1.0 if k == 0 else 0.0
        if q >= 1.0:
            return 1.0 if k == n else 0.0
        return math.exp(
            math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
            + k * math.log(q) + (n - k) * math.log1p(-q)
        )

    return (1.0 - part) * _terme(bas) + part * _terme(bas + 1)


def count_pmf(k: int, mean: float, dispersion: float = 1.0) -> float:
    """P(X = k) pour un comptage de moyenne `mean` et de dispersion donnee.

    `dispersion` est le rapport variance / moyenne. A 1, la loi de Poisson est
    exacte et c'est elle qui est employee. Au-dela, Poisson est trop etroite :
    on passe a une binomiale negative de meme moyenne et de variance
    `mean * dispersion`, parametree par

        p = 1 / dispersion        r = mean / (dispersion - 1)

    ce qui redonne bien moyenne = r(1-p)/p et variance = mean * dispersion.

    SOUS 1, Poisson est trop LARGE, et une binomiale negative ne sait pas se
    resserrer. On passe alors a une binomiale : de moyenne `n p` et de variance
    `n p (1 - p)`, elle donne exactement la dispersion voulue avec

        p = 1 - dispersion        n = mean / p

    C'est la loi d'un nombre d'occasions independantes a probabilite fixe, ce
    qui decrit assez bien un nombre de cartons : il y a un nombre limite de
    situations ou l'arbitre peut en donner un.

    Dispersions mesurees, chacune sur des milliers de matchs, equipe par equipe
    rapportee a sa propre moyenne (voir `dispersion` dans chaque modele) : buts
    1.173, corners 1.615, tirs cadres 1.396, cartons jaunes 0.847. Les cartons sont la seule grandeur
    SOUS-dispersee -- il s'en donne un nombre remarquablement regulier.
    """
    if mean <= 0:
        return poisson_pmf(k, mean)
    if dispersion < 1.0:
        return _binomiale_pmf(k, mean, dispersion)
    if dispersion == 1.0:
        return poisson_pmf(k, mean)
    p = 1.0 / dispersion
    r = mean / (dispersion - 1.0)
    return math.exp(
        math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1)
        + r * math.log(p) + k * math.log1p(-p)
    )


def count_distribution(
    mean: float, dispersion: float = 1.0, size: int = MAX_EVENTS
) -> list[float]:
    return [count_pmf(k, mean, dispersion) for k in range(size + 1)]


def _grid_size(lam: float, dispersion: float = 1.0) -> int:
    """Borne de troncature de la matrice des scores.

    MAX_EVENTS suffit pour des buts (lambda ~ 1.5, masse restante < 1e-9), mais
    pas pour des corners : a lambda = 6, P(X > 12) vaut encore 2 pour mille, et
    la renormalisation redistribuerait cette masse sur les seuls petits scores.
    La borne suit donc la moyenne, a six ecarts-types -- ecart-type reel, donc
    sqrt(lambda * dispersion) : une loi plus large a une queue plus longue.
    """
    spread = math.sqrt(max(lam, 0.0) * max(dispersion, 1.0))
    return max(MAX_EVENTS, int(lam + 6 * spread) + 1)


def dixon_coles_tau(
    home: int, away: int, lam_home: float, lam_away: float, rho: float
) -> float:
    """Facteur de dependance des quatre petits scores (Dixon et Coles 1997).

    Le produit de deux Poisson sous-estime les 1-1 et surestime les 1-0 / 0-1 :
    les equipes n'ajustent pas leur jeu independamment l'une de l'autre quand le
    score est serre. Le facteur ne touche que ces quatre cases et vaut 1 partout
    ailleurs, donc l'identite a rho = 0.
    """
    if rho == 0.0:
        return 1.0
    if home == 0 and away == 0:
        return 1.0 - lam_home * lam_away * rho
    if home == 0 and away == 1:
        return 1.0 + lam_home * rho
    if home == 1 and away == 0:
        return 1.0 + lam_away * rho
    if home == 1 and away == 1:
        return 1.0 - rho
    return 1.0


def score_matrix(
    lam_home: float,
    lam_away: float,
    rho: float = RHO,
    phi_home: float = 1.0,
    phi_away: float = 1.0,
) -> list[list[float]]:
    """Loi jointe des deux scores, normalisee.

    Toutes les probabilites de la fiche en decoulent : issue, seuils, scores
    exacts. Passer par la matrice plutot que par des formules fermees a un cout
    negligeable et garantit qu'elles restent coherentes entre elles -- y compris
    quand rho n'est pas nul, ou la loi jointe cesse d'etre un produit et ou
    P(total) ne suit plus une Poisson de parametre la somme.

    `phi_home` et `phi_away` sont les dispersions effectives, celles-la memes
    que les echelles emploient deja (`effective_dispersion`). Les passer ici
    corrige une incoherence : les echelles tenaient compte de ce que lambda est
    ESTIME -- sur trois a cinq matchs, la loi predictive vraie est plus large
    qu'une Poisson de parametre l'estimation --, alors que la grille des scores
    l'ignorait et restait strictement poissonienne. Or c'est elle qui produit
    les scores exacts ET les issues : la fiche affichait donc, cote a cote, des
    seuils qui reconnaissaient l'incertitude d'estimation et des scores qui la
    niaient.

    A 1.0 -- la valeur par defaut, et celle que rend `effective_dispersion`
    quand `estimation_dispersion` vaut zero --, `count_pmf` retombe exactement
    sur Poisson et la matrice est identique a l'ancienne, ce que les tests
    verifient.
    """
    size = max(_grid_size(lam_home, phi_home), _grid_size(lam_away, phi_away))
    home = count_distribution(lam_home, phi_home, size)
    away = count_distribution(lam_away, phi_away, size)
    grid = [
        [
            # Un tau tres negatif rendrait la case negative : la loi de Dixon et
            # Coles n'est definie que pour un rho modere, et on ne produit pas
            # une probabilite negative parce que le reglage sort du domaine.
            max(0.0, p_h * p_a * dixon_coles_tau(h, a, lam_home, lam_away, rho))
            for a, p_a in enumerate(away)
        ]
        for h, p_h in enumerate(home)
    ]
    mass = sum(sum(row) for row in grid)
    if mass <= 0:
        raise NotEnoughData("Distribution degeneree.")
    return [[cell / mass for cell in row] for row in grid]


def over_probability(
    lam_home: float, lam_away: float, line: float, rho: float = RHO
) -> float:
    """P(total des deux equipes > line)."""
    threshold = int(math.floor(line))
    under = sum(
        joint
        for goals_home, row in enumerate(score_matrix(lam_home, lam_away, rho))
        for goals_away, joint in enumerate(row)
        if goals_home + goals_away <= threshold
    )
    return max(0.0, 1.0 - under)


def team_over_probability(
    lam: float, line: float, dispersion: float = 1.0
) -> float:
    """P(X > line) pour une seule equipe.

    Sans facteur de dependance : celui-ci porte sur le couple des deux scores,
    pas sur la loi marginale d'une equipe, qu'il laisse inchangee. `dispersion`
    elargit la loi quand la grandeur varie plus que Poisson ne le permet.
    """
    threshold = int(math.floor(line))
    return max(0.0, 1.0 - sum(count_distribution(lam, dispersion, threshold)))


def total_over_probability(
    lam_home: float, lam_away: float, line: float, dispersion: float = 1.0
) -> float:
    """P(total > line) a partir de la seule loi du total.

    Deux binomiales negatives de meme dispersion partagent le parametre p :
    leur somme est une binomiale negative de meme p et de moyenne cumulee. La
    meme additivite vaut pour deux binomiales de meme p, donc aussi sous 1. On
    peut ainsi traiter le total directement, sans passer par la matrice jointe
    -- qui n'existe que pour les buts, ou le facteur de Dixon-Coles intervient.

    `dispersion` est celle du TOTAL, correlation comprise, et non celle d'une
    equipe : voir `dispersion_du_total`.
    """
    return team_over_probability(lam_home + lam_away, line, dispersion)


def _blend(phi_home: float, phi_away: float, lam_home: float, lam_away: float) -> float:
    """Dispersion du total, moyenne des deux cotes ponderee par leur apport."""
    weight = lam_home + lam_away
    if weight <= 0:
        return (phi_home + phi_away) / 2
    return (phi_home * lam_home + phi_away * lam_away) / weight


def _grid_probability(grid: list[list[float]], predicate) -> float:
    """Masse des cases de la matrice jointe qui verifient `predicate(dom, ext)`.

    Tous les marches qui portent sur le COUPLE des deux scores -- ecart, parite,
    cage inviolee, combines -- se ramenent a une somme de cases. Les calculer
    ainsi plutot que par une formule par marche garantit qu'ils restent d'accord
    entre eux et avec les issues, et qu'ils tiennent compte du facteur de
    Dixon-Coles sans que chacun ait a s'en souvenir.
    """
    return sum(
        cell
        for home, row in enumerate(grid)
        for away, cell in enumerate(row)
        if predicate(home, away)
    )
