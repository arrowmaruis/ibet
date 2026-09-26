"""Modele des buts.

Le plus riche des modeles d'evenement, parce que les buts sont la seule
grandeur qui :

  - recoit des apports exterieurs sur son nombre attendu -- les buts attendus
    (xG, critere 11) et les notes attaque / defense globales (`forces`) ;
  - porte le facteur de Dixon et Coles sur les petits scores ;
  - alimente le modele de l'issue, qui lit sa loi jointe ;
  - ouvre les marches sur le couple des deux scores (ecart, parite, cage
    inviolee, combines).
"""

from __future__ import annotations

from typing import Any

from .base import ModeleEvenement
from .issue import ModeleIssue
from .lois import _grid_probability, score_matrix

# Seuils usuels, exprimes en demi-unites pour qu'aucun resultat ne tombe dessus.
GOALS_LINE = 2.5

# Dispersion mesuree equipe par equipe, rapportee a sa propre moyenne : 1.173,
# legere sur-dispersion. Correlation des residus entre les deux equipes :
# -0.008 +/- 0.010 (t = -0.8), nulle, laissee a zero. Biais d'echelle mesure a
# -0.02 sur 100 matchs : pas de recalage.
DISPERSION = 1.173
CORRELATION = 0.0
CALIBRATION = 1.0


def melange_xg_effectif(poids: float, effectif: float, k: float) -> float:
    """Part des buts attendus dans le nombre de buts, selon l'echantillon.

    `poids` est le poids constant du critere 11 (son plancher : c'est ce que le
    reglage retenu accorde au xG quand l'echantillon est large). `effectif` est
    l'effectif efficace sur lequel les buts ont ete moyennes, et `k` le nombre
    de matchs fictifs de `XG_ECHANTILLON`.

    A k = 0 la fonction rend `poids` inchange : c'est le melange fixe d'avant.
    Au-dela, elle monte vers 1 quand l'echantillon se reduit -- le xG prend la
    main la ou les buts ne sont plus que du bruit -- et redescend vers `poids`
    quand il s'allonge.
    """
    if k <= 0 or effectif < 0:
        return poids
    return poids + (1.0 - poids) * k / (effectif + k)


# Part laissee au modele quand les notes attaque / defense sont disponibles.
#
# Le modele tire sa discrimination de la forme recente, normalisee dans sa
# competition. Cette construction ne sait pas comparer deux equipes de
# championnats differents, et la regularisation ecrase le peu qu'elle sait : sur
# les fiches emises, le modele n'atteignait 50 % de certitude que 7 fois sur 26,
# la ou le marche va jusqu'a 90 %.
#
# Les notes attaque / defense de `forces.lambdas_attendus` placent toutes les
# equipes sur une echelle unique et rendent directement deux lambdas. Deux
# mesures independantes, toutes deux MONOTONES jusqu'a zero, disent quel poids
# laisser au modele :
#
#   - sur 5 360 matchs en walk-forward, contre une reconstitution du signal de
#     forme : 0 % (100 % coute +0.0280 de Brier, t = +11.7) ;
#   - sur les 21 fiches tranchees ou les notes sont etablies, contre le VRAI
#     modele -- normalisation par competition, correction d'adversaire,
#     contexte compris : 0 % aussi (Brier 0.5949 contre 0.6350, t = -1.67).
#
# La seconde a peu de matchs, la premiere beaucoup de puissance ; elles
# concordent. Le modele n'est donc plus employe pour les BUTS quand les notes
# existent. Il reste :
#
#   - le repli quand elles manquent (14 % des fiches : equipe trop peu vue, ou
#     deux groupes qui ne se sont jamais rencontres) ;
#   - le seul estimateur des corners, tirs cadres et cartons, que les notes ne
#     couvrent pas ;
#   - le porteur des corrections de contexte, qui s'appliquent par-dessus.
#
# La valeur reste exposee pour que `backtest.tune` puisse la reprendre sur un
# echantillon plus large.
POIDS_MODELE = 0.0

# Plancher d'un lambda apres melange.
LAMBDA_MIN = 0.05


def melanger_lambdas(
    modele: tuple[float, float],
    forces: tuple[float, float] | None,
    poids_modele: float = POIDS_MODELE,
) -> tuple[float, float]:
    """Combine les nombres attendus du modele et ceux des notes globales.

    A poids 1, le modele seul ; a 0, les notes seules. Sans notes, le modele est
    rendu tel quel : une prevision n'est jamais degradee par l'absence d'une
    information.

    Le melange porte sur les deux lambdas, donc sur le TOTAL autant que sur
    l'ecart. C'est ce qui distingue ces notes d'un classement de type Elo, qui
    ne sait que departager deux equipes : elles disent aussi combien de buts
    attendre -- mesure sur 5 360 matchs, l'erreur absolue sur le total passe de
    1.437 a 1.396, t = -5.9.
    """
    if not forces:
        return modele
    poids = min(1.0, max(0.0, poids_modele))
    return (
        max(LAMBDA_MIN, poids * modele[0] + (1 - poids) * forces[0]),
        max(LAMBDA_MIN, poids * modele[1] + (1 - poids) * forces[1]),
    )



class ModeleButs(ModeleEvenement):
    cle = "buts"
    libelle = "Buts"
    champ = None
    seuil = GOALS_LINE
    seuils_equipe = (0.5, 1.5, 2.5, 3.5)
    seuils_total = (0.5, 1.5, 2.5, 3.5, 4.5, 5.5)
    prefere = "les deux"
    dispersion = DISPERSION
    correlation = CORRELATION
    calibration = CALIBRATION
    dixon_coles = True

    def __init__(self, issue: ModeleIssue | None = None) -> None:
        # Le modele partenaire qui lit la loi jointe des scores.
        self.issue = issue or ModeleIssue()

    def ajuster(
        self, lam: tuple[float, float], apports: dict[str, Any] | None
    ) -> tuple[float, float, dict[str, Any] | None]:
        """Melange du xG, puis des notes globales, sur les nombres attendus.

        `apports` porte `xg` -- (lambdas xG ou None, part du melange) -- et
        `forces` -- lambdas des notes attaque / defense, ou None.
        """
        lam_home, lam_away = lam
        apports = apports or {}
        lam_xg, melange_xg = apports.get("xg") or (None, 0.0)
        if lam_xg is not None and melange_xg > 0:
            # Deux estimations de la meme quantite, melangees et non composees :
            # les buts attendus passent par le meme modele que les buts, avec la
            # meme reference de competition et les memes forces.
            lam_home = (1 - melange_xg) * lam_home + melange_xg * lam_xg[0]
            lam_away = (1 - melange_xg) * lam_away + melange_xg * lam_xg[1]

        # Notes attaque / defense globales : elles remplacent l'estimation du
        # modele pour les buts, total ET ecart. Voir POIDS_MODELE.
        lambdas_forces = apports.get("forces")
        if lambdas_forces is None:
            return lam_home, lam_away, None
        avant = (lam_home, lam_away)
        lam_home, lam_away = melanger_lambdas(avant, lambdas_forces)
        trace = {
            "lambda_modele": (round(avant[0], 3), round(avant[1], 3)),
            "lambda_forces": (round(lambdas_forces[0], 3),
                              round(lambdas_forces[1], 3)),
            "lambda_retenu": (round(lam_home, 3), round(lam_away, 3)),
            "poids_modele": POIDS_MODELE,
        }
        return lam_home, lam_away, trace

    def completer(
        self,
        entry: dict[str, Any],
        teams: tuple[str, str],
        lam_home: float,
        lam_away: float,
        rho: float,
        phi_home: float,
        phi_away: float,
    ) -> list[dict[str, Any]]:
        """Branche le modele de l'issue sur les nombres de buts definitifs."""
        issue = self.issue.prevoir(lam_home, lam_away, rho, phi_home, phi_away)
        entry.update(issue)
        return self.issue.candidats(
            teams, issue["resultat"], issue["p_les_deux_marquent"]
        )

    def candidats_propres(
        self, teams: tuple[str, str], lam_home: float, lam_away: float, rho: float
    ) -> list[dict[str, Any]]:
        """Marches qui portent sur le couple des deux scores.

        Reserves aux buts : "plus de buts pour X que pour Y" ne dirait rien de
        plus que "victoire de X", et un ecart de cartons n'est pas un marche.
        """
        home, away = teams
        noun = self.libelle.lower()
        candidates: list[dict[str, Any]] = []

        def add(label: str, probability: float, family: str) -> None:
            candidates.append({"libelle": label, "p": probability, "famille": family})

        grid = score_matrix(lam_home, lam_away, rho)

        for name, ahead in ((home, True), (away, False)):
            for margin in (2, 3):
                add(
                    "%s gagne par %d %s ou plus" % (name, margin, noun),
                    _grid_probability(
                        grid,
                        (lambda m: (lambda h, a: h - a >= m if ahead else a - h >= m))(
                            margin
                        ),
                    ),
                    "ecart",
                )

        even = _grid_probability(grid, lambda h, a: (h + a) % 2 == 0)
        add("Nombre total de %s pair" % noun, even, "parite")
        add("Nombre total de %s impair" % noun, 1.0 - even, "parite")

        add(
            "%s n'encaisse aucun but" % home,
            _grid_probability(grid, lambda h, a: a == 0),
            "cage inviolee",
        )
        add(
            "%s n'encaisse aucun but" % away,
            _grid_probability(grid, lambda h, a: h == 0),
            "cage inviolee",
        )

        # Combines : deux conditions en une. `verify.check_offer` les tranche en
        # coupant sur " et " et en jugeant chaque moitie, donc rien de nouveau
        # cote verification.
        for line in (1.5, 2.5):
            add(
                "Les deux equipes marquent et plus de %g %s" % (line, noun),
                _grid_probability(
                    grid,
                    (lambda l: lambda h, a: h >= 1 and a >= 1 and h + a > l)(line),
                ),
                "combine",
            )
            for name, at_home in ((home, True), (away, False)):
                add(
                    "Victoire %s et plus de %g %s" % (name, line, noun),
                    _grid_probability(
                        grid,
                        (lambda l, w: lambda h, a: (h > a if w else a > h) and h + a > l)(
                            line, at_home
                        ),
                    ),
                    "combine",
                )
        return candidates
