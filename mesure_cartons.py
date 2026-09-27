"""Mesure, sur les feuilles archivees, ce qu'apportent arbitre, joueurs et entraineur.

Rejoue les matchs dans l'ordre chronologique. Pour chacun, l'index de
`modeles.discipline` ne connait que les matchs anterieurs ; on prevoit, on note,
puis on ajoute le match a l'index. Aucune information posterieure ne fuit.

Reference : l'attendu par equipe de l'index (Maher reduit aux cartons), sous la
loi du modele 1.0.0 -- binomiale de dispersion 0.847, correlation +0.083. Les
variantes le multiplient :

    arbitre       rapport lisse de l'arbitre designe, les deux cotes ;
    joueurs       onze aligne contre onze habituel de l'equipe ;
    entraineur    carriere de l'entraineur, quand il vient d'arriver ;
    et toutes leurs combinaisons avec le poids retenu.

Notes, toutes appariees par match (erreur type groupee par match) :
    log-vraisemblance du couple de cartons observe (plus haut = mieux) ;
    Brier des seuils du total (2.5 a 5.5) et par equipe (0.5 a 3.5) ;
    calibration des seuils du total : annonce contre observe.

Les reglages sont choisis sur les 60 % les plus anciens des matchs et jugés sur
les 40 % les plus recents : un gain qui n'existe que la ou on l'a regle n'en est
pas un.

Usage :
    python mesure_cartons.py
"""

from __future__ import annotations

import math
import sys
from collections import defaultdict
from typing import Any, Callable

import store
from modeles.cartons import CORRELATION, DISPERSION
from modeles.discipline import Discipline, Reglage, cle_arbitre, jaunes_du_cote
from modeles.lois import count_pmf, dispersion_du_total, team_over_probability, total_over_probability

SEUILS_TOTAL = (2.5, 3.5, 4.5, 5.5)
SEUILS_EQUIPE = (0.5, 1.5, 2.5, 3.5)
MIN_MATCHS = 3          # historique minimal de chaque equipe pour etre note
HABITUEL = 5            # onzes precedents qui definissent l'onze habituel
PART_TEST = 0.40
INSTALLATION = 8     # matchs pour qu'un entraineur soit dans l'historique


def _loglik(yd: int, ye: int, ld: float, le: float, phi: float) -> float:
    p = count_pmf(yd, ld, phi) * count_pmf(ye, le, phi)
    return math.log(max(p, 1e-12))


def _loglik_total(t: int, ld: float, le: float, phi: float, corr: float) -> float:
    phi_t = dispersion_du_total(ld, le, phi, corr)
    return math.log(max(count_pmf(t, ld + le, phi_t), 1e-12))


def _noter(pred: dict[str, float], yd: int, ye: int) -> dict[str, Any]:
    ld, le, phi, corr = pred["ld"], pred["le"], pred["phi"], pred["corr"]
    phi_t = dispersion_du_total(ld, le, phi, corr)
    t = yd + ye
    total = [(total_over_probability(ld, le, s, phi_t), t > s) for s in SEUILS_TOTAL]
    equipe = [(team_over_probability(l, s, phi), y > s)
              for l, y in ((ld, yd), (le, ye)) for s in SEUILS_EQUIPE]
    return {
        "ll": _loglik(yd, ye, ld, le, phi),
        "ll_total": _loglik_total(t, ld, le, phi, corr),
        "brier_total": sum((p - o) ** 2 for p, o in total) / len(total),
        "brier_equipe": sum((p - o) ** 2 for p, o in equipe) / len(equipe),
        "total": total,
        "erreur": ld + le - t,
    }


def rejouer(reglage: Reglage, poids: dict[str, float]) -> list[dict[str, Any]]:
    """Rend, match par match, la note de chaque variante."""
    index = Discipline(reglage)
    rows = [r for r in store.feuilles() if r.get("date")]
    rows.sort(key=lambda r: (r["date"], r["match_id"]))
    notes: list[dict[str, Any]] = []

    for row in rows:
        yd, ye = jaunes_du_cote(row, "domicile"), jaunes_du_cote(row, "exterieur")
        date, comp = row["date"], row.get("competition") or ""
        dom, ext = row.get("domicile") or "", row.get("exterieur") or ""
        feuille = row.get("feuille") or {}
        if yd is None or ye is None:
            continue

        _, _, nd = index.equipe(dom, date)
        _, _, ne = index.equipe(ext, date)
        if nd >= MIN_MATCHS and ne >= MIN_MATCHS:
            ld0, le0 = index.attendu_equipes(comp, dom, ext, date)
            arb = index.arbitre(cle_arbitre(row.get("arbitre") or "",
                                            feuille.get("arbitre_pays") or ""), date)
            facteurs: dict[str, tuple[float, float]] = {"arbitre": (arb["rapport"],) * 2}

            fj = []
            onzes_connus = 0
            for cote, equipe in (("domicile", dom), ("exterieur", ext)):
                bloc = feuille.get(cote) or {}
                titulaires = [j for j in bloc.get("joueurs") or [] if j.get("titulaire")]
                f = index.facteur_joueurs(equipe, titulaires, bloc.get("systeme") or "", date, HABITUEL)
                onzes_connus += 1 if "attendu_onze" in f else 0
                fj.append(f["facteur"])
            facteurs["joueurs"] = (fj[0], fj[1])

            fc = []
            for cote, equipe in (("domicile", dom), ("exterieur", ext)):
                coach = ((feuille.get(cote) or {}).get("entraineur") or {}).get("id") or ""
                fc.append(index.facteur_entraineur(equipe, coach, date, INSTALLATION)["facteur"])
            facteurs["entraineur"] = (fc[0], fc[1])

            base = {"ld": ld0, "le": le0, "phi": DISPERSION, "corr": CORRELATION}
            variantes: dict[str, dict[str, float]] = {"reference": base}
            for nom, (fd, fe) in facteurs.items():
                w = poids.get(nom, 1.0)
                variantes[nom] = dict(base, ld=ld0 * fd ** w, le=le0 * fe ** w)
            # Arbitre inconnu ou mal connu : sa variance elargit le total.
            v_arb = arb["variance"] if arb["matchs"] else 1.0 / reglage.lissage_arbitre
            ld_a, le_a = variantes["arbitre"]["ld"], variantes["arbitre"]["le"]
            corr_a = CORRELATION + poids.get("variance_arbitre", 0.0) * v_arb * math.sqrt(ld_a * le_a) / DISPERSION
            variantes["arbitre+variance"] = dict(variantes["arbitre"], corr=corr_a)
            ld_all, le_all = ld0, le0
            for nom in ("arbitre", "joueurs", "entraineur"):
                w = poids.get(nom, 1.0)
                ld_all *= facteurs[nom][0] ** w
                le_all *= facteurs[nom][1] ** w
            variantes["tout"] = dict(base, ld=ld_all, le=le_all, corr=corr_a)

            notes.append({
                "date": date, "match_id": row["match_id"],
                "arbitre_connu": arb["matchs"],
                "onze": onzes_connus == 2,
                "changement_coach": any(f != 1.0 for f in fc),
                "variantes": {k: _noter(v, yd, ye) for k, v in variantes.items()},
            })

        index.ajouter(row)
    return notes


def _moyenne_et_erreur(valeurs: list[float]) -> tuple[float, float]:
    n = len(valeurs)
    if n < 2:
        return (valeurs[0] if valeurs else 0.0), float("nan")
    m = sum(valeurs) / n
    v = sum((x - m) ** 2 for x in valeurs) / (n - 1)
    return m, math.sqrt(v / n)


def rapport(notes: list[dict[str, Any]], filtre: Callable[[dict], bool] = lambda n: True,
            titre: str = "") -> None:
    choisies = [n for n in notes if filtre(n)]
    if not choisies:
        return
    print("\n== %s : %d matchs" % (titre, len(choisies)))
    print("%-18s %9s %9s %9s %10s %10s %8s" % (
        "variante", "ll", "d ll", "t", "brier tot", "brier eq", "biais"))
    ref = [n["variantes"]["reference"] for n in choisies]
    for nom in choisies[0]["variantes"]:
        v = [n["variantes"][nom] for n in choisies]
        ll, _ = _moyenne_et_erreur([x["ll_total"] + x["ll"] for x in v])
        d, e = _moyenne_et_erreur([x["ll_total"] + x["ll"] - r["ll_total"] - r["ll"]
                                   for x, r in zip(v, ref)])
        bt, _ = _moyenne_et_erreur([x["brier_total"] for x in v])
        be, _ = _moyenne_et_erreur([x["brier_equipe"] for x in v])
        biais, _ = _moyenne_et_erreur([x["erreur"] for x in v])
        t = d / e if nom != "reference" and e == e and e > 1e-9 else 0.0
        print("%-18s %9.4f %+9.4f %+9.1f %10.4f %10.4f %+8.2f" % (nom, ll, d, t, bt, be, biais))
    # Calibration des seuils du total, par tranche annoncee.
    for nom in ("reference", "arbitre+variance", "tout"):
        if nom not in choisies[0]["variantes"]:
            continue
        paires = [p for n in choisies for p in n["variantes"][nom]["total"]
                  if max(p[0], 1 - p[0]) >= 0.70]
        if paires:
            annonce = sum(max(p, 1 - p) for p, _ in paires) / len(paires)
            observe = sum((o if p >= 0.5 else not o) for p, o in paires) / len(paires)
            print("  calibration total >=70 %% [%s] : annonce %.1f %%, observe %.1f %% (%d)"
                  % (nom, 100 * annonce, 100 * observe, len(paires)))


def main(argv: list[str]) -> int:
    store.init()
    poids = {"arbitre": 1.0, "joueurs": 1.0, "entraineur": 1.0, "variance_arbitre": 1.0}
    reglage = Reglage()
    for arg in argv:
        cle, _, valeur = arg.partition("=")
        if cle in poids:
            poids[cle] = float(valeur)
        elif cle in Reglage._fields:
            reglage = reglage._replace(**{cle: float(valeur)})
    notes = rejouer(reglage, poids)
    coupe = int(len(notes) * (1 - PART_TEST))
    print("Reglage %s, poids %s" % (dict(reglage._asdict()), poids))
    rapport(notes[:coupe], titre="reglage (60 % anciens)")
    rapport(notes[coupe:], titre="TEST (40 % recents)")
    rapport(notes[coupe:], lambda n: n["arbitre_connu"] >= 3, "TEST, arbitre vu >= 3 fois")
    rapport(notes[coupe:], lambda n: n["onze"], "TEST, onzes connus")
    rapport(notes[coupe:], lambda n: n["changement_coach"], "TEST, changement d'entraineur")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
