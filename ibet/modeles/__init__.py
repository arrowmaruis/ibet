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

Chaque modele porte une `version`, et le moteur commun la sienne
(`VERSION_MOTEUR`). Elles sont inscrites dans chaque fiche ; leur histoire --
changements, points forts, points faibles, mesures -- est tenue dans
`journal/`. Voir `journal/README.md` pour les regles.

`predict.build` orchestre : chaque modele estime, le contexte s'intercale, puis
chaque modele produit sa fiche. Pour ajouter un evenement, ecrire une
sous-classe de `ModeleEvenement` (ou `ModeleParEquipe`) et l'inscrire dans
`MODELES` ci-dessous.
"""

from __future__ import annotations

from pathlib import Path

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

# Version du socle commun (estimation, lois, offres, reglages). Une prevision
# depend de son modele ET du moteur : une amelioration du moteur change toutes
# les grandeurs a la fois, et doit se lire comme telle dans les etudes.
VERSION_MOTEUR = "1.0.0"

# Journal des versions : un fichier Markdown par modele, plus un pour le moteur.
JOURNAL = Path(__file__).parent / "journal"


def versions() -> dict[str, str]:
    """Versions en service, inscrites dans chaque fiche emise."""
    rendu = {"moteur": VERSION_MOTEUR}
    for m in MODELES:
        rendu[m.cle] = m.version
        if m is BUTS:
            rendu[ISSUE.cle] = ISSUE.version
    for m in AUXILIAIRES:
        rendu[m.cle] = m.version
    return rendu


def modele(cle: str) -> ModeleEvenement:
    """Le modele d'une grandeur, par sa cle ("buts", "corners"...)."""
    return PAR_CLE[cle]


__all__ = [
    "AUXILIAIRES", "BUTS", "CARTONS_JAUNES", "CORNERS", "ISSUE", "MODELES",
    "Metric", "ModeleButs", "ModeleCartonsJaunes", "ModeleCorners",
    "ModeleEvenement", "ModeleIssue", "ModeleParEquipe", "ModeleTirsCadres",
    "ModeleXG", "PAR_CLE", "TIRS_CADRES", "VERSION_MOTEUR", "XG", "JOURNAL",
    "modele", "versions",
]
