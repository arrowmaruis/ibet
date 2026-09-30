"""Modele de l'issue du match : 1X2, double chance, les deux marquent, scores exacts.

L'issue n'a pas de comptage propre : elle se lit sur la loi jointe des deux
SCORES. Ce modele ne fait donc aucune estimation ; il recoit du modele des buts
les nombres attendus definitifs (contexte applique) et leurs dispersions, et en
tire tout ce qui porte sur le resultat.

Il travaille sur la meme matrice que les buts, et c'est voulu : issue, scores
exacts et seuils de buts doivent decouler d'une SEULE loi, sans quoi la fiche
afficherait deux lectures incompatibles de la meme rencontre.

Ses resultats sont publies dans la fiche des buts (`resultat`,
`p_les_deux_marquent`, `scores_probables`) et ses propositions y sont greffees :
c'est la forme que lisent l'exportateur, le serveur, le marche et la
verification.
"""

from __future__ import annotations

from typing import Any

from .lois import score_matrix
from .reglages import RHO


def outcome_probabilities(
    lam_home: float,
    lam_away: float,
    rho: float = RHO,
    phi_home: float = 1.0,
    phi_away: float = 1.0,
) -> dict[str, float]:
    """(victoire domicile, nul, victoire exterieur).

    Les dispersions viennent de la meme grille que les scores exacts : issue et
    scores doivent decouler d'une seule loi, sans quoi la fiche afficherait deux
    lectures incompatibles de la meme rencontre.
    """
    win = draw = loss = 0.0
    for goals_home, row in enumerate(
        score_matrix(lam_home, lam_away, rho, phi_home, phi_away)
    ):
        for goals_away, joint in enumerate(row):
            if goals_home > goals_away:
                win += joint
            elif goals_home == goals_away:
                draw += joint
            else:
                loss += joint
    return {"domicile": win, "nul": draw, "exterieur": loss}


def both_teams_score(
    lam_home: float,
    lam_away: float,
    rho: float = RHO,
    phi_home: float = 1.0,
    phi_away: float = 1.0,
) -> float:
    """P(les deux equipes marquent)."""
    grid = score_matrix(lam_home, lam_away, rho, phi_home, phi_away)
    return sum(sum(row[1:]) for row in grid[1:])


def most_likely_scores(
    lam_home: float,
    lam_away: float,
    rho: float = RHO,
    top: int = 3,
    phi_home: float = 1.0,
    phi_away: float = 1.0,
) -> list[tuple[int, int, float]]:
    """Scores exacts les plus probables, sur la meme matrice que les issues.

    Sans cette matrice commune, les pourcentages des scores exacts et ceux des
    issues, affiches l'un sous l'autre, ne seraient pas sur la meme echelle.
    """
    cells = [
        (home, away, joint)
        for home, row in enumerate(
            score_matrix(lam_home, lam_away, rho, phi_home, phi_away)
        )
        for away, joint in enumerate(row)
    ]
    cells.sort(key=lambda cell: cell[2], reverse=True)
    return cells[:top]


class ModeleIssue:
    """Issue du match, tiree des nombres de buts attendus."""

    cle = "issue"
    version = "1.3.0"

    def prevoir(
        self,
        lam_home: float,
        lam_away: float,
        rho: float,
        phi_home: float,
        phi_away: float,
    ) -> dict[str, Any]:
        """Resultat, les deux marquent et scores exacts, sur une seule loi.

        Les memes dispersions effectives que les echelles des buts, et non 1.0.
        Elles valent 1.0 tant que `estimation_dispersion` est nul.
        """
        return {
            "resultat": outcome_probabilities(
                lam_home, lam_away, rho, phi_home, phi_away
            ),
            "p_les_deux_marquent": both_teams_score(
                lam_home, lam_away, rho, phi_home, phi_away
            ),
            "scores_probables": most_likely_scores(
                lam_home, lam_away, rho, 3, phi_home, phi_away
            ),
        }

    def candidats(
        self,
        teams: tuple[str, str],
        outcome: dict[str, float] | None,
        btts: float | None,
    ) -> list[dict[str, Any]]:
        """Propositions de l'issue : 1X2, double chance, les deux marquent."""
        home, away = teams
        candidates: list[dict[str, Any]] = []

        def add(label: str, probability: float, family: str) -> None:
            candidates.append({"libelle": label, "p": probability, "famille": family})

        if outcome:
            add("Victoire %s" % home, outcome["domicile"], "issue")
            add("Victoire %s" % away, outcome["exterieur"], "issue")
            add("Match nul", outcome["nul"], "issue")
            add("%s ou nul" % home, outcome["domicile"] + outcome["nul"], "double chance")
            add("%s ou nul" % away, outcome["exterieur"] + outcome["nul"], "double chance")
            add("Pas de match nul", 1.0 - outcome["nul"], "double chance")
        if btts is not None:
            add("Les deux equipes marquent", btts, "les deux marquent")
            add("Une equipe au moins ne marque pas", 1.0 - btts, "les deux marquent")
        return candidates
