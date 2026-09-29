"""Les « plus de » et les « moins de » tiennent-ils autant leurs promesses ?

Sur les fiches emises, les « moins de X tirs cadres » echouaient nettement plus
qu'annonce, les « plus de X corners au total » aussi, quand les autres tenaient.
96 matchs ne suffisent pas a trancher : ce module rejoue l'archive.

Pour chaque match rejoue, on reconstruit les propositions que le systeme ferait
-- lignes autour de la ligne principale (celle dont le depassement est le plus
proche de 50 %), les deux faces, au total et par equipe, dans la zone
proposable (60 a 85 %) -- puis on compare annonce et observe PAR SENS.

    corners, tirs cadres   archive football-data.co.uk (`mesure_historique`),
                           variante du modele en service (cotes du marche),
                           reglee jusqu'en 2022-23, jugee sur 2023-24 et apres ;
    cartons jaunes         feuilles de match (`mesure_cartons`), modele 3.0.0
                           (historique de l'arbitre + variance), 40 % recents.

`recalage` cherche, sur le reglage seulement, le facteur multiplicatif des
nombres attendus qui equilibre les deux sens, puis le juge sur le test.

Usage :
    python -m ibet mesurer-sens
"""

from __future__ import annotations

import math
import sys
from collections import defaultdict
from typing import Any, Callable, Iterable

from ibet.evaluation import mesure_cartons, mesure_historique
from ibet.modeles import cartons as m_cartons
from ibet.modeles import corners as m_corners
from ibet.modeles import tirs_cadres as m_tirs
from ibet.modeles.discipline import Reglage
from ibet.modeles.lois import dispersion_du_total, team_over_probability, total_over_probability

ZONE = (0.60, 0.85)

MODELES = {
    "corners": m_corners.ModeleCorners,
    "tirs_cadres": m_tirs.ModeleTirsCadres,
    "cartons_jaunes": m_cartons.ModeleCartonsJaunes,
}


def _lignes_ouvertes(p_plus: Callable[[float], float], gamme, ecart) -> list[float]:
    bas, haut = gamme
    toutes = [bas + k for k in range(int(round(haut - bas)) + 1)]
    principale = min(toutes, key=lambda l: abs(p_plus(l) - 0.5))
    return [l for l in toutes if abs(l - principale) <= ecart + 1e-9]


def propositions(grandeur: str, ld: float, le: float, yd: int, ye: int,
                 phi: float, corr: float) -> Iterable[tuple[str, str, float, bool]]:
    """(portee, sens, probabilite, realisee) des options proposables d'un match."""
    m = MODELES[grandeur]
    phi_t = dispersion_du_total(ld, le, phi, corr)
    total = yd + ye

    def plus_total(l: float) -> float:
        return total_over_probability(ld, le, l, phi_t)

    for l in _lignes_ouvertes(plus_total, m.gamme_total, m.ecart_total):
        p = plus_total(l)
        yield "total", "plus", p, total > l
        yield "total", "moins", 1.0 - p, total < l
    for lam, y in ((ld, yd), (le, ye)):
        def plus_eq(l: float, lam=lam) -> float:
            return team_over_probability(lam, l, phi)
        for l in _lignes_ouvertes(plus_eq, m.gamme_equipe, m.ecart_equipe):
            p = plus_eq(l)
            yield "equipe", "plus", p, y > l
            yield "equipe", "moins", 1.0 - p, y < l


def mesurer(grandeur: str, matchs: list[dict[str, Any]], facteur: float = 1.0) -> dict:
    """Annonce / observe par (portee, sens), erreur type groupee par match."""
    phi = MODELES[grandeur].dispersion
    corr = MODELES[grandeur].correlation
    par: dict[tuple[str, str], list[tuple[int, float, bool]]] = defaultdict(list)
    for i, x in enumerate(matchs):
        ld, le = x["ld"] * facteur, x["le"] * facteur
        for portee, sens, p, ok in propositions(grandeur, ld, le, x["yd"], x["ye"], phi, corr):
            if ZONE[0] <= p <= ZONE[1]:
                par[(portee, sens)].append((i, p, ok))
                par[("tout", sens)].append((i, p, ok))
    rendu = {}
    for cle, obs in par.items():
        n = len(obs)
        annonce = sum(p for _, p, _ in obs) / n
        observe = sum(ok for _, _, ok in obs) / n
        groupes: dict[int, float] = defaultdict(float)
        for i, p, ok in obs:
            groupes[i] += p - ok
        m = len(groupes)
        ecart = annonce - observe
        centre = [g - ecart * n / m for g in groupes.values()]
        erreur = math.sqrt(sum(c * c for c in centre) * m / (m - 1)) / n if m > 1 else float("nan")
        rendu[cle] = {"n": n, "matchs": m, "annonce": annonce, "observe": observe,
                      "ecart": ecart, "erreur": erreur}
    return rendu


def recalage(grandeur: str, reglage: list[dict]) -> float:
    """Facteur des nombres attendus qui annule l'ecart entre les deux sens."""
    meilleur, score = 1.0, float("inf")
    for k in range(80, 121):
        f = k / 100.0
        r = mesurer(grandeur, reglage, f)
        dp = r.get(("tout", "plus"), {}).get("ecart", 0.0)
        dm = r.get(("tout", "moins"), {}).get("ecart", 0.0)
        s = dp * dp + dm * dm
        if s < score:
            meilleur, score = f, s
    return meilleur


def _archive(grandeur: str) -> tuple[list[dict], list[dict]]:
    """Corners / tirs cadres : variante du modele en service, sur l'archive."""
    lignes = mesure_historique.rejouer(grandeur)
    reglage = [l for l in lignes if l["date"] < mesure_historique.DEBUT_TEST]
    test = [l for l in lignes if l["date"] >= mesure_historique.DEBUT_TEST]
    # corners 3.0.0 : cotes 1X2 seules ; tirs cadres 2.1.0 : 1X2 + plus / moins 2,5.
    coefs = mesure_historique.ajuster_marche(reglage, "ref", avec25=(grandeur == "tirs_cadres"))

    def forme(l: dict) -> dict:
        ld, le = mesure_historique._predire(l, coefs, "ref")
        return {"ld": ld, "le": le, "yd": int(l["yd"]), "ye": int(l["ye"])}

    return [forme(l) for l in reglage], [forme(l) for l in test]


def _feuilles() -> tuple[list[dict], list[dict]]:
    """Cartons : modele 3.0.0 rejoue sur les feuilles de match."""
    notes = mesure_cartons.rejouer(Reglage(), {"arbitre": 0.5, "variance_arbitre": 1.0},
                                   avec_lambdas=True)
    matchs = [{"ld": n["lambdas"]["historique"][0], "le": n["lambdas"]["historique"][1],
               "yd": n["yd"], "ye": n["ye"]} for n in notes]
    coupe = int(len(matchs) * (1 - mesure_cartons.PART_TEST))
    return matchs[:coupe], matchs[coupe:]


def _afficher(titre: str, r: dict) -> None:
    print("  %s" % titre)
    for portee in ("tout", "total", "equipe"):
        for sens in ("plus", "moins"):
            x = r.get((portee, sens))
            if not x:
                continue
            print("    %-7s %-6s %6d props %5d matchs  annonce %5.1f %%  observe %5.1f %%  ecart %+5.1f +/- %.1f%s"
                  % (portee, sens, x["n"], x["matchs"], 100 * x["annonce"], 100 * x["observe"],
                     100 * x["ecart"], 100 * x["erreur"],
                     "  *" if abs(x["ecart"]) > 2 * x["erreur"] else ""))


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m ibet mesurer-sens",
                                     description="Calibration des « plus de » et « moins de », par grandeur.")
    parser.add_argument("--grandeur", choices=list(MODELES), action="append",
                        help="Par defaut : toutes")
    args = parser.parse_args(argv)
    for grandeur in args.grandeur or list(MODELES):
        reglage, test = _feuilles() if grandeur == "cartons_jaunes" else _archive(grandeur)
        print("\n== %s : %d matchs de reglage, %d de test" % (grandeur, len(reglage), len(test)))
        _afficher("TEST, modele en service", mesurer(grandeur, test))
        f = recalage(grandeur, reglage)
        print("  recalage choisi sur le reglage : x%.2f" % f)
        _afficher("TEST, recale x%.2f" % f, mesurer(grandeur, test, f))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
