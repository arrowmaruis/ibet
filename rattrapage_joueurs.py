"""Releve les statistiques par joueur des grands championnats.

C'est la matiere du catalogue des styles de jeu (`modeles/styles.py`) : pour
chaque match termine d'une saison, qui a joue combien de minutes a quel poste,
et ce qu'il a fait -- tirs, centres, dribbles, touches dans la surface,
degagements, contres. Les statistiques de l'EQUIPE (corners, tirs cadres)
sont archivees au passage dans `resultats` quand elles y manquent : ce sont
elles que les profils cherchent a expliquer.

Le script est REPRENABLE et INCREMENTAL : un match deja releve est saute. On
peut donc le lancer championnat par championnat, puis le relancer chaque
semaine pour ajouter la derniere journee.

Trois requetes par match (liste des joueurs, valeurs, stats d'equipe), puis une
pause. La reponse des valeurs pese pres d'un megaoctet : une saison de
Premier League (380 matchs) represente environ 400 Mo telecharges et une
vingtaine de minutes.

Usage :
    python rattrapage_joueurs.py                              # tout ce qui manque
    python rattrapage_joueurs.py --championnats france italie
    python rattrapage_joueurs.py --saisons 2025-2026          # une saison precise
    python rattrapage_joueurs.py --limite 50                  # s'arreter apres 50 matchs
    python rattrapage_joueurs.py --liste                      # championnats et saisons
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from dotenv import load_dotenv

import api_client
import store

REPOS = 1.0
FILS = 4
PAQUET = 20


def saison_en_cours(jour: date | None = None) -> str:
    """Saison europeenne qui contient le jour : "2026-2027" a partir de juillet."""
    jour = jour or date.today()
    debut = jour.year if jour.month >= 7 else jour.year - 1
    return "%d-%d" % (debut, debut + 1)


def saisons_par_defaut() -> list[str]:
    """La saison en cours et la precedente : assez pour qu'un joueur ait un
    profil, pas si loin que l'effectif ait tourne."""
    courante = saison_en_cours()
    debut = int(courante[:4])
    return ["%d-%d" % (debut - 1, debut), courante]


def a_relever(cles: list[str], saisons: list[str]) -> list[tuple[dict, str, str]]:
    """(match, championnat, saison) pas encore releves, les plus recents d'abord."""
    connus = store.matchs_joueurs_connus()
    courante = saison_en_cours()
    restants = []
    for cle in cles:
        nom = api_client.CHAMPIONNATS_STYLES[cle][2]
        for saison in saisons:
            try:
                matchs = api_client.matchs_de_saison(cle, "" if saison == courante else saison)
            except api_client.ApiError as exc:
                print("  %s %s : %s" % (nom, saison, exc), flush=True)
                continue
            neufs = [m for m in matchs if m["match_id"] not in connus]
            print("  %-15s %s : %3d matchs, %3d a relever" % (nom, saison, len(matchs), len(neufs)),
                  flush=True)
            restants.extend((m, nom, saison) for m in neufs)
    restants.sort(key=lambda r: r[0].get("date") or "", reverse=True)
    return restants


def main(argv: list[str]) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--championnats", nargs="*", default=list(api_client.CHAMPIONNATS_STYLES),
                        choices=list(api_client.CHAMPIONNATS_STYLES), metavar="CLE")
    parser.add_argument("--saisons", nargs="*", default=None, metavar="AAAA-AAAA")
    parser.add_argument("--limite", type=int, default=0)
    parser.add_argument("--liste", action="store_true", help="championnats suivis et etat du releve")
    args = parser.parse_args(argv)

    store.init()
    if args.liste:
        with store.connect() as cx:
            etat = {(r[0], r[1]): (r[2], r[3]) for r in cx.execute(
                "SELECT championnat, saison, COUNT(*), SUM(couvert) FROM matchs_joueurs "
                "GROUP BY championnat, saison")}
        for cle, (_, _, nom) in api_client.CHAMPIONNATS_STYLES.items():
            lignes = [(s, n, c) for (ch, s), (n, c) in sorted(etat.items()) if ch == nom]
            detail = ", ".join("%s : %d matchs (%d couverts)" % l for l in lignes) or "rien"
            print("  %-10s %-15s %s" % (cle, nom, detail))
        return 0

    saisons = args.saisons or saisons_par_defaut()
    print("Recensement des matchs (%s)..." % ", ".join(saisons), flush=True)
    restants = a_relever(args.championnats, saisons)
    if args.limite > 0:
        restants = restants[: args.limite]
    print("%d match(s) a relever." % len(restants), flush=True)
    if not restants:
        return 0

    joueurs_ecrits = couverts = non_couverts = stats_ajoutees = 0
    debut = time.time()
    lot: list[tuple[dict, str, str, dict, dict | None]] = []

    def vider() -> None:
        nonlocal joueurs_ecrits, stats_ajoutees
        if not lot:
            return
        with store.connect() as cx:
            for match, nom, saison, joueurs, stats in lot:
                if stats:
                    store.archiver_resultat(
                        dict(match, statut=api_client.FINISHED), stats, connexion=cx)
                    stats_ajoutees += 1
                joueurs_ecrits += store.archiver_stats_joueurs(match, joueurs, nom, saison, cx)
        lot.clear()

    def chercher(item: tuple[dict, str, str]) -> tuple[dict, str, str, dict, dict | None]:
        match, nom, saison = item
        try:
            joueurs = api_client.stats_joueurs(match["match_id"])
        except api_client.ApiError:
            joueurs = {}
        stats = None
        archive = store.resultat(match["match_id"])
        if not archive or (archive.get("stats") or {}).get("domicile", {}).get("corners") is None:
            try:
                stats = api_client.get_stats(
                    {"provider": "flashscore", "match_id": match["match_id"]})
            except api_client.ApiError:
                stats = None
        time.sleep(REPOS)
        return match, nom, saison, joueurs, stats

    with ThreadPoolExecutor(max_workers=FILS) as pool:
        for rang, item in enumerate(pool.map(chercher, restants), 1):
            lot.append(item)
            if item[3]:
                couverts += 1
            else:
                non_couverts += 1
            if len(lot) >= PAQUET:
                vider()
            if rang % 50 == 0:
                ecoule = time.time() - debut
                print("%d/%d  couverts %d, non couverts %d  -- %.0f min restantes"
                      % (rang, len(restants), couverts, non_couverts,
                         ecoule / rang * (len(restants) - rang) / 60), flush=True)
    vider()
    print("Termine : %d match(s) couverts (%d lignes joueur), %d non couverts ; "
          "%d statistiques d'equipe ajoutees a l'archive."
          % (couverts, joueurs_ecrits, non_couverts, stats_ajoutees), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
