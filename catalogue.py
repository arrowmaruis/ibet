"""Catalogue des styles de jeu : les joueurs de chaque equipe, et ce qu'ils font.

Lit les statistiques par joueur archivees (`rattrapage_joueurs.py`) et affiche,
par equipe, chaque joueur avec ses taux par 90 minutes -- tirs, tirs cadres,
centres, dribbles, touches dans la surface, passes cles -- et ses etiquettes
de style (« centreur », « tireur »...), donnees quand un taux est dans le
cinquieme superieur de son poste, sur au moins 450 minutes.

Les taux sont ponderes par l'anciennete (demi-vie 240 jours) et lisses vers le
taux du poste : un joueur vu deux fois ressemble encore beaucoup a un joueur
moyen de son poste. Un joueur est range dans le dernier club ou on l'a vu.

Usage :
    python catalogue.py --equipe Arsenal
    python catalogue.py --championnat france            # toutes les equipes
    python catalogue.py --championnat angleterre --style centreur
    python catalogue.py --championnat espagne --export  # exports/styles_espagne.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date as _date
from pathlib import Path

import api_client
import store
from modeles.styles import ETIQUETTES, INDICES, MINUTES_TITULAIRE, Styles

COLONNES = (
    ("tirs", "Tirs"), ("tirs_cadres", "Cadr"), ("centres", "Centr"),
    ("dribbles", "Dribl"), ("touches_surface", "Surf"), ("passes_cles", "PCle"),
    ("degagements", "Degag"),
)


def charger(championnat: str = "") -> Styles:
    return Styles().alimenter(store.stats_joueurs_archivees(championnat))


def _equipes_du_championnat(cle: str) -> list[str]:
    nom = api_client.CHAMPIONNATS_STYLES[cle][2]
    with store.connect() as cx:
        return sorted({r[0] for r in cx.execute(
            "SELECT domicile FROM matchs_joueurs WHERE championnat = ? AND couvert = 1 "
            "AND date >= ?", (nom, _saison_debut()))})


def _saison_debut() -> str:
    """Premier jour de la saison precedente : les equipes qu'on affiche sont
    celles des deux dernieres saisons, pas un promu d'il y a trois ans."""
    j = _date.today()
    debut = j.year - 1 if j.month >= 7 else j.year - 2
    return "%d-07-01" % debut


def afficher_equipe(index: Styles, equipe: str, date: str, seuils, style: str = "",
                    minutes_min: float = 90.0) -> list[dict]:
    profils = [p for p in index.effectif(equipe, date) if p["minutes"] >= minutes_min]
    lignes = []
    for p in profils:
        etiquettes = index.etiquettes(p, seuils)
        if style and style not in etiquettes:
            continue
        lignes.append(dict(p, etiquettes=etiquettes))
    if not lignes:
        return []
    corners = sum(sorted(
        (sum(c * p["par_90"][g] for g, c in INDICES["corners"].items()) for p in lignes),
        reverse=True)[:11]) * MINUTES_TITULAIRE / 90.0
    print("\n%s  -- %d joueurs, indice corners des onze plus actifs : %.1f"
          % (equipe, len(lignes), corners))
    print("  %-22s %-2s %5s  %s  %s" % (
        "joueur", "P", "min", "  ".join("%5s" % c for _, c in COLONNES), "style"))
    for p in lignes:
        print("  %-22s %-2s %5.0f  %s  %s" % (
            p["nom"][:22], p["poste"], p["minutes"],
            "  ".join("%5.2f" % p["par_90"][g] for g, _ in COLONNES),
            ", ".join(p["etiquettes"])))
    return lignes


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--equipe", default="", help="nom ou partie du nom")
    parser.add_argument("--championnat", default="", choices=[""] + list(api_client.CHAMPIONNATS_STYLES))
    parser.add_argument("--style", default="", choices=[""] + [e[0] for e in ETIQUETTES])
    parser.add_argument("--minutes", type=float, default=90.0, help="minutes minimales (defaut 90)")
    parser.add_argument("--export", action="store_true", help="ecrit exports/styles_<championnat>.csv")
    args = parser.parse_args(argv)
    if not args.equipe and not args.championnat:
        parser.error("indiquer --equipe ou --championnat")

    store.init()
    date = (_date.today()).isoformat() + "T"  # apres tous les matchs du jour
    index = charger()
    seuils = index.seuils_etiquettes(date)

    if args.championnat:
        equipes = _equipes_du_championnat(args.championnat)
    else:
        cherche = args.equipe.lower()
        equipes = [e for e in index.equipes() if cherche in e.lower()]
    if not equipes:
        print("Aucune equipe trouvee. Le releve est-il fait ? python rattrapage_joueurs.py --liste")
        return 1

    toutes = []
    for equipe in equipes:
        toutes.extend(afficher_equipe(index, equipe, date, seuils, args.style, args.minutes))

    if args.export and toutes:
        Path("exports").mkdir(exist_ok=True)
        chemin = Path("exports") / ("styles_%s.csv" % (args.championnat or "equipes"))
        with chemin.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            grandeurs = list(toutes[0]["par_90"])
            w.writerow(["equipe", "joueur", "poste", "minutes", "matchs", "styles"]
                       + ["%s_90" % g for g in grandeurs])
            for p in toutes:
                w.writerow([p["equipe"], p["nom"], p["poste"], round(p["minutes"]), p["matchs"],
                            ", ".join(p["etiquettes"])]
                           + [("%.3f" % p["par_90"][g]).replace(".", ",") for g in grandeurs])
        print("\nExporte : %s (%d joueurs)" % (chemin, len(toutes)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
