"""Mesure ce que les styles des joueurs alignes apportent aux corners et aux tirs cadres.

Rejoue dans l'ordre chronologique les matchs dont les statistiques par joueur
sont archivees (`rattrapage_joueurs.py`). Pour chacun, les index ne
connaissent que les matchs anterieurs ; on prevoit, on note, puis on ajoute
le match. Aucune information posterieure ne fuit.

Reference : un Maher reduit a la grandeur, reconstruit sur la meme archive --
moyenne de la competition a domicile et a l'exterieur, ce que l'equipe obtient,
ce que l'adversaire concede --, sous la loi du modele en service (binomiale
negative, dispersion et correlation de `modeles/corners.py` ou
`modeles/tirs_cadres.py`). Les variantes le multiplient par le facteur de
`modeles.styles` :

    aligne        onze reellement aligne (connu une heure avant le match) ;
    habituel      onze des trois derniers matchs (ce qu'on sait la veille) ;
    ... en mode « taux » (taux par 90 du joueur) ou « part » (sa part du
    volume de son equipe).

Le poids du facteur (0 = rien, 1 = entier) est choisi sur les 60 % de matchs
les plus anciens et juge sur les 40 % les plus recents.

Usage :
    python -m ibet mesurer-styles                     # corners
    python -m ibet mesurer-styles --grandeur tirs_cadres
    python -m ibet mesurer-styles --poids             # quels gestes font les corners
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from typing import Any

from ibet.modeles import corners as m_corners
from ibet.modeles import tirs_cadres as m_tirs
from ibet.modeles.discipline import _Serie
from ibet.modeles.lois import (
    count_pmf,
    dispersion_du_total,
    team_over_probability,
    total_over_probability,
)
from ibet.modeles.styles import INDICES, Reglage, Styles
from ibet.stockage import store

PART_TEST = 0.40
MIN_MATCHS = 5
DEMI_VIE_EQUIPE = 180.0
# Effectif fictif de l'equipe, en matchs au niveau de la competition.
LISSAGE_MATCHS = 10.0
POIDS_ESSAYES = (0.25, 0.5, 0.75, 1.0, 1.5)

LOIS = {
    "corners": (m_corners.DISPERSION, m_corners.CORRELATION,
                m_corners.ModeleCorners.seuils_equipe, m_corners.ModeleCorners.seuils_total),
    "tirs_cadres": (m_tirs.DISPERSION, m_tirs.CORRELATION,
                    m_tirs.ModeleTirsCadres.seuils_equipe, m_tirs.ModeleTirsCadres.seuils_total),
}


class Maher:
    """Attendu d'une grandeur par equipe, d'apres les seules equipes."""

    def __init__(self) -> None:
        self.comp: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
        self.obtient: dict[str, _Serie] = defaultdict(_Serie)
        self.concede: dict[str, _Serie] = defaultdict(_Serie)

    def moyennes(self, comp: str) -> tuple[float, float]:
        d, e, n = self.comp.get(comp) or (0.0, 0.0, 0.0)
        tot = [sum(v[i] for v in self.comp.values()) for i in range(3)]
        base_d = tot[0] / tot[2] if tot[2] else 5.0
        base_e = tot[1] / tot[2] if tot[2] else 4.5
        k = 20.0
        return (d + k * base_d) / (n + k), (e + k * base_e) / (n + k)

    def matchs(self, equipe: str, date: str) -> int:
        s = self.obtient.get(equipe)
        return sum(1 for d, _, _ in s.points if d < date) if s else 0

    def attendu(self, comp: str, dom: str, ext: str, date: str) -> tuple[float, float]:
        md, me = self.moyennes(comp)
        k = LISSAGE_MATCHS * (md + me) / 2

        def r(serie: dict[str, _Serie], eq: str) -> float:
            return serie[eq].rapport(date, DEMI_VIE_EQUIPE, k)[0] if eq in serie else 1.0

        return (md * r(self.obtient, dom) * r(self.concede, ext),
                me * r(self.obtient, ext) * r(self.concede, dom))

    def ajouter(self, comp: str, dom: str, ext: str, date: str, yd: float, ye: float) -> None:
        md, me = self.moyennes(comp)
        self.obtient[dom].ajouter(date, yd, md)
        self.obtient[ext].ajouter(date, ye, me)
        self.concede[dom].ajouter(date, ye, me)
        self.concede[ext].ajouter(date, yd, md)
        c = self.comp[comp]
        c[0] += yd
        c[1] += ye
        c[2] += 1


def _valeur(match: dict[str, Any], cote: str, grandeur: str) -> int | None:
    v = ((match.get("stats") or {}).get(cote) or {}).get(grandeur)
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _noter(ld: float, le: float, yd: int, ye: int, grandeur: str) -> dict[str, Any]:
    phi, corr, s_eq, s_tot = LOIS[grandeur]
    phi_t = dispersion_du_total(ld, le, phi, corr)
    t = yd + ye
    total = [(total_over_probability(ld, le, s, phi_t), t > s) for s in s_tot]
    equipe = [(team_over_probability(l, s, phi), y > s) for l, y in ((ld, yd), (le, ye)) for s in s_eq]
    return {
        "ll": math.log(max(count_pmf(yd, ld, phi) * count_pmf(ye, le, phi), 1e-12)),
        "ll_total": math.log(max(count_pmf(t, ld + le, phi_t), 1e-12)),
        "brier_total": sum((p - o) ** 2 for p, o in total) / len(total),
        "brier_equipe": sum((p - o) ** 2 for p, o in equipe) / len(equipe),
        "erreur": ld + le - t,
    }


def rejouer(grandeur: str) -> list[dict[str, Any]]:
    indice = grandeur
    matchs = [m for m in store.stats_joueurs_archivees() if m.get("date")]
    index = {"taux": Styles(Reglage(mode="taux")), "part": Styles(Reglage(mode="part"))}
    maher = Maher()
    notes = []
    for m in matchs:
        yd, ye = _valeur(m, "domicile", grandeur), _valeur(m, "exterieur", grandeur)
        date, comp = m["date"], m["championnat"]
        dom, ext = m["domicile"], m["exterieur"]
        if yd is not None and ye is not None:
            if maher.matchs(dom, date) >= MIN_MATCHS and maher.matchs(ext, date) >= MIN_MATCHS:
                ld0, le0 = maher.attendu(comp, dom, ext, date)
                facteurs = {}
                for mode, idx in index.items():
                    for source in ("aligne", "habituel"):
                        f = []
                        for cote, eq in (("domicile", dom), ("exterieur", ext)):
                            onze = ([j["id"] for j in m["joueurs"][cote] if j.get("titulaire")]
                                    if source == "aligne" else None)
                            f.append(idx.facteur(eq, onze, date, indice)["facteur"])
                        facteurs["%s/%s" % (source, mode)] = tuple(f)
                notes.append({"date": date, "championnat": comp, "ld0": ld0, "le0": le0,
                              "yd": yd, "ye": ye, "facteurs": facteurs})
            maher.ajouter(comp, dom, ext, date, yd, ye)
        for idx in index.values():
            idx.ajouter(m)
    return notes


def _moyenne_et_erreur(valeurs: list[float]) -> tuple[float, float]:
    n = len(valeurs)
    if n < 2:
        return (valeurs[0] if valeurs else 0.0), float("nan")
    moy = sum(valeurs) / n
    var = sum((x - moy) ** 2 for x in valeurs) / (n - 1)
    return moy, math.sqrt(var / n)


def _variantes(n: dict[str, Any], poids: dict[str, float], grandeur: str) -> dict[str, dict]:
    ref = _noter(n["ld0"], n["le0"], n["yd"], n["ye"], grandeur)
    out = {"reference": ref}
    for nom, (fd, fe) in n["facteurs"].items():
        w = poids.get(nom, 1.0)
        out[nom] = _noter(n["ld0"] * fd ** w, n["le0"] * fe ** w, n["yd"], n["ye"], grandeur)
    return out


def _gain(notes: list[dict[str, Any]], nom: str, w: float, grandeur: str) -> float:
    s = 0.0
    for n in notes:
        fd, fe = n["facteurs"][nom]
        a = _noter(n["ld0"] * fd ** w, n["le0"] * fe ** w, n["yd"], n["ye"], grandeur)
        r = _noter(n["ld0"], n["le0"], n["yd"], n["ye"], grandeur)
        s += a["ll"] + a["ll_total"] - r["ll"] - r["ll_total"]
    return s / max(1, len(notes))


def rapport(notes: list[dict[str, Any]], poids: dict[str, float], grandeur: str, titre: str) -> None:
    if not notes:
        return
    print("\n== %s : %d matchs" % (titre, len(notes)))
    print("%-18s %6s %9s %9s %7s %10s %10s %8s" % (
        "variante", "poids", "ll", "d ll", "t", "brier tot", "brier eq", "biais"))
    notees = [_variantes(n, poids, grandeur) for n in notes]
    ref = [v["reference"] for v in notees]
    for nom in notees[0]:
        v = [x[nom] for x in notees]
        ll, _ = _moyenne_et_erreur([x["ll"] + x["ll_total"] for x in v])
        d, e = _moyenne_et_erreur([x["ll"] + x["ll_total"] - r["ll"] - r["ll_total"]
                                   for x, r in zip(v, ref)])
        bt, _ = _moyenne_et_erreur([x["brier_total"] for x in v])
        be, _ = _moyenne_et_erreur([x["brier_equipe"] for x in v])
        biais, _ = _moyenne_et_erreur([x["erreur"] for x in v])
        t = d / e if nom != "reference" and e == e and e > 1e-9 else 0.0
        print("%-18s %6s %9.4f %+9.4f %+7.1f %10.4f %10.4f %+8.2f" % (
            nom, "" if nom == "reference" else "%.2f" % poids.get(nom, 1.0),
            ll, d, t, bt, be, biais))


def dispersion_des_facteurs(notes: list[dict[str, Any]]) -> None:
    print("\nEcart des facteurs a 1 (ce que l'onze change, en moyenne absolue) :")
    for nom in notes[0]["facteurs"]:
        v = [abs(f - 1) for n in notes for f in n["facteurs"][nom]]
        v.sort()
        print("  %-16s moyenne %.3f, mediane %.3f, 90e centile %.3f"
              % (nom, sum(v) / len(v), v[len(v) // 2], v[int(0.9 * len(v))]))


# --- Quels gestes font les corners -------------------------------------------

def _resoudre(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        if abs(m[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def poids_des_gestes(grandeur: str) -> None:
    """Regression lineaire du nombre de corners d'une equipe sur les gestes de
    ses joueurs pendant le MEME match. Descriptive : elle dit quels gestes
    s'accompagnent de corners, pas ce qu'on peut prevoir."""
    gestes = ("centres", "tirs_contres", "tirs", "dribbles_reussis", "touches_surface",
              "entrees_surface", "passes_dernier_tiers")
    x, y = [], []
    for m in store.stats_joueurs_archivees():
        for cote in ("domicile", "exterieur"):
            v = _valeur(m, cote, grandeur)
            if v is None or not m["joueurs"][cote]:
                continue
            tot = defaultdict(float)
            for j in m["joueurs"][cote]:
                for g in gestes:
                    tot[g] += float(j["stats"].get(g) or 0)
            x.append([1.0] + [tot[g] for g in gestes])
            y.append(float(v))
    if len(y) < 50:
        print("Trop peu de matchs (%d)." % len(y))
        return
    k = len(x[0])
    xtx = [[sum(r[i] * r[j] for r in x) for j in range(k)] for i in range(k)]
    xty = [sum(r[i] * t for r, t in zip(x, y)) for i in range(k)]
    beta = _resoudre(xtx, xty)
    pred = [sum(b * v for b, v in zip(beta, r)) for r in x]
    moy = sum(y) / len(y)
    r2 = 1 - sum((a - b) ** 2 for a, b in zip(y, pred)) / sum((a - moy) ** 2 for a in y)
    print("%s d'une equipe ~ gestes de ses joueurs, %d equipes-matchs, R2 = %.3f"
          % (grandeur, len(y), r2))
    print("  %-22s %8s %10s" % ("geste", "coef", "moyenne"))
    print("  %-22s %+8.3f" % ("constante", beta[0]))
    for i, g in enumerate(gestes, 1):
        print("  %-22s %+8.3f %10.1f" % (g, beta[i], sum(r[i] for r in x) / len(x)))
    # Correlation simple de chaque geste avec la grandeur.
    print("  correlations simples :")
    for i, g in enumerate(gestes, 1):
        xs = [r[i] for r in x]
        mx = sum(xs) / len(xs)
        cov = sum((a - mx) * (b - moy) for a, b in zip(xs, y))
        vx = sum((a - mx) ** 2 for a in xs)
        vy = sum((b - moy) ** 2 for b in y)
        print("    %-20s %+.3f" % (g, cov / math.sqrt(vx * vy) if vx and vy else 0.0))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grandeur", default="corners", choices=list(LOIS))
    parser.add_argument("--poids", action="store_true", help="quels gestes font la grandeur")
    args = parser.parse_args(argv)
    store.init()
    if args.poids:
        poids_des_gestes(args.grandeur)
        return 0

    print("Indice %s : %s" % (args.grandeur, INDICES[args.grandeur]))
    notes = rejouer(args.grandeur)
    if len(notes) < 100:
        print("Trop peu de matchs notes (%d)." % len(notes))
        return 1
    coupe = int(len(notes) * (1 - PART_TEST))
    reglage, test = notes[:coupe], notes[coupe:]
    dispersion_des_facteurs(notes)

    poids = {}
    for nom in notes[0]["facteurs"]:
        gains = {w: _gain(reglage, nom, w, args.grandeur) for w in POIDS_ESSAYES}
        meilleur = max(gains, key=gains.get)
        poids[nom] = meilleur if gains[meilleur] > 0 else 0.0
        print("  %-16s gain par poids (reglage) : %s -> %.2f" % (
            nom, "  ".join("%.2f:%+.4f" % kv for kv in gains.items()), poids[nom]))

    rapport(reglage, poids, args.grandeur, "reglage (60 % anciens)")
    rapport(test, poids, args.grandeur, "TEST (40 % recents)")
    rapport(test, {k: 1.0 for k in poids}, args.grandeur, "TEST, poids 1 partout")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
