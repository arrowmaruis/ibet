"""Emission des previsions : du modele a la fiche enregistree.

`predict.build` rend le resultat du modele ; ce module en fait une **fiche**,
c'est-a-dire ce qui est publie et conserve : qui joue, quand, avec quels
reglages, et quelles propositions sont engagees. La fiche est ensuite ecrite
dans la base (`store`), d'ou l'interface la relit sans jamais reinterroger la
source.

La distinction n'est pas cosmetique. Le resultat du modele peut changer d'un
jour a l'autre -- l'historique s'allonge, la reference du championnat bouge. La
fiche, elle, est datee et figee : c'est ce qui permet de la confronter au
resultat reel sans qu'elle ait pu etre reecrite entre-temps.

    python -m ibet emettre --date 2026-09-07 --league "Premier League"
    python -m ibet emettre --jours 3 --max 12

Cout reseau : une prevision demande l'historique detaille des deux equipes (une
requete par match d'historique) et la reference de leur championnat. Le plafond
`--max` existe pour cela -- la source n'est pas une API publiee, et ses CGU
demandent un volume de requetes raisonnable. Le cache absorbe les repetitions :
deux matchs d'une meme competition partagent toute la reference.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from ibet.prevision import context, forces, predict
from ibet.sources import api_client, horloge
from ibet.stockage import store

load_dotenv()

# Profondeur d'historique par equipe. Au-dela, les matchs decrivent une equipe
# qui n'existe plus vraiment ; en deca, la moyenne devient du bruit.
FORM_DEPTH = 10

# Plafond de previsions par execution. Chacune coute une vingtaine de requetes :
# sans plafond, une journee complete en demanderait des dizaines de milliers.
DEFAULT_MAX = 10

# Une fiche n'est emise que si le match commence dans plus de MARGE_MINUTES,
# a l'heure FIABLE (`horloge`). Le 27/09/2026, l'horloge de l'ordinateur
# avait 13 h 32 de retard : six fiches ont ete emises alors que les matchs
# etaient deja joues, et rien ne l'a empeche. Une prevision emise apres le
# coup d'envoi n'en est pas une -- elle fausse toute mesure qui la compte.
MARGE_MINUTES = 5.0


def minutes_avant_coup_denvoi(match: dict[str, Any]) -> float | None:
    """Minutes entre maintenant (heure fiable) et le coup d'envoi ; None si
    le coup d'envoi est inconnu. Negatif : le match a commence."""
    brut = match.get("kickoff_utc") or ""
    try:
        coup = datetime.fromisoformat(brut)
    except ValueError:
        return None
    if coup.tzinfo is None:
        coup = coup.replace(tzinfo=timezone.utc)
    return (coup - horloge.maintenant(timezone.utc)).total_seconds() / 60.0


def trop_tard(match: dict[str, Any]) -> str | None:
    """La raison de ne pas emettre, ou None si le match est assez loin."""
    minutes = minutes_avant_coup_denvoi(match)
    if minutes is None:
        return "coup d'envoi inconnu"
    if minutes < MARGE_MINUTES:
        if minutes < 0:
            return "match commence depuis %d min" % round(-minutes)
        return "coup d'envoi dans %d min (minimum %d)" % (round(minutes), MARGE_MINUTES)
    return None

# Competitions retenues par `--majeures`. Une journee complete compte plus de
# quatre cents matchs a venir, repartis sur deux cents competitions : sans
# selection, le plafond serait consomme par les premiers matchs de l'horaire,
# c'est-a-dire par des championnats regionaux et des categories de jeunes.
#
# Le couple (pays, competition) et non le seul nom : « Ligue 1 » designe aussi
# la Tunisie et l'Algerie, « Premier League » une douzaine de pays.
MAJOR_COMPETITIONS: tuple[tuple[str, str], ...] = (
    ("Angleterre", "Premier League"),
    ("Espagne", "LaLiga"),
    ("Italie", "Serie A"),
    ("Allemagne", "Bundesliga"),
    ("France", "Ligue 1"),
    ("France", "Ligue 2"),
    ("Angleterre", "Championship"),
    ("Pays-Bas", "Eredivisie"),
    ("Portugal", "Liga Portugal"),
    ("Europe", "Ligue des Champions - Phase de ligue"),
    ("Europe", "Ligue Europa - Phase de ligue"),
    ("Europe", "Ligue Conference - Phase de ligue"),
)


def is_major(match: dict[str, Any]) -> bool:
    """Le match appartient-il a l'une des competitions retenues ?"""
    return any(
        match.get("pays") == country and match.get("championnat") == league
        for country, league in MAJOR_COMPETITIONS
    )


def competition_label(match: dict[str, Any]) -> str:
    """`Premier League (Angleterre)` -- le pays fait partie de l'identite.

    Plusieurs championnats portent le meme nom : sans le pays, une fiche de
    Ligue 1 francaise et une de Ligue 1 algerienne seraient indiscernables dans
    la liste.
    """
    country = match.get("pays") or ""
    league = match.get("championnat") or ""
    return "%s (%s)" % (league, country) if country else league


def _team_ladder(
    entry: dict[str, Any], teams: tuple[str, str]
) -> dict[str, list[float]] | None:
    """Echelle par equipe, indexee par nom d'equipe.

    Le modele rend `domicile` / `exterieur` ; la fiche porte les noms, parce
    qu'elle est relue des mois plus tard, souvent sans le match sous les yeux.
    """
    ladders = entry.get("echelles")
    if not ladders:
        return None
    return {
        "seuils": list(ladders["seuils_equipe"]),
        teams[0]: list(ladders["domicile"]),
        teams[1]: list(ladders["exterieur"]),
    }


# Cote du marche mis en avant, par grandeur. Repris de `predict.METRICS` plutot
# que redefini : deux listes auraient diverge.
PREFERENCES = {metric.key: metric.prefere for metric in predict.METRICS}


def _quantity(entry: dict[str, Any], teams: tuple[str, str]) -> dict[str, Any]:
    """Une grandeur du modele, mise en forme de fiche."""
    ladders = entry.get("echelles") or {}
    quantity: dict[str, Any] = {
        # La cle machine en plus du libelle : la fiche est relue par du code
        # (l'API, la vue des options) autant que par un lecteur, et « Tirs
        # cadres » n'est pas un identifiant.
        "cle": entry["cle"],
        "grandeur": entry["libelle"],
        "attendu_domicile": entry["lambda_domicile"],
        "attendu_exterieur": entry["lambda_exterieur"],
        "attendu_total": entry["total_attendu"],
        "methode": entry["methode"],
        "marche_privilegie": PREFERENCES.get(entry["cle"], "les deux"),
    }
    if entry.get("confiance") is not None:
        quantity["confiance"] = entry["confiance"]
    if entry.get("contexte"):
        # La correction appliquee ET le nombre attendu d'avant : une correction
        # qu'on ne pourrait pas retrouver apres coup serait indefendable.
        quantity["contexte"] = entry["contexte"]
    if entry.get("forces"):
        # Les forces d'attaque et de defense qui ont PRODUIT ce nombre attendu,
        # brutes et regularisees. La ligne « methode » affirmait « corrige du
        # niveau des adversaires » sans jamais dire de combien : sans elles, une
        # fiche relue des mois plus tard n'est pas verifiable, et l'ecart entre
        # brute et regularisee -- qui dit ce que le modele a retenu de
        # l'echantillon -- etait perdu a l'ecriture.
        quantity["forces"] = entry["forces"]
    if entry.get("marche"):
        # L'issue combinee aux cotes : celle du modele seul, celle du marche et
        # les nombres attendus d'avant calage, pour que le melange se relise.
        quantity["marche"] = entry["marche"]

    team_ladder = _team_ladder(entry, teams)
    if team_ladder:
        quantity["echelle_par_equipe"] = team_ladder
    if ladders.get("seuils_total"):
        quantity["echelle_total"] = {
            "seuils": list(ladders["seuils_total"]),
            "probabilites": [round(p, 4) for p in ladders["total"]],
        }

    # `verifie` reste absent tant que le match n'est pas joue : c'est `verify.py`
    # qui tranche, et lui seul. Une fiche ne se juge pas elle-meme.
    quantity["offres"] = [
        {"pari": offer["libelle"], "probabilite": round(offer["p"], 4), "verifie": None}
        for offer in entry.get("offres") or []
    ]

    # Le tableau des marches au-dela de la selection : ecarts, parite, cage
    # inviolee, duels de corners. La fiche les perdait, alors que ce sont eux
    # qui repondent a « quels autres paris ce match propose-t-il ? ».
    if entry.get("marches"):
        quantity["marches"] = [
            {
                "famille": famille["famille"],
                "propositions": [
                    {"pari": o["libelle"], "probabilite": round(o["p"], 4)}
                    for o in famille["propositions"]
                ],
            }
            for famille in entry["marches"]
        ]

    if entry.get("resultat"):
        quantity["issue"] = {k: round(v, 4) for k, v in entry["resultat"].items()}
    if entry.get("scores_probables"):
        quantity["scores_probables"] = [
            [home, away, round(probability, 4)]
            for home, away, probability in entry["scores_probables"]
        ]
    return quantity


def to_record(
    match: dict[str, Any], prediction: dict[str, Any], tz_name: str
) -> dict[str, Any]:
    """Fiche complete, prete a etre enregistree."""
    teams: tuple[str, str] = tuple(prediction["equipes"])  # type: ignore[assignment]
    return {
        "emis_le": horloge.maintenant(ZoneInfo(tz_name)).isoformat(timespec="seconds"),
        # Delai reel entre l'emission et le coup d'envoi : ce qui prouve que la
        # fiche est une PREVISION. Une fiche sans ce champ date d'avant la garde.
        "minutes_avant_coup_denvoi": (
            round(minutes_avant_coup_denvoi(match), 1)
            if minutes_avant_coup_denvoi(match) is not None
            else None
        ),
        "statut_a_l_emission": match.get("statut", ""),
        "match_id": match.get("match_id", ""),
        "match": "%s - %s" % (match["domicile"], match["exterieur"]),
        "competition": competition_label(match),
        "coup_denvoi_local": "%s %s" % (match["date"], match["heure"]),
        "url": match.get("url", ""),
        "reglage": dict(prediction["reglage"]),
        "versions": dict(prediction.get("versions") or {}),
        "grandeurs": [_quantity(entry, teams) for entry in prediction["grandeurs"]],
        # Les quatorze criteres tels qu'ils ont ete releves. Ils font partie de
        # l'engagement : relire une fiche avec la meteo ou le classement
        # d'aujourd'hui ne serait plus relire la prevision qui a ete emise.
        "contexte": prediction.get("contexte"),
        # Les deux entraineurs, pour lire le match : systeme, style, anciennete.
        "entraineurs": prediction.get("entraineurs"),
        "confiance": prediction.get("confiance"),
        "resultat_reel": None,
    }


# Familles de propositions qui portent sur l'issue du match, par opposition a
# celles qui portent sur un comptage. La distinction sert a la vue des options :
# un marche 1X2 ne se presente pas comme une echelle de seuils.
FAMILLES_ISSUE = ("issue", "double chance", "les deux marquent", "ecart", "combine")


def _cote_du_seuil(libelle: str) -> str:
    """Face d'une proposition a seuil : "plus", "moins", ou "" si elle n'en est
    pas une."""
    plat = libelle.lower()
    if "plus de" in plat:
        return "plus"
    if "moins de" in plat:
        return "moins"
    return ""


def betting_options(record: dict[str, Any]) -> dict[str, Any]:
    """Les options de paris d'une fiche, regroupees comme un marche les presente.

    Une fiche est organisee par GRANDEUR, parce que c'est ainsi que le modele
    calcule. Un parieur, lui, cherche un marche : l'issue, puis les buts, puis
    les corners, chacun decline par equipe et au total. Cette vue fait la
    conversion sans rien recalculer -- toutes les probabilites viennent telles
    quelles de la fiche, sinon deux lectures de la meme prevision pourraient ne
    pas coincider.

    Chaque grandeur porte le cote mis en avant (`marche_privilegie`) et les
    propositions de ce cote. Les deux faces restent presentes : "plus de 9.5
    corners" et "moins de 9.5 corners" sont les deux faces de la MEME
    probabilite, l'une vaut p et l'autre 1 - p, et le modele ne peut pas etre
    plus fiable sur l'une que sur l'autre. Mettre une face devant est un choix
    de presentation ; ecarter l'autre serait decider a la place du lecteur.
    """
    grandeurs = record.get("grandeurs") or []
    equipes = [
        part.strip() for part in (record.get("match") or " - ").split(" - ", 1)
    ]

    buts = next((g for g in grandeurs if g.get("cle") == "buts"), {})
    issue: dict[str, Any] = {}
    if buts.get("issue"):
        issue = {
            "probabilites": buts["issue"],
            "scores_probables": buts.get("scores_probables") or [],
            "marches": [
                proposition
                for famille in buts.get("marches") or []
                if famille["famille"] in FAMILLES_ISSUE
                for proposition in famille["propositions"]
            ],
        }

    lignes = []
    for grandeur in grandeurs:
        prefere = grandeur.get("marche_privilegie") or "les deux"
        offres = grandeur.get("offres") or []
        echelle_equipe = grandeur.get("echelle_par_equipe") or {}
        echelle_total = grandeur.get("echelle_total") or {}
        totaux = echelle_total.get("probabilites") or []
        lignes.append(
            {
                "cle": grandeur.get("cle", ""),
                "libelle": grandeur.get("grandeur", ""),
                "attendu": {
                    "domicile": grandeur.get("attendu_domicile"),
                    "exterieur": grandeur.get("attendu_exterieur"),
                    "total": grandeur.get("attendu_total"),
                },
                "par_equipe": {
                    "seuils": echelle_equipe.get("seuils") or [],
                    equipes[0]: echelle_equipe.get(equipes[0]) or [],
                    equipes[-1]: echelle_equipe.get(equipes[-1]) or [],
                },
                "total": {
                    "seuils": echelle_total.get("seuils") or [],
                    "plus_de": totaux,
                    "moins_de": [round(1.0 - p, 4) for p in totaux],
                },
                "marche_privilegie": prefere,
                "propositions": offres,
                "propositions_privilegiees": [
                    offre
                    for offre in offres
                    if prefere == "les deux"
                    or _cote_du_seuil(offre.get("pari", "")) in ("", prefere)
                ],
                "autres_marches": grandeur.get("marches") or [],
                "confiance": grandeur.get("confiance"),
            }
        )

    return {
        "match": record.get("match", ""),
        "equipes": equipes,
        "competition": record.get("competition", ""),
        "coup_denvoi_local": record.get("coup_denvoi_local", ""),
        "issue": issue,
        "grandeurs": lignes,
        "confiance": record.get("confiance"),
    }


def forecast_match(
    match: dict[str, Any],
    tz_name: str,
    use_cache: bool = True,
    avec_contexte: bool = True,
) -> dict[str, Any]:
    """Calcule et met en fiche la prevision d'un match. Leve `NotEnoughData`.

    `avec_contexte` ajoute les quatorze criteres : classement, arbitre, meteo,
    compositions, confrontations chiffrees. Il coute une poignee de requetes de
    plus par match, presque toutes mises en cache pour trente jours puisqu'elles
    portent sur des matchs termines.

    Le couper rend exactement la fiche d'avant. C'est ce qui permet de comparer
    les deux sur les memes matchs -- et c'est la seule facon de savoir si le
    contexte apporte quelque chose.
    """
    form = api_client.get_form(
        match, count=FORM_DEPTH, tz_name=tz_name, use_cache=use_cache, with_stats=True
    )
    baseline = None
    try:
        baseline = api_client.league_baseline(
            match, tz_name=tz_name, use_cache=use_cache, with_stats=True
        )
    except api_client.ApiError:
        # Sans reference de championnat le modele retombe sur la moyenne
        # production / concession, ce que la fiche indique dans `methode`.
        pass

    collecte = None
    if avec_contexte:
        try:
            collecte = context.collecter(
                match, form, baseline, poids=context.DEFAULT_POIDS,
                tz_name=tz_name, use_cache=use_cache,
            )
        except api_client.ApiError as exc:
            # Le contexte est un complement : son echec ne doit pas emporter la
            # prevision. La fiche dira qu'il manque, ce qui est une information
            # -- et une fiche sans contexte reste une fiche juste.
            collecte = None
            print("  contexte indisponible : %s" % exc, file=sys.stderr)

    # Chaque emission enregistre un releve de cotes horodate. C'est ce qui fait
    # exister le mouvement de ligne : une cote seule ne dit rien, deux cotes
    # prises a deux moments disent ou est alle l'argent. Le releve est ecrit
    # meme quand il est identique au precedent -- « la cote n'a pas bouge » est
    # aussi une information, et l'effacer la rendrait indistinguable de
    # « personne n'a regarde ».
    match_id = match.get("match_id", "")
    releve = (collecte or {}).get("cotes") or {}
    if releve.get("meilleures"):
        try:
            store.save_odds(match_id, releve["meilleures"], operateur="betexplorer")
        except ValueError:
            pass
    historique_cotes = store.odds_history(match_id)
    # Les cotes ne sont PAS repassees en argument : le releve de la collecte
    # porte les deux jeux (moyennes et meilleures), la base n'en garde qu'un.
    # Passer celui de la base ferait prendre les meilleures cotes pour le
    # consensus du marche, et sous-estimerait la marge des operateurs.
    # L'historique, lui, ne sert qu'au mouvement de ligne.
    # Nombres de buts attendus d'apres les notes attaque / defense globales,
    # sur l'echelle commune a toutes les competitions. None des qu'une des deux
    # equipes n'a pas de notes etablies, ou que les deux appartiennent a des
    # groupes qui ne se sont jamais rencontres : le modele reste alors
    # exactement ce qu'il etait, plutot que d'etre corrige par une force qu'on
    # ne connait pas.
    lambdas_forces = forces.lambdas_attendus(
        match.get("domicile", ""), match.get("exterieur", ""),
        competition=match.get("championnat", ""),
    )
    prediction = predict.build(
        match, form, baseline,
        collecte=collecte,
        historique_cotes=historique_cotes or None,
        lambdas_forces=lambdas_forces,
    )
    return to_record(match, prediction, tz_name)


def upcoming(
    date: str,
    tz_name: str,
    league: str | None,
    use_cache: bool,
    variants: bool = False,
    country: str | None = None,
    majors: bool = False,
) -> list[dict[str, Any]]:
    """Matchs a venir de la journee, dans l'ordre du coup d'envoi.

    Les equipes feminines, de jeunes et reserves sont ecartees par defaut : un
    filtre par sous-chaine ramene « Premier League Cup » avec des equipes -21,
    dont l'historique et la reference de championnat ne valent rien pour le
    modele. C'est la meme regle que celle appliquee a `--team`.
    """
    matches = api_client.get_matches(
        date, league=league, provider="flashscore", tz_name=tz_name, use_cache=use_cache
    )
    # On ne garde que les matchs a venir pour prevoir, mais la journee en
    # contient aussi des termines : archives au passage, sans requete de plus.
    store.archiver_journee(matches, api_client.FINISHED)
    scheduled = [m for m in matches if m.get("statut") == api_client.SCHEDULED]
    if majors:
        scheduled = [m for m in scheduled if is_major(m)]
    if country:
        # Le filtre par competition est une recherche par sous-chaine, et la
        # couverture est mondiale : « Championship » ramene aussi bien la
        # deuxieme division anglaise que la Motsepe Championship sud-africaine.
        # Le pays est ce qui les separe.
        needle = country.strip().lower()
        scheduled = [m for m in scheduled if needle in m["pays"].lower()]
    if variants:
        return scheduled
    return [
        m
        for m in scheduled
        if not api_client.is_variant_team(m["domicile"], m["championnat"])
        and not api_client.is_variant_team(m["exterieur"], m["championnat"])
    ]


def emit(
    days: int,
    start: str,
    tz_name: str,
    league: str | None,
    limit: int,
    use_cache: bool,
    skip_known: bool = True,
    variants: bool = False,
    country: str | None = None,
    majors: bool = False,
    avec_contexte: bool = True,
) -> tuple[int, list[str]]:
    """Emet les previsions des matchs a venir. Retourne (emises, avertissements)."""
    known = {p["match_id"] for p in store.all_predictions(limit=1000)} if skip_known else set()
    warnings: list[str] = []
    emitted = 0

    for offset in range(days):
        day = (
            datetime.strptime(start, "%Y-%m-%d") + timedelta(days=offset)
        ).strftime("%Y-%m-%d")
        try:
            matches = upcoming(day, tz_name, league, use_cache, variants, country, majors)
        except api_client.ApiError as exc:
            warnings.append("%s : %s" % (day, exc))
            continue

        for match in matches:
            if emitted >= limit:
                return emitted, warnings
            if match.get("match_id") in known:
                continue
            libelle = "%s - %s" % (match["domicile"], match["exterieur"])
            raison = trop_tard(match)
            if raison:
                warnings.append("%s : non emise, %s" % (libelle, raison))
                continue
            try:
                record = forecast_match(match, tz_name, use_cache, avec_contexte)
            except (predict.NotEnoughData, api_client.ApiError) as exc:
                warnings.append("%s : %s" % (libelle, exc))
                continue
            # Deuxieme controle : le calcul (reseau, contexte) peut prendre
            # plusieurs minutes, et le match avoir commence entre-temps.
            raison = trop_tard(match)
            if raison:
                warnings.append("%s : non emise, %s" % (libelle, raison))
                continue
            store.save(record)
            emitted += 1
            print(
                "  %s  %s - %s (%s)"
                % (
                    record["coup_denvoi_local"],
                    match["domicile"],
                    match["exterieur"],
                    record["competition"],
                )
            )

    return emitted, warnings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Emet les previsions des matchs a venir et les enregistre en base."
    )
    parser.add_argument("--date", help="Jour de depart (YYYY-MM-DD, defaut : aujourd'hui)")
    parser.add_argument(
        "--jours",
        type=int,
        default=1,
        metavar="N",
        help="Nombre de journees a couvrir a partir de --date (defaut : 1)",
    )
    parser.add_argument("--league", "-l", help="Filtre par championnat (sous-chaine)")
    parser.add_argument(
        "--pays",
        "-p",
        help="Filtre par pays (sous-chaine). Necessaire des que le nom de la "
        "competition se repete d'un pays a l'autre : Championship, Ligue 1, "
        "Premier League...",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=DEFAULT_MAX,
        metavar="N",
        help="Plafond de previsions emises (defaut : %d)" % DEFAULT_MAX,
    )
    parser.add_argument(
        "--refaire",
        action="store_true",
        help="Reemet aussi les matchs deja en base (remplace la fiche precedente)",
    )
    parser.add_argument(
        "--majeures",
        action="store_true",
        help="Ne retient que les grandes competitions europeennes (%d couvertes)"
        % len(MAJOR_COMPETITIONS),
    )
    parser.add_argument(
        "--variantes",
        action="store_true",
        help="Conserve equipes feminines, de jeunes et reserves (ecartees par defaut)",
    )
    parser.add_argument("--tz", help="Fuseau IANA, ex: Europe/Paris")
    parser.add_argument("--no-cache", action="store_true", help="Ignore le cache local")
    parser.add_argument(
        "--sans-contexte",
        action="store_true",
        help=(
            "N'emet que le modele, sans les quatorze criteres (classement, "
            "arbitre, meteo, compositions, confrontations chiffrees). Sert a "
            "comparer les deux sur les memes matchs."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _, tz_name = api_client.resolve_settings("flashscore", args.tz)
    except api_client.ApiError as exc:
        print("Erreur : %s" % exc, file=sys.stderr)
        return 1

    start = args.date or horloge.maintenant(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    try:
        api_client.validate_date(start)
    except api_client.ApiError as exc:
        print("Erreur : %s" % exc, file=sys.stderr)
        return 1

    store.init()
    # Avant d'emettre, pas apres : une fiche produite sur des notes perimees ne
    # se rattrape pas -- elle est enregistree telle quelle et jamais recalculee.
    retard = forces.avertissement()
    if retard:
        print("Attention : %s\n" % retard, file=sys.stderr)
    print(
        "Emission des previsions a partir du %s (%d jour(s), %d au maximum)"
        % (start, args.jours, args.max)
    )
    emitted, warnings = emit(
        days=max(1, args.jours),
        start=start,
        tz_name=tz_name,
        league=args.league,
        limit=args.max,
        use_cache=not args.no_cache,
        skip_known=not args.refaire,
        variants=args.variantes,
        country=args.pays,
        majors=args.majeures,
        avec_contexte=not args.sans_contexte,
    )

    if warnings:
        print("\nEcartes (%d) :" % len(warnings), file=sys.stderr)
        for warning in warnings[:10]:
            print("  - %s" % warning, file=sys.stderr)
        if len(warnings) > 10:
            print("  ... et %d autres" % (len(warnings) - 10), file=sys.stderr)

    print("\n%d prevision(s) emise(s). Bilan : %s" % (emitted, store.tally()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
