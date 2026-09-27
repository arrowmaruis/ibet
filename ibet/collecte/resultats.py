"""Retrouve le resultat des fiches que la source ne publie plus.

La source ne rend une journee que sept jours autour d'aujourd'hui. Passe ce
delai, une fiche non verifiee ne se tranche plus : elle ne compte ni en
reussite ni en echec, et le peu de mesures dont dispose le projet s'appauvrit
d'autant. Onze fiches -- 264 propositions -- ont ete perdues ainsi.

L'archive de `store` empeche que cela se reproduise, mais elle ne peut rien
pour ce qui n'a jamais ete telecharge. Ce script est la voie de retour, et il
tient a une particularite du flux : l'HISTORIQUE d'une equipe, lui, remonte
des mois. Un match du 13 septembre n'est plus dans la journee du 13 septembre,
mais il est toujours dans la forme recente de ses deux equipes.

Une subtilite rend la chose moins directe qu'il n'y parait : le flux exclut de
la forme le match qu'on consulte. Demander l'historique du match perdu ne le
rend donc pas -- il faut passer par un match VOISIN de l'une de ses equipes,
que ce meme historique vient justement de nommer. D'ou les deux passes.

Usage :
    python -m ibet rattraper            # les fiches en attente sans resultat
    python -m ibet rattraper --limite 5 # s'arreter apres cinq fiches
"""

from __future__ import annotations

import argparse
import sys
import time

from dotenv import load_dotenv

from ibet.sources import api_client
from ibet.stockage import store

# La source n'est pas une API publiee : on espace. Une seconde entre deux
# appels reste tres en dessous de ce qu'un navigateur genere en consultant
# normalement le site.
REPOS = 1.0

# Garde-fou : une erreur de boucle ne doit pas se traduire en centaines de
# requetes. Le rattrapage porte sur quelques dizaines de fiches au plus.
APPELS_MAX = 120


def _fiches_sans_resultat() -> list[dict]:
    """Fiches en attente dont l'archive ignore encore le resultat."""
    restantes = []
    for fiche in store.pending():
        identifiant = fiche.get("match_id") or store.match_id_of(fiche)
        if not identifiant:
            continue
        garde = store.resultat(identifiant)
        if garde and garde.get("score_domicile") is not None:
            continue
        fiche["match_id"] = identifiant
        restantes.append(fiche)
    return restantes


def main(argv: list[str]) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limite", type=int, default=0,
                        help="nombre maximum de fiches a traiter")
    parser.add_argument("--tz", default="Europe/Paris")
    args = parser.parse_args(argv)

    fiches = _fiches_sans_resultat()
    if args.limite > 0:
        fiches = fiches[: args.limite]
    if not fiches:
        print("Aucune fiche en attente sans resultat : rien a rattraper.")
        return 0

    print("%d fiche(s) a rattraper.\n" % len(fiches))
    appels = 0
    voisins: dict[str, list[str]] = {}

    def historique(match_id: str) -> list[dict]:
        """Un historique, archive au passage. Rend les bords tels quels."""
        nonlocal appels
        if appels >= APPELS_MAX:
            raise RuntimeError("plafond de %d appels atteint" % APPELS_MAX)
        appels += 1
        time.sleep(REPOS)
        sides = api_client._fs_history(match_id, args.tz, use_cache=False)
        ecrits = store.archiver_historique(sides)
        if ecrits:
            print("      (+%d matchs archives au passage)" % ecrits)
        return sides

    # Passe 1 : l'historique du match perdu. Il ne le contient pas lui-meme,
    # mais il nomme les matchs voisins de ses deux equipes -- notre porte.
    for fiche in fiches:
        identifiant = fiche["match_id"]
        print("  %-38s" % fiche.get("match", identifiant)[:38])
        try:
            sides = historique(identifiant)
        except (api_client.ApiError, RuntimeError) as exc:
            print("      source indisponible : %s" % str(exc)[:80])
            continue
        proches = []
        for bord in sides:
            for entree in bord.get("matchs") or []:
                autre = (entree.get("match_id") or "").strip()
                if autre and autre != identifiant:
                    proches.append(autre)
        voisins[identifiant] = proches

    # Passe 2 : pour ce qui manque encore, l'historique d'un voisin. On essaie
    # les plus recents d'abord : ce sont eux qui encadrent le match perdu.
    restantes = _fiches_sans_resultat()
    restantes = [f for f in restantes if f["match_id"] in voisins]
    for fiche in restantes:
        identifiant = fiche["match_id"]
        print("  %-38s (par un voisin)" % fiche.get("match", identifiant)[:38])
        for autre in voisins[identifiant][:6]:
            try:
                historique(autre)
            except (api_client.ApiError, RuntimeError) as exc:
                print("      %s : %s" % (autre, str(exc)[:60]))
                continue
            garde = store.resultat(identifiant)
            if garde and garde.get("score_domicile") is not None:
                print("      trouve : %s - %s"
                      % (garde["score_domicile"], garde["score_exterieur"]))
                break
        else:
            print("      introuvable")

    # On compte sur les fiches VISEES, pas sur ce qui reste en attente : avec
    # `--limite`, les deux ne se recouvrent pas.
    rattrapees = sum(
        1
        for f in fiches
        if (store.resultat(f["match_id"]) or {}).get("score_domicile") is not None
    )
    print("\n%d appel(s) reseau." % appels)
    print("Fiches rattrapees : %d / %d" % (rattrapees, len(fiches)))
    print("Resultats archives au total : %d" % store.resultats_archives())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
