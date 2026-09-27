"""Selection des propositions et tableau des marches.

Commun a tous les modeles : chacun produit ses candidates, la selection se fait
ensuite sur l'ensemble, avec les memes regles.
"""

from __future__ import annotations

from typing import Any, Sequence

from . import bookmakers

# Propositions retenues a l'affichage : les plus probables, et seulement
# celles qui disent quelque chose. En deca de OFFER_MIN, annoncer une
# proposition revient a annoncer un tirage a pile ou face ; au-dela de
# OFFER_CEILING, elle est vraie par construction ("moins de 5.5 buts" a 99.5 %)
# et n'apprend rien non plus -- aucun marche ne la propose.
OFFER_MIN = 0.60
OFFER_CEILING = 0.95
OFFER_MAX = 6

# Ce que paieraient les dix bookmakers de reference (`bookmakers.py`). Une
# proposition n'est retenue que si au moins trois d'entre eux la paient
# COTE_MIN ou plus -- « plus de 0.5 but » a 93 % se paie 1.01, personne ne la
# prend et aucun coupon ne l'accepte. `MARGE_BOOKMAKER` est la marge mediane
# des dix, pour la cote « moyenne » affichee.
MARGE_BOOKMAKER = 0.067
COTE_MIN = 1.15


def cote_juste(p: float) -> float | None:
    """Cote a partir de laquelle le pari est rentable selon le modele."""
    return round(1.0 / p, 2) if p > 0 else None


def cote_estimee(p: float) -> float | None:
    """Cote qu'un bookmaker median proposerait, marge comprise."""
    return round(1.0 / (p * (1.0 + MARGE_BOOKMAKER)), 2) if p > 0 else None


def jouable(p: float, special: bool = False) -> bool:
    """Assez d'operateurs la paient-ils au-dessus de la cote minimale ?"""
    return p <= OFFER_CEILING and bookmakers.jouable(p, COTE_MIN, special)


def _coter(offer: dict[str, Any], special: bool = False) -> dict[str, Any]:
    return dict(offer, cote_juste=cote_juste(offer["p"]), cote_estimee=cote_estimee(offer["p"]),
                bookmakers=bookmakers.resume(offer["p"], COTE_MIN, special))


# Familles deja couvertes, seuil par seuil, par les echelles : les repeter dans
# le tableau des marches n'apprendrait rien.
LADDER_FAMILIES = ("total", "equipe")

# Tableau des marches : ce que les echelles ne montrent pas. Deux propositions
# par famille suffisent a donner le choix sans noyer la fiche, et sous 5 % une
# proposition n'est plus un pari mais une curiosite.
MARKET_PER_FAMILY = 2
MARKET_MIN = 0.05

# Une meme famille ne peut pas monopoliser la liste. Sans ce plafond, les six
# retenues etaient six seuils voisins de la meme echelle ("moins de 3.5", "moins
# de 4.5", "moins de 5.5"...) : trois facons de dire la meme chose, et aucun
# choix reel. Le parieur veut des propositions qui ne se recouvrent pas.
OFFER_PER_FAMILY = 2


def select_offers(
    candidates: list[dict[str, Any]],
    limit: int = OFFER_MAX,
    per_family: int = OFFER_PER_FAMILY,
    special: bool = False,
) -> list[dict[str, Any]]:
    """Les propositions retenues : les plus sures, et les moins redondantes.

    Sous OFFER_MIN, une proposition n'engage rien ; si moins de trois des dix
    bookmakers de reference la paient COTE_MIN, elle enonce une evidence. Entre les deux, les plus probables d'abord -- mais
    avec un plafond par famille.

    Prendre simplement les N plus probables donnait une liste ou tout se
    recouvrait : "moins de 3.5 buts", "moins de 4.5 buts" et "moins de 5.5 buts"
    ne sont pas trois choix, c'est le meme pari trois fois. On sert donc au plus
    `per_family` propositions par famille, puis on complete sans contrainte s'il
    reste de la place -- mieux vaut une liste pleine et un peu redondante qu'une
    liste courte.
    """
    retained = sorted(
        (_coter(c, special) for c in candidates
         if c["p"] >= OFFER_MIN and jouable(c["p"], special)),
        key=lambda offer: offer["p"],
        reverse=True,
    )
    chosen: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    # Le plafond se relache par paliers plutot que de sauter d'un coup : si trois
    # familles seulement tiennent dans la fourchette, on en sert trois de chaque
    # avant d'en servir quatre d'une seule. Un plafond leve d'emblee redonnait la
    # liste redondante qu'il etait cense empecher.
    cap = max(1, per_family)
    while len(chosen) < limit and cap <= limit:
        for candidate in retained:
            if candidate in chosen:
                continue
            family = candidate.get("famille", "")
            if seen.get(family, 0) >= cap:
                continue
            seen[family] = seen.get(family, 0) + 1
            chosen.append(candidate)
            if len(chosen) >= limit:
                break
        cap += 1
    # Le remplissage par paliers casse l'ordre : on le retablit, sans quoi la
    # liste affiche 65 % avant 77 %.
    chosen.sort(key=lambda offer: offer["p"], reverse=True)
    return chosen


def market_board(
    candidates: list[dict[str, Any]], exclude: Sequence[dict[str, Any]] = (),
    special: bool = False,
) -> list[dict[str, Any]]:
    """Les marches disponibles, par famille, au-dela de la selection.

    La selection ne retient que les propositions sures ; or "victoire de X par
    deux buts d'ecart" ne depasse jamais 50 %, et n'en est pas moins un pari
    qu'on peut vouloir prendre. La retirer de la fiche revient a decider a la
    place du lecteur quels marches existent.

    On rend donc le tableau complet, familles couvertes par les echelles mises a
    part -- celles-la sont deja affichees seuil par seuil, plus finement que ce
    tableau ne le ferait.
    """
    already = {offer["libelle"] for offer in exclude}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        family = candidate.get("famille", "")
        # Ni curiosite sous 5 %, ni evidence payee sous la cote minimale.
        if (family in LADDER_FAMILIES or candidate["p"] < MARKET_MIN
                or not jouable(candidate["p"], special)):
            continue
        # Ce qui est deja dans la selection n'a pas a etre repete deux tableaux
        # plus bas : le lecteur croirait a deux propositions distinctes.
        if candidate["libelle"] in already:
            continue
        grouped.setdefault(family, []).append(_coter(candidate, special))

    board = []
    for family, offers in grouped.items():
        offers = sorted(offers, key=lambda o: o["p"], reverse=True)
        board.append({"famille": family, "propositions": offers[:MARKET_PER_FAMILY]})
    board.sort(key=lambda row: row["propositions"][0]["p"], reverse=True)
    return board
