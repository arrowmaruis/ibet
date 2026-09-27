"""Rattrape la feuille de match des resultats archives.

Le modele des cartons 2.0.0 a besoin de trois choses que l'archive ignorait :
l'ARBITRE de chaque match (le champ existait, toujours vide), les cartons
JOUEUR PAR JOUEUR, et qui a joue combien de minutes sous quel entraineur. Le
flux d'un match termine les donne toutes, en deux requetes
(`api_client.feuille_de_match`).

Ce script les verse dans la table `feuilles` pour chaque resultat archive qui
porte des cartons. Il est REPRENABLE : un match deja rattrape est saute, donc
une interruption ne coute que le match en cours. Les plus recents passent
d'abord -- ce sont eux qui ressemblent le plus aux matchs a prevoir.

La date et la competition viennent de l'historique des equipes en cache (le
seul endroit ou l'archive les retrouve) : sans elles, une feuille ne peut pas
etre rejouee avec coupure temporelle.

Usage :
    python -m ibet rattraper-feuilles              # tout ce qui manque
    python -m ibet rattraper-feuilles --limite 50  # s'arreter apres cinquante matchs
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

from ibet.sources import api_client
from ibet.stockage import store

# Deux requetes par match, puis une pause, sur quatre fils au plus : moins
# qu'un navigateur qui charge une page du site. A un seul fil, 4 250 matchs
# demandaient pres de quatre heures.
REPOS = 1.0
FILS = 4

# Ecritures groupees : une transaction par paquet, pas par match.
PAQUET = 25


def index_des_historiques(dossier: Path) -> dict[str, dict[str, str]]:
    """Date, competition et equipes de chaque match vu dans un historique en cache."""
    index: dict[str, dict[str, str]] = {}
    for fichier in dossier.glob("*.json"):
        try:
            paquet = json.loads(fichier.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError):
            continue
        if "|hist|" not in str(paquet.get("key", "")):
            continue
        for bord in paquet.get("value") or []:
            if not isinstance(bord, dict):
                continue
            equipe = (bord.get("equipe") or "").strip()
            for m in bord.get("matchs") or []:
                if not isinstance(m, dict) or not m.get("match_id"):
                    continue
                chez_soi = m.get("lieu") == "domicile"
                adverse = (m.get("adversaire") or "").strip()
                index.setdefault(m["match_id"], {
                    "date": m.get("date") or "",
                    "competition": m.get("competition") or "",
                    "domicile": equipe if chez_soi else adverse,
                    "exterieur": adverse if chez_soi else equipe,
                })
    return index


def a_rattraper(index: dict[str, dict[str, str]]) -> list[tuple[str, dict[str, str]]]:
    """Resultats archives avec des cartons et sans feuille, les plus recents d'abord."""
    connues = store.feuilles_connues()
    restants = []
    with store.connect() as connection:
        for ligne in connection.execute("SELECT match_id, libelle, stats FROM resultats"):
            if ligne["match_id"] in connues:
                continue
            stats = json.loads(ligne["stats"] or "{}")
            if (stats.get("domicile") or {}).get("cartons_jaunes") is None:
                continue
            meta = dict(index.get(ligne["match_id"]) or {})
            if not meta.get("domicile"):
                dom, _, ext = (ligne["libelle"] or "").partition(" - ")
                meta.update(domicile=dom.strip(), exterieur=ext.strip())
            restants.append((ligne["match_id"], meta))
    restants.sort(key=lambda r: r[1].get("date") or "", reverse=True)
    return restants


def main(argv: list[str]) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limite", type=int, default=0,
                        help="nombre maximum de matchs a traiter")
    args = parser.parse_args(argv)

    store.init()
    index = index_des_historiques(api_client.cache.CACHE_DIR)
    restants = a_rattraper(index)
    if args.limite > 0:
        restants = restants[: args.limite]
    print("%d match(s) a rattraper." % len(restants), flush=True)

    ecrits = vides = avec_arbitre = 0
    debut = time.time()
    lot: list[tuple[str, dict, dict[str, str]]] = []

    def vider() -> None:
        if not lot:
            return
        with store.connect() as connection:
            for match_id, feuille, meta in lot:
                store.archiver_feuille(
                    match_id, feuille, meta.get("date", ""),
                    meta.get("competition", ""), meta.get("domicile", ""),
                    meta.get("exterieur", ""), connexion=connection,
                )
        lot.clear()

    def chercher(item: tuple[str, dict[str, str]]) -> tuple[str, dict, dict[str, str]]:
        match_id, meta = item
        feuille = api_client.feuille_de_match(match_id)
        time.sleep(REPOS)
        return match_id, feuille, meta

    with ThreadPoolExecutor(max_workers=FILS) as pool:
        # Les ecritures restent dans ce fil : SQLite n'aime pas les ecrivains
        # concurrents, et `map` rend les resultats dans l'ordre.
        for rang, (match_id, feuille, meta) in enumerate(pool.map(chercher, restants), 1):
            if feuille:
                lot.append((match_id, feuille, meta))
                ecrits += 1
                avec_arbitre += 1 if feuille.get("arbitre") else 0
            else:
                vides += 1
            if len(lot) >= PAQUET:
                vider()
            if rang % 100 == 0:
                ecoule = time.time() - debut
                print(
                    "%d/%d  feuilles %d (arbitre %d), vides %d  -- %.0f min restantes"
                    % (rang, len(restants), ecrits, avec_arbitre, vides,
                       ecoule / rang * (len(restants) - rang) / 60),
                    flush=True,
                )
    vider()
    print("Termine : %d feuille(s), dont %d avec arbitre ; %d flux vide(s)."
          % (ecrits, avec_arbitre, vides), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
