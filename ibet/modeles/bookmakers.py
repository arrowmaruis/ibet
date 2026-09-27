"""Les bookmakers de reference : ce que chacun paierait une proposition.

iBET est un systeme d'analyse, pas l'outil d'un seul parieur : ses
utilisateurs jouent chez des operateurs differents, et un meme pari ne vaut
pas la meme chose partout. Une proposition payee 1.30 chez l'un peut l'etre
1.18 chez l'autre -- en dessous de ce qu'un coupon accepte.

Chaque operateur est decrit par sa MARGE, mesuree et non supposee : la somme
des inverses de ses cotes 1X2, moins 1, mediane sur les matchs releves.

    football-data.co.uk   saisons 2024-25 et 2025-26, sept grands championnats
    betexplorer.com       comparateur, 120 matchs de sept championnats,
                          aout-septembre 2026 (cotes de cloture)
    the-odds-api.com      Ligue 1, 18 matchs, septembre 2026 (versions
                          francaises de Betclic, Winamax, Unibet)
    sites des operateurs  SportyBet (Nigeria, Ghana, Kenya, 300 matchs a
                          venir) et Betika (Kenya, 75 matchs), septembre 2026

Quatre operateurs AFRICAINS figurent dans la liste : SportyBet, Megapari,
Mozzartbet et Betika. Melbet, 22Bet, Bet9ja et Premier Bet n'ont pas pu etre
mesures (flux refuses ou non publics) : ils n'y sont pas, faute de marge
connue.

La marge des marches SPECIAUX (corners, tirs cadres, cartons) n'est publiee
par aucune source gratuite ; elle est plus forte que celle du 1X2 sur les
marches de niche. On l'estime a la marge 1X2 plus MAJORATION_SPECIAUX -- une
hypothese, pas une mesure.

Cote payee d'une proposition de probabilite p, marge M repartie sur les
issues : 1 / (p . (1 + M)).
"""

from __future__ import annotations

from typing import Any, NamedTuple

# Marge supplementaire supposee sur corners, tirs cadres et cartons.
MAJORATION_SPECIAUX = 0.03

# Une proposition n'est retenue que si au moins ce nombre d'operateurs la
# paierait au-dessus de la cote minimale : un pari que seul un operateur
# confidentiel rend jouable n'est pas une proposition pour tout le monde.
OPERATEURS_MIN = 3


class Bookmaker(NamedTuple):
    nom: str
    marge: float          # marge 1X2 mesuree
    source: str
    zone: str = "international"


BOOKMAKERS: tuple[Bookmaker, ...] = (
    Bookmaker("1xBet", 0.016, "football-data.co.uk, 2 299 matchs"),
    Bookmaker("Pinnacle", 0.037, "football-data.co.uk, 3 561 matchs"),
    Bookmaker("SportyBet", 0.040, "sportybet.com (NG, GH, KE), 300 matchs", "Afrique"),
    Bookmaker("Megapari", 0.041, "betexplorer.com, 120 matchs", "Afrique"),
    Bookmaker("Mozzartbet", 0.053, "betexplorer.com, 120 matchs", "Afrique"),
    Bookmaker("bet365", 0.056, "football-data.co.uk, 4 728 matchs"),
    Bookmaker("Bwin", 0.059, "football-data.co.uk, 3 830 matchs"),
    Bookmaker("William Hill", 0.061, "football-data.co.uk, 1 791 matchs"),
    Bookmaker("Betway", 0.068, "betexplorer.com, 120 matchs"),
    Bookmaker("Betfair", 0.096, "betexplorer.com (Sportsbook), 120 matchs"),
    Bookmaker("Betika", 0.096, "betika.com (KE), 75 matchs", "Afrique"),
    Bookmaker("Betclic", 0.101, "the-odds-api.com (FR), 18 matchs", "France"),
    Bookmaker("Winamax", 0.130, "the-odds-api.com (FR), 18 matchs", "France"),
    Bookmaker("Unibet", 0.139, "the-odds-api.com (FR), 18 matchs", "France"),
)


def marge(bookmaker: Bookmaker, special: bool = False) -> float:
    return bookmaker.marge + (MAJORATION_SPECIAUX if special else 0.0)


def cotes(p: float, special: bool = False) -> list[tuple[str, float]]:
    """Cote que chaque operateur paierait, de la meilleure a la moins bonne."""
    if p <= 0:
        return []
    rendu = [(b.nom, round(1.0 / (p * (1.0 + marge(b, special))), 2)) for b in BOOKMAKERS]
    return sorted(rendu, key=lambda x: -x[1])


def jouable(p: float, cote_min: float, special: bool = False,
            operateurs_min: int = OPERATEURS_MIN) -> bool:
    """Au moins `operateurs_min` operateurs paient-ils `cote_min` ou plus ?"""
    return sum(1 for _, c in cotes(p, special) if c >= cote_min) >= operateurs_min


def resume(p: float, cote_min: float, special: bool = False) -> dict[str, Any]:
    """Ou prendre la proposition : les trois meilleurs, et chez combien elle
    reste au-dessus de la cote minimale."""
    toutes = cotes(p, special)
    return {
        "meilleurs": toutes[:3],
        "toutes": toutes,
        "fourchette": (toutes[-1][1], toutes[0][1]) if toutes else None,
        "operateurs_ok": sum(1 for _, c in toutes if c >= cote_min),
        "operateurs": len(toutes),
    }
