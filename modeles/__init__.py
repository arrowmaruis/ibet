"""Modeles d'evenement : un modele par grandeur prevue.

Chaque evenement a son modele, dans son fichier, avec ses propres constantes
mesurees :

    buts.py         -- buts ; porte le xG, les notes globales et l'issue
    issue.py        -- issue du match (1X2, double chance, les deux marquent,
                       scores exacts), branchee sur la loi jointe des buts
    corners.py      -- corners
    tirs_cadres.py  -- tirs cadres
    cartons.py      -- cartons jaunes
    xg.py           -- buts attendus, auxiliaire : estime, jamais parie

Ils partagent un socle qui ne connait aucun evenement :

    reglages.py     -- `Params` et reglages du moteur
    estimation.py   -- modele attaque / defense de Maher
    lois.py         -- lois de comptage et loi jointe
    offres.py       -- selection des propositions et tableau des marches
    base.py         -- le contrat d'un modele d'evenement

`predict.build` orchestre : chaque modele estime, le contexte s'intercale, puis
chaque modele produit sa fiche. Pour ajouter un evenement, ecrire une
sous-classe de `ModeleEvenement` (ou `ModeleParEquipe`) et l'inscrire dans
`MODELES` ci-dessous.
"""

from __future__ import annotations

from .base import Metric, ModeleEvenement, ModeleParEquipe
from .buts import ModeleButs
from .cartons import ModeleCartonsJaunes
from .corners import ModeleCorners
from .issue import ModeleIssue
from .tirs_cadres import ModeleTirsCadres
from .xg import ModeleXG

ISSUE = ModeleIssue()
BUTS = ModeleButs(ISSUE)
CORNERS = ModeleCorners()
TIRS_CADRES = ModeleTirsCadres()
CARTONS_JAUNES = ModeleCartonsJaunes()
XG = ModeleXG()

# Les grandeurs prevues, dans l'ordre de la fiche.
MODELES: tuple[ModeleEvenement, ...] = (BUTS, CORNERS, TIRS_CADRES, CARTONS_JAUNES)

# Estimees pour servir les autres, jamais prevues pour elles-memes.
AUXILIAIRES: tuple[ModeleEvenement, ...] = (XG,)

PAR_CLE: dict[str, ModeleEvenement] = {m.cle: m for m in MODELES + AUXILIAIRES}


def modele(cle: str) -> ModeleEvenement:
    """Le modele d'une grandeur, par sa cle ("buts", "corners"...)."""
    return PAR_CLE[cle]


__all__ = [
    "AUXILIAIRES", "BUTS", "CARTONS_JAUNES", "CORNERS", "ISSUE", "MODELES",
    "Metric", "ModeleButs", "ModeleCartonsJaunes", "ModeleCorners",
    "ModeleEvenement", "ModeleIssue", "ModeleParEquipe", "ModeleTirsCadres",
    "ModeleXG", "PAR_CLE", "TIRS_CADRES", "XG", "modele",
]
