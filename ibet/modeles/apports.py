"""Apports exterieurs des modeles par equipe : cotes du marche, styles des onze.

Deux informations que le modele de Maher ignore, appliquees dans cet ordre au
nombre attendu de chaque equipe :

  1. le MARCHE (`corriger_par_le_marche`) : les cotes 1X2 d'avant-match disent
     qui va dominer. Le favori obtient plus de corners et cadre plus de tirs
     que son historique seul ne le laisse croire, et l'historique d'une equipe
     -- dix matchs -- est trop court pour ne pas surestimer ses ecarts : la
     correction resserre donc aussi le nombre attendu vers la moyenne ;
  2. les STYLES des onze alignes (`styles.appliquer`).

La correction du marche est la regression de Poisson mesuree par
`ibet/evaluation/mesure_historique.py` sur l'historique long
(football-data.co.uk, 23 000 matchs de reglage) :

    log lambda' = a + b . log lambda + c . (p_dom - p_ext)       (domicile)
    log lambda' = a + b . log lambda + c . (p_ext - p_dom)       (exterieur)

Sans cotes, elle ne fait rien : le nombre attendu ressort intact.
"""

from __future__ import annotations

import math
from typing import Any

from . import styles

# Garde-fou : la correction du marche ne multiplie pas un nombre attendu par
# plus de 1.6 ni moins de 1/1.6.
BORNE_MARCHE = 1.6


def corriger_par_le_marche(
    lam: tuple[float, float],
    marche: dict[str, float] | None,
    coefs: dict[str, tuple[float, float, float]] | None,
) -> tuple[tuple[float, float], dict[str, Any] | None]:
    """Nombres attendus corriges par les probabilites 1X2 du marche (marge
    retiree : {"domicile", "nul", "exterieur"}), et la trace."""
    if not coefs or not marche:
        return lam, None
    try:
        pd, pe = float(marche["domicile"]), float(marche["exterieur"])
    except (KeyError, TypeError, ValueError):
        return lam, None
    rendu = []
    for rang, (cote, ecart) in enumerate((("domicile", pd - pe), ("exterieur", pe - pd))):
        a, b, c = coefs[cote]
        brut = math.exp(a + b * math.log(max(lam[rang], 1e-6)) + c * ecart)
        rendu.append(max(lam[rang] / BORNE_MARCHE, min(lam[rang] * BORNE_MARCHE, brut)))
    trace = {"p_domicile": round(pd, 3), "p_exterieur": round(pe, 3),
             "lambda_avant_marche": (round(lam[0], 2), round(lam[1], 2)),
             "lambda_apres_marche": (round(rendu[0], 2), round(rendu[1], 2))}
    return (rendu[0], rendu[1]), trace


class AvecApports:
    """Melange pour un `ModeleParEquipe` qui recoit le marche et les styles.

    `apports` : {"marche": {"domicile", "nul", "exterieur"} | None,
                 "styles": {"domicile": {...}, "exterieur": {...}} | None}.

    `prevoir` range la trace sous « apports » et garde les forces d'equipe
    sous « forces » : les forces disent d'ou vient le nombre attendu, les
    apports ce qui l'a deplace.
    """

    poids_styles = 0.0
    coefs_marche: dict[str, tuple[float, float, float]] | None = None

    def ajuster(self, lam, apports):
        if not apports:
            return lam[0], lam[1], None
        trace: dict[str, Any] = {}
        lam, marche = corriger_par_le_marche(lam, apports.get("marche"), self.coefs_marche)
        if marche:
            trace["marche"] = marche
        ld, le, st = styles.appliquer(lam, apports.get("styles"), self.poids_styles)
        if st:
            trace["styles"] = st
        return ld, le, (trace or None)

    def prevoir(self, estimation, teams, baseline, params, correction=None,
                with_candidates=False, apports=None):
        entry = super().prevoir(estimation, teams, baseline, params, correction,
                                with_candidates, apports)
        if apports:
            from .estimation import forces_des_equipes

            trace = entry.pop("forces")
            if trace:
                entry["apports"] = trace
            entry["forces"] = forces_des_equipes(baseline, teams, self.champ, params)
        return entry
