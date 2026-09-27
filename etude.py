"""Etude des versions : ce que chaque version de chaque modele a reellement donne.

Chaque fiche emise porte les versions des modeles qui l'ont produite
(`versions` dans la fiche, voir `modeles.versions`). Chaque proposition verifiee
est rattachee au modele qui l'a calculee :

  - les familles de l'issue (1X2, double chance, les deux marquent) au modele
    de l'issue, meme si elles sont affichees dans la fiche des buts ;
  - toutes les autres au modele de leur grandeur.

Puis elles sont regroupees par `modele@version`, avec la meme mesure que le
bilan des options (`verify._mesure_groupee`) : annonce, observe, ecart et
erreur type GROUPEE PAR MATCH, plus le score de Brier.

Les fiches emises avant le versionnage n'ont pas de version : elles sont
regroupees sous « anterieure ». Elles ne sont pas la version 1.0.0 -- plusieurs
reglages ont change depuis leur emission -- et servent de point de depart.

Lire un tableau de versions demande deux precautions :

  - deux versions ne se comparent que sur un nombre de MATCHS suffisant ; sous
    une trentaine, l'erreur type depasse le plus souvent l'ecart entre elles ;
  - deux versions n'ont pas vu les memes matchs. Pour une comparaison appariee
    (les deux versions sur les memes rencontres), c'est le banc d'essai qu'il
    faut employer : `python main.py --backtest`.

Usage :
    python etude.py                    # toutes les versions de tous les modeles
    python etude.py --modele corners   # un seul modele
    python etude.py --familles         # detail par famille de proposition
    python etude.py --moteur           # regroupe par version du moteur commun
    python etude.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import modeles
import store
import verify

ANTERIEURE = "anterieure"

# Familles calculees par le modele de l'issue, bien qu'affichees sous les buts.
FAMILLES_ISSUE = ("issue", "double chance", "les deux marquent")

# Libelle de grandeur -> cle du modele. Les fiches enregistrent le libelle.
_CLE_PAR_LIBELLE = {m.libelle: m.cle for m in modeles.MODELES}


def modele_de(grandeur: str, famille: str) -> str:
    """Le modele qui a calcule une proposition."""
    if famille in FAMILLES_ISSUE:
        return modeles.ISSUE.cle
    return _CLE_PAR_LIBELLE.get(grandeur, grandeur)


def _lignes(limite: int) -> list[dict[str, Any]]:
    with store.connect() as connection:
        rangs = connection.execute(
            """
            SELECT p.match_id, p.emis_le, o.grandeur, o.pari, o.probabilite,
                   o.verifie, json_extract(p.payload, '$.versions') AS versions
              FROM offres o
              JOIN predictions p ON p.id = o.prediction_id
             WHERE o.verifie IS NOT NULL
             LIMIT ?
            """,
            (limite,),
        ).fetchall()
    lignes = []
    for rang in rangs:
        versions = json.loads(rang["versions"]) if rang["versions"] else {}
        famille = verify.famille_de(rang["pari"])
        cle = modele_de(rang["grandeur"], famille)
        lignes.append({
            "match_id": rang["match_id"],
            "emis_le": rang["emis_le"],
            "modele": cle,
            "version": versions.get(cle, ANTERIEURE),
            "moteur": versions.get("moteur", ANTERIEURE),
            "famille": famille,
            "p": float(rang["probabilite"]),
            "reussi": bool(rang["verifie"]),
        })
    return lignes


def _mesure(lignes: list[dict[str, Any]]) -> dict[str, Any] | None:
    mesure = verify._mesure_groupee(
        [(l["match_id"], l["p"], l["reussi"]) for l in lignes]
    )
    if not mesure:
        return None
    mesure["brier"] = sum(
        (l["p"] - (1.0 if l["reussi"] else 0.0)) ** 2 for l in lignes
    ) / len(lignes)
    dates = sorted(l["emis_le"][:10] for l in lignes if l["emis_le"])
    mesure["premiere_fiche"] = dates[0] if dates else ""
    mesure["derniere_fiche"] = dates[-1] if dates else ""
    return mesure


def bilan(
    limite: int = 20000,
    modele: str | None = None,
    par_moteur: bool = False,
    par_famille: bool = False,
) -> list[dict[str, Any]]:
    """Mesure par `modele@version` (ou par version du moteur)."""
    groupes: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for ligne in _lignes(limite):
        if modele and ligne["modele"] != modele:
            continue
        if par_moteur:
            cle: tuple[str, ...] = ("moteur", ligne["moteur"])
        else:
            cle = (ligne["modele"], ligne["version"])
        if par_famille:
            cle = cle + (ligne["famille"],)
        groupes.setdefault(cle, []).append(ligne)

    rendu = []
    for cle, lignes in groupes.items():
        mesure = _mesure(lignes)
        if mesure:
            rendu.append(dict(mesure, modele=cle[0], version=cle[1],
                              famille=cle[2] if par_famille else None))
    ordre = {m.cle: i for i, m in enumerate(modeles.MODELES)}
    ordre[modeles.ISSUE.cle] = 0.5
    rendu.sort(key=lambda r: (
        ordre.get(r["modele"], 99),
        # « anterieure » d'abord, puis les versions dans l'ordre.
        (0,) if r["version"] == ANTERIEURE
        else (1,) + tuple(int(x) for x in r["version"].split(".")),
        r["famille"] or "",
    ))
    return rendu


def render(lignes: list[dict[str, Any]]) -> str:
    if not lignes:
        return "Aucune proposition verifiee."
    avec_famille = any(l["famille"] for l in lignes)
    tete = "%-15s %-11s %s%6s %6s %8s %8s %9s %8s %7s" % (
        "Modele", "Version", "%-18s " % "Famille" if avec_famille else "",
        "Props", "Matchs", "Annonce", "Observe", "Ecart", "+/-", "Brier",
    )
    out = [tete, "-" * len(tete)]
    for l in lignes:
        erreur = "%.1f" % (100 * l["erreur_type"]) if l["erreur_type"] else "-"
        out.append("%-15s %-11s %s%6d %6d %7.1f%% %7.1f%% %+8.1f %8s %7.4f%s" % (
            l["modele"], l["version"],
            "%-18s " % (l["famille"] or "")[:18] if avec_famille else "",
            l["propositions"], l["matchs"], 100 * l["annonce"],
            100 * l["observe"], 100 * l["ecart"], erreur, l["brier"],
            "  *" if l["significatif"] else "",
        ))
    out.append("")
    out.append("Ecart = annonce - observe, en points : positif, la version promet "
               "plus qu'elle ne tient.")
    out.append("* : ecart superieur a deux erreurs types (groupees par match).")
    out.append("Brier : plus bas est meilleur, a comparer sur des matchs comparables.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare ce que chaque version de chaque modele a donne."
    )
    parser.add_argument("--modele", help="Restreint a un modele (buts, issue, corners...)")
    parser.add_argument("--familles", action="store_true",
                        help="Detail par famille de proposition")
    parser.add_argument("--moteur", action="store_true",
                        help="Regroupe par version du moteur commun")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    args = parser.parse_args(argv)

    lignes = bilan(modele=args.modele, par_moteur=args.moteur,
                   par_famille=args.familles)
    if args.json:
        print(json.dumps(lignes, ensure_ascii=False, indent=2))
    else:
        print("Versions en service : %s\n" % ", ".join(
            "%s %s" % item for item in modeles.versions().items()))
        print(render(lignes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
