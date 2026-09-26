"""Point d'entree CLI : recupere les matchs de football d'une date donnee.

Exemples :
    python main.py --date 2026-09-10
    python main.py --date 2026-09-10 --league "Ligue 1" --export csv
    python main.py --provider football-data --date 2026-09-10 --export both
    python main.py --clear-cache
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import date as date_cls
from datetime import datetime

from dotenv import load_dotenv

import api_client
import cache
import backtest
import context
import exporter
import marche
import store
import forces
import predict

# Charge .env AVANT toute lecture de os.getenv dans les autres modules.
load_dotenv()

# Garde-fou pour --open : une selection large ouvrirait des centaines d'onglets.
MAX_OPEN_TABS = 5

# Garde-fou pour --form : une requete par match selectionne, et jusqu'a 2N de
# plus avec --stats. A utiliser avec --team, sur un match ou deux.
MAX_FORM_MATCHES = 3

# Mode classement : plus permissif que --form, puisque c'est tout son interet,
# mais borne car chaque match coute une requete d'historique.
MAX_RANK_MATCHES = 30


def _force_utf8_output() -> None:
    """Evite un crash sur les noms de clubs non-ASCII (Gyor, Plzen, Bodo/Glimt...).

    Sous Windows, une sortie redirigee vers un fichier ou un pipe utilise cp1252,
    qui ne sait pas encoder tous les caracteres renvoyes par les API.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


_force_utf8_output()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Recupere les matchs de football d'une date via API officielle.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Providers disponibles :\n"
            "  flashscore     (defaut) aucune inscription, couverture mondiale,\n"
            "                 fenetre de +/- 7 jours autour d'aujourd'hui\n"
            "  thesportsdb    gratuit, aucune inscription\n"
            "  football-data  gratuit, cle sur football-data.org\n"
            "  api-football   gratuit 100 req/jour, cle sur api-football.com\n"
        ),
    )
    parser.add_argument(
        "--date",
        "-d",
        default=date_cls.today().isoformat(),
        help="Date au format YYYY-MM-DD (defaut : aujourd'hui)",
    )
    parser.add_argument(
        "--league",
        "-l",
        help='Filtre sur le nom du championnat, ex: "Ligue 1" (sous-chaine)',
    )
    parser.add_argument(
        "--provider",
        "-p",
        choices=sorted(api_client.PROVIDERS),
        help="Source de donnees (defaut : variable PROVIDER du .env)",
    )
    parser.add_argument(
        "--export",
        "-e",
        choices=["csv", "json", "both"],
        help="Exporte le resultat dans ./exports/",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Chemin de sortie explicite (uniquement si --export csv ou json)",
    )
    parser.add_argument(
        "--round",
        "-r",
        type=int,
        metavar="N",
        help=(
            "Affiche la journee N complete au lieu d'une seule date. "
            "Necessite --league sur une grande competition europeenne."
        ),
    )
    parser.add_argument(
        "--list-leagues",
        action="store_true",
        help="Liste les competitions couvertes par le mode journee puis quitte",
    )
    parser.add_argument(
        "--team",
        "-t",
        metavar="NOM",
        help=(
            "Ne garde que les matchs d'une equipe (sous-chaine, domicile ou "
            "exterieur). Combine avec --stats pour la fiche d'un seul match."
        ),
    )
    parser.add_argument(
        "--all-teams",
        action="store_true",
        help=(
            "Avec --team, conserve les equipes feminines, de jeunes et reserves, "
            "ecartees par defaut."
        ),
    )
    parser.add_argument(
        "--form",
        nargs="?",
        type=int,
        const=api_client.FORM_DEFAULT,
        metavar="N",
        help=(
            "Forme des deux equipes sur leurs N derniers matchs (defaut : %d, "
            "maximum %d). Une seule requete. Avec --stats, ajoute les moyennes "
            "de tirs, corners et possession, au prix d'une requete par match. "
            "Provider flashscore, %d matchs selectionnes au maximum."
            % (api_client.FORM_DEFAULT, api_client.FORM_MAX, MAX_FORM_MATCHES)
        ),
    )
    parser.add_argument(
        "--pays",
        help=(
            "Filtre par pays (sous-chaine). Indispensable des que le nom de la "
            "competition se repete d'un pays a l'autre : --league \"Premier "
            "League\" ramene le Kazakhstan, Hong Kong et le Ghana autant que "
            "l'Angleterre, et rien ne le signale. Meme option que forecast.py."
        ),
    )
    parser.add_argument(
        "--regler",
        metavar="CLE=V1,V2,...",
        help=(
            "Avec --backtest : compare plusieurs valeurs d'un reglage du modele "
            "sur EXACTEMENT les memes matchs, et les classe par log-vraisemblance. "
            "Ex: --regler xg_echantillon=0,3,6,12. La premiere valeur sert de "
            "reference pour l'ecart appariee, donc mettre en tete celle en "
            "vigueur. Cles : %s."
            % ", ".join(predict.DEFAULT_PARAMS._fields)
        ),
    )
    parser.add_argument(
        "--marches-etendus",
        action="store_true",
        help=(
            "Avec --valeur : demande aussi les marches que l'appel groupe ne "
            "sert pas (totaux par equipe, corners, les deux marquent, double "
            "chance, parite). Ils ouvrent presque toutes les familles de la "
            "fiche, mais coutent UN APPEL PAR MATCH -- environ 7 credits "
            "chacun, contre 2 pour toute une competition. Sans ce drapeau, "
            "seuls le 1X2 et les totaux de buts sont releves."
        ),
    )
    parser.add_argument(
        "--cote",
        action="append",
        metavar="MATCH_ID:LIBELLE=COTE",
        help=(
            "Enregistre la cote d'une proposition, pour un marche que rien ne "
            "collecte automatiquement. Ex: --cote "
            "Glagw7N6:\"Plus de 1.5 buts au total\"=1.28. Le libelle doit etre "
            "celui EXACT de la fiche : rapprocher un vocabulaire d'operateur de "
            "celui du modele valoriserait un pari avec le prix d'un autre. "
            "Repetable. Le 1X2, lui, est releve tout seul."
        ),
    )
    parser.add_argument(
        "--valeur",
        action="store_true",
        help=(
            "Parmi les fiches DEJA EMISES dont le match n'est pas joue, celles "
            "ou le modele s'ecarte du marche assez pour que le pari rapporte en "
            "moyenne. C'est la seule sortie qui reponde a « ou me servir du "
            "modele ? » -- une prevision calibree jouee partout perd la marge de "
            "l'operateur a chaque coup. Releve les cotes au passage, ce qui fait "
            "exister le mouvement de ligne."
        ),
    )
    parser.add_argument(
        "--bilan",
        action="store_true",
        help=(
            "Ce que les fiches DEJA EMISES ont appris, sans reseau : le "
            "rendement des paris face au marche, tranche par tranche de valeur, "
            "et ce que valent les quatre criteres laisses a poids zero. Ces "
            "deux mesures ne peuvent venir que de l'usage reel -- le banc "
            "d'essai rejoue des matchs, il ne peut ni connaitre les cotes du "
            "moment ni l'arbitre qui avait ete designe."
        ),
    )
    parser.add_argument(
        "--backtest",
        action="store_true",
        help=(
            "Rejoue les matchs TERMINES de la selection et mesure la qualite du "
            "modele (Brier, log-loss, reussite) face a deux references. "
            "L'historique est coupe au coup d'envoi de chaque match."
        ),
    )
    parser.add_argument(
        "--top",
        nargs="?",
        type=int,
        const=10,
        metavar="N",
        help=(
            "Classe les previsions des matchs A VENIR de la selection, les plus "
            "tranchees d'abord (defaut : 10, plafond %d matchs analyses). "
            "Implique --predict." % MAX_RANK_MATCHES
        ),
    )
    parser.add_argument(
        "--predict",
        action="store_true",
        help=(
            "Prevision (buts, corners, tirs cadres, cartons jaunes) par loi de "
            "Poisson sur la forme recente. Implique --form et --stats."
        ),
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help=(
            "Ouvre la page Flashscore des matchs selectionnes dans le navigateur "
            "(provider flashscore, %d onglets au maximum)." % MAX_OPEN_TABS
        ),
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help=(
            "Ajoute les statistiques des matchs termines : tirs cadres, corners, "
            "fautes, cartons, arbitre. Une requete API par match."
        ),
    )
    parser.add_argument(
        "--max-stats",
        type=int,
        default=20,
        metavar="N",
        help="Plafond de matchs enrichis par --stats (defaut : 20, protege le quota)",
    )
    parser.add_argument(
        "--sans-contexte",
        action="store_true",
        help=(
            "Avec --predict, n'applique que le modele : les quatorze criteres "
            "(style, forme, classement, arbitre, meteo, xG, confrontations...) "
            "ne sont ni releves ni affiches."
        ),
    )
    parser.add_argument(
        "--contexte",
        action="store_true",
        help=(
            "Avec --backtest, rejoue AUSSI avec les quatorze criteres et compare "
            "les deux sur les memes matchs. Le classement et l'arbitre sont "
            "coupes : sur un match passe, ils contiennent le resultat cherche."
        ),
    )
    parser.add_argument("--tz", help="Fuseau horaire IANA, ex: Europe/Paris")
    parser.add_argument(
        "--no-cache", action="store_true", help="Ignore le cache et force l'appel API"
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true", help="N'affiche pas le tableau console"
    )
    parser.add_argument(
        "--clear-cache", action="store_true", help="Vide le cache local puis quitte"
    )
    return parser


def _nearby_hint(collected: list[dict], date: str, label: str) -> str:
    """Propose les dates réellement jouées quand la date demandée est vide."""
    if not collected:
        return ""
    counts: dict[str, int] = {}
    for match in collected:
        if match["date"]:
            counts[match["date"]] = counts.get(match["date"], 0) + 1
    nearby = sorted(counts.items(), key=lambda kv: abs_days(kv[0], date))[:5]
    lines = [
        "  Pas de match de %s le %s. Dates jouees les plus proches :" % (label, date),
        "",
    ]
    for day, count in sorted(nearby):
        lines.append("    %s  (%d match%s)" % (day, count, "s" if count > 1 else ""))
    lines.append("")
    return "\n".join(lines)


def abs_days(a: str, b: str) -> int:
    fmt = "%Y-%m-%d"
    return abs((datetime.strptime(a, fmt) - datetime.strptime(b, fmt)).days)


def collect(args: argparse.Namespace) -> tuple[list[dict], str, str]:
    """Recupere les matchs et retourne (matchs, titre affiche, message d'aide)."""
    api_client.validate_date(args.date)
    provider, tz_name = api_client.resolve_settings(args.provider, args.tz)
    resolved = api_client.resolve_tsdb_league(args.league) if args.league else None

    # Mode journee : contourne le plafonnement de la cle gratuite TheSportsDB.
    if provider == "thesportsdb" and resolved:
        league_id, label = resolved
        on_date, collected = api_client.fetch_league_rounds(
            league_id,
            args.date,
            tz_name,
            round_no=args.round,
            use_cache=not args.no_cache,
        )
        season = api_client.season_for(args.date)
        if args.round is not None:
            return collected, "%s - journee %d (%s)" % (label, args.round, season), ""
        if on_date:
            return on_date, "%s - %s" % (label, args.date), ""
        return [], "%s - %s" % (label, args.date), _nearby_hint(collected, args.date, label)

    if args.round is not None:
        raise api_client.ApiError(
            "--round n'est disponible que sur le provider thesportsdb, avec une "
            "competition connue. Voir `python main.py --list-leagues`."
        )

    matches = api_client.get_matches(
        args.date,
        league=args.league,
        provider=provider,
        tz_name=tz_name,
        use_cache=not args.no_cache,
    )
    # Tout match termine qui passe ici est archive : c'est ce qui rend la
    # verification independante du moment ou on la lance. La source oublie au
    # bout de sept jours, l'archive garde.
    store.archiver_journee(matches, api_client.FINISHED)
    if getattr(args, "pays", None):
        # Le filtre par competition est une sous-chaine sur une couverture
        # mondiale : sans le pays, une mesure qu'on croit faite sur la Premier
        # League anglaise porte en realite sur celles du Kazakhstan et du Ghana.
        # Rien dans la sortie ne le disait, et un banc d'essai fausse ainsi ne
        # se voit pas -- d'ou cette option.
        besoin = args.pays.strip().lower()
        matches = [m for m in matches if besoin in (m.get("pays") or "").lower()]

    hint = ""
    if not matches and args.pays:
        hint = (
            "  Astuce : aucun match ne combine %r et le pays %r ce jour-la.\n"
            "  Les deux filtres sont des sous-chaines et s'appliquent ensemble.\n"
            % (args.league or "(toutes competitions)", args.pays)
        )
    elif not matches and args.league:
        hint = (
            "  Astuce : %r ne correspond a aucun championnat renvoye ce jour-la.\n"
            "  Le filtre est une sous-chaine. Pour les grandes competitions\n"
            "  europeennes, voir `python main.py --list-leagues`.\n" % args.league
        )
    return matches, args.date, hint


def enrich_with_stats(
    matches: list[dict], max_stats: int, use_cache: bool = True
) -> list[str]:
    """Attache les statistiques aux matchs termines. Retourne les avertissements.

    Une erreur sur un match n'interrompt pas les autres : on collecte et on
    signale a la fin. Un quota epuise, en revanche, arrete la boucle — insister
    ne ferait que consommer des requetes pour rien.
    """
    finished = [m for m in matches if m["statut"] == api_client.FINISHED]
    warnings: list[str] = []

    if not finished:
        return ["Aucun match termine : les statistiques ne sont disponibles "
                "qu'apres le coup de sifflet final."]

    budget = finished[:max_stats]
    if len(finished) > max_stats:
        # « Les N premiers » n'est pas un echantillon : le flux est trie par
        # pays, donc la coupe s'arrete toujours au meme endroit de l'alphabet.
        # Sur une journee mondiale, --max-stats 40 ne depasse pas l'Allemagne,
        # et un banc d'essai lance ainsi mesure l'Afrique et les championnats
        # amateurs -- dont Flashscore ne publie aucune statistique -- en croyant
        # mesurer les grands championnats. Dire ou l'on s'est arrete est le seul
        # moyen que cela se voie.
        derniere = budget[-1].get("championnat", "")
        pays = budget[-1].get("pays", "")
        warnings.append(
            "%d matchs termines, seuls les %d premiers sont enrichis "
            "(--max-stats pour relever le plafond). Le flux est trie par pays : "
            "la coupe s'arrete a %s%s, et tout ce qui vient apres dans "
            "l'alphabet est sans statistiques. Utilisez --pays ou --league pour "
            "choisir ce qui est mesure au lieu de le subir."
            % (
                len(finished), max_stats,
                derniere or "la fin de la selection",
                " (%s)" % pays if pays else "",
            )
        )

    for match in budget:
        try:
            match["stats"] = api_client.get_stats(match, use_cache=use_cache)
        except api_client.QuotaError as exc:
            warnings.append("Quota atteint, enrichissement interrompu : %s" % exc)
            break
        except api_client.ApiError as exc:
            warnings.append(
                "%s - %s : %s" % (match["domicile"], match["exterieur"], exc)
            )
    return warnings


def open_in_browser(matches: list[dict]) -> list[str]:
    """Ouvre la page Flashscore des matchs selectionnes. Retourne les avertissements.

    Seul le provider flashscore expose une URL de match ; les API REST n'en
    fournissent pas. Le nombre d'onglets est plafonne : une selection sans
    filtre compte plusieurs centaines de matchs.
    """
    links = [m["url"] for m in matches if m.get("url")]
    if not links:
        return [
            "--open n'a rien a ouvrir : seul le provider flashscore fournit "
            "l'adresse de la page d'un match."
        ]

    warnings: list[str] = []
    if len(links) > MAX_OPEN_TABS:
        warnings.append(
            "%d matchs selectionnes, seuls les %d premiers sont ouverts. "
            "Affinez avec --team ou --league."
            % (len(links), MAX_OPEN_TABS)
        )
        links = links[:MAX_OPEN_TABS]

    for link in links:
        webbrowser.open_new_tab(link)
    print("Ouvert dans le navigateur : %d page(s)." % len(links))
    return warnings


def enrich_with_form(
    matches: list[dict],
    count: int,
    tz_name: str,
    use_cache: bool,
    with_stats: bool,
    limit: int = MAX_FORM_MATCHES,
) -> list[str]:
    """Attache la forme recente aux matchs selectionnes. Retourne les avertissements."""
    warnings: list[str] = []
    budget = matches[:limit]
    if len(matches) > limit:
        warnings.append(
            "%d matchs selectionnes, la forme n'est calculee que pour les %d "
            "premiers. Affinez avec --team." % (len(matches), limit)
        )

    for match in budget:
        try:
            match["form"] = api_client.get_form(
                match,
                count=count,
                tz_name=tz_name,
                use_cache=use_cache,
                with_stats=with_stats,
            )
        except api_client.QuotaError as exc:
            warnings.append("Quota atteint, forme interrompue : %s" % exc)
            break
        except api_client.ApiError as exc:
            warnings.append(
                "%s - %s : %s" % (match["domicile"], match["exterieur"], exc)
            )
    return warnings


def build_predictions(
    matches: list[dict], tz_name: str, use_cache: bool, with_context: bool = True
) -> list[str]:
    """Calcule la prevision des matchs deja enrichis de leur forme.

    `with_context` releve les quatorze criteres en plus du modele. Il coute une
    poignee de requetes par match, presque toutes mises en cache pour trente
    jours ; le couper rend exactement la prevision d'avant.
    """
    warnings: list[str] = []
    for match in matches:
        if not match.get("form"):
            continue
        # La reference de championnat est optionnelle : sans elle, le modele
        # retombe sur la moyenne production/concession, ce que l'affichage dit.
        baseline = None
        try:
            # with_stats : la reference detaillee (corners, tirs, cartons)
            # coute une requete par match de la competition la premiere fois,
            # puis rien pendant trente jours.
            baseline = api_client.league_baseline(
                match, tz_name, use_cache, with_stats=True
            )
        except api_client.ApiError as exc:
            warnings.append(
                "%s - %s : pas de reference de competition (%s)"
                % (match["domicile"], match["exterieur"], exc)
            )
        collecte = None
        if with_context:
            try:
                collecte = context.collecter(
                    match, match["form"], baseline, tz_name=tz_name,
                    use_cache=use_cache,
                )
            except api_client.ApiError as exc:
                # Le contexte complete le modele, il ne le conditionne pas :
                # son echec ne doit pas emporter la prevision.
                warnings.append(
                    "%s - %s : contexte indisponible (%s)"
                    % (match["domicile"], match["exterieur"], exc)
                )
        try:
            match["prediction"] = predict.build(
                match, match["form"], baseline, collecte=collecte,
                lambdas_forces=forces.lambdas_attendus(
                    match.get("domicile", ""), match.get("exterieur", "")
                ),
            )
        except predict.NotEnoughData as exc:
            warnings.append(
                "%s - %s : %s" % (match["domicile"], match["exterieur"], exc)
            )
    return warnings


def parse_reglages(brut: str) -> tuple[str, list[predict.Params]]:
    """"cle=v1,v2,..." -> la cle et les reglages a comparer, dans l'ordre donne.

    L'ordre compte : `backtest.tune` prend le PREMIER comme reference de l'ecart
    appariee. C'est a l'appelant de mettre en tete la valeur en vigueur, sans
    quoi le tableau comparerait deux inconnues entre elles.

    Leve `ValueError` avec un message lisible : une cle mal ecrite dans une
    commande doit s'expliquer, pas produire un classement sur un reglage que
    l'utilisateur ne croyait pas faire varier.
    """
    cle, separateur, valeurs = brut.partition("=")
    cle = cle.strip()
    if not separateur or not cle:
        raise ValueError(
            "Format attendu : CLE=V1,V2,... (ex: xg_echantillon=0,3,6). Recu : %r"
            % brut
        )
    if cle not in predict.DEFAULT_PARAMS._fields:
        raise ValueError(
            "Reglage inconnu : %r. Cles possibles : %s."
            % (cle, ", ".join(predict.DEFAULT_PARAMS._fields))
        )
    candidats = []
    for morceau in valeurs.split(","):
        morceau = morceau.strip()
        if not morceau:
            continue
        try:
            candidats.append(
                predict.DEFAULT_PARAMS._replace(**{cle: float(morceau)})
            )
        except ValueError:
            raise ValueError(
                "Valeur illisible pour %s : %r (un nombre etait attendu)."
                % (cle, morceau)
            ) from None
    if len(candidats) < 2:
        raise ValueError(
            "Il faut au moins deux valeurs a comparer : --regler %s=0,3" % cle
        )
    return cle, candidats


def paris_de_valeur(
    tz_name: str, use_cache: bool, etendus: bool = False
) -> dict[str, Any]:
    """Les paris de valeur parmi les fiches en attente, aux cotes du moment.

    Le reseau est ici et non dans `marche` : ce module lit la base et rien
    d'autre, ce qui le rend testable sans cle ni connexion. La cotation est la
    seule partie qui demande une source, et elle n'a pas a contaminer le calcul.

    Chaque appel **enregistre** un releve horodate. C'est voulu, et c'est le
    seul moyen de faire exister le mouvement de ligne : une cote seule ne dit
    rien, deux cotes prises a deux moments disent ou est alle l'argent. Le
    releve est ecrit meme identique au precedent -- « la cote n'a pas bouge »
    est aussi une information, et l'effacer la rendrait indistinguable de
    « personne n'a regarde ».
    """
    fiches = store.pending()
    par_date: dict[str, list[dict[str, Any]]] = {}
    for fiche in fiches:
        # "AAAA-MM-JJ HH:MM" -> la date seule, qui indexe le releve du jour.
        jour = (fiche.get("coup_denvoi_local") or "")[:10]
        if jour and fiche.get("match_id"):
            par_date.setdefault(jour, []).append(fiche)

    prets: list[dict[str, Any]] = []
    releves = 0
    incidents: list[str] = []
    non_apparies: list[str] = []
    # Competitions que l'agregateur ne publie pas : dites, pas devinees.
    hors_couverture: set[str] = set()
    for jour, groupe in sorted(par_date.items()):
        # L'agregateur, s'il est configure. Il n'a pas de cle « football »
        # generique : il indexe et facture par COMPETITION. On interroge donc
        # une fois chacune de celles que les fiches du jour couvrent, et rien
        # d'autre -- une competition qu'il ne publie pas n'est pas demandee.
        agregat: list[dict[str, Any]] = []
        for competition in sorted(
            {(f.get("competition") or "") for f in groupe}
        ):
            # Le flux ecrit « Premier League (Angleterre) » : le pays est entre
            # parentheses, et il fait partie de l'identite -- « Premier League »
            # designe une dizaine de championnats dans le monde.
            nom, _, pays = competition.rpartition(" (")
            sport_key = api_client.cle_agregateur(nom, pays.rstrip(")"))
            if sport_key:
                for evenement in api_client.aggregated_odds(
                    sport_key, jour, use_cache
                ):
                    # La cle voyage avec l'evenement : l'appel par match en a
                    # besoin, et la recalculer plus bas depuis le libelle de
                    # competition invitait a se tromper d'argument.
                    agregat.append(dict(evenement, sport_key=sport_key))
            elif competition:
                hors_couverture.add(competition)
        try:
            # Une requete par JOUR, pas par match : la source publie la journee
            # entiere, et dix fiches du meme jour ne coutent qu'un appel.
            cotes_du_jour = api_client.market_odds(jour, use_cache)
        except api_client.ApiError as exc:
            incidents.append("%s : %s" % (jour, exc))
            continue
        for fiche in groupe:
            bloc = cotes_du_jour.get(fiche["match_id"]) or {}
            meilleures = dict(bloc.get("meilleures") or {})

            # L'agregateur se rapproche par NOM d'equipe : ses identifiants ne
            # sont pas ceux de Flashscore. Les deux equipes doivent se
            # reconnaitre, sinon l'evenement est ecarte et compte a part --
            # apparier deux matchs differents valoriserait un pari avec le prix
            # d'un autre, sans jamais lever d'erreur.
            equipes = tuple(
                (fiche.get("match") or "").split(" - ", 1) + [""]
            )[:2]
            trouve = next(
                (
                    event
                    for event in agregat
                    if equipes[0]
                    and equipes[1]
                    and api_client.memes_equipes(event["domicile"], equipes[0])
                    and api_client.memes_equipes(event["exterieur"], equipes[1])
                ),
                None,
            )
            if trouve:
                issues, libelles = marche.libelles_depuis_agregateur(fiche, trouve)
                sport_key = trouve.get("sport_key", "")
                if etendus and trouve.get("id") and sport_key:
                    # Un appel PAR MATCH : c'est pour cela que le drapeau
                    # existe. Les libelles traduits s'ajoutent a ceux des
                    # marches vedettes plutot que de les remplacer.
                    riche = api_client.aggregated_event_odds(
                        sport_key, trouve["id"], marche.MARCHES_ETENDUS, use_cache
                    )
                    equipes_fiche = tuple(
                        ((fiche.get("match") or "").split(" - ", 1) + [""])[:2]
                    )
                    for operateur_bloc in riche.get("bookmakers") or []:
                        for bloc_marche in operateur_bloc.get("markets") or []:
                            for lib, prix in marche._traduire_marche(
                                bloc_marche, equipes_fiche
                            ).items():
                                # La MEILLEURE cote de chaque libelle, tous
                                # operateurs : c'est ce qu'un parieur obtient.
                                if prix > libelles.get(lib, 0.0):
                                    libelles[lib] = prix
                # Les cotes de l'agregateur priment : elles sont les meilleures
                # de plusieurs operateurs, la ou BetExplorer n'en donne qu'un
                # releve. Elles ne remplacent rien si elles sont absentes.
                meilleures.update(issues)
                if libelles:
                    try:
                        store.save_odds(
                            fiche["match_id"], libelles,
                            operateur="agregateur",
                            marche=marche.MARCHE_LIBELLES,
                        )
                    except ValueError:
                        pass
            elif agregat:
                non_apparies.append(fiche.get("match", ""))

            if not meilleures:
                continue
            try:
                store.save_odds(
                    fiche["match_id"], meilleures, operateur="betexplorer"
                )
                releves += 1
            except ValueError:
                pass
            prets.append(
                dict(
                    fiche,
                    cotes=meilleures,
                    # Les cotes saisies pour les AUTRES marches, s'il y en a.
                    # Elles ne viennent d'aucune source automatique : la page du
                    # jour de BetExplorer ne rend que le 1X2 cote serveur, et
                    # les pages par match chargent le reste en JavaScript.
                    # `cotes_courantes` et non `latest_odds` : les cotes par
                    # libelle sont saisies une par une, donc reparties sur
                    # plusieurs releves. Ne lire que le dernier en perdrait la
                    # plupart, silencieusement.
                    cotes_libelles=store.cotes_courantes(
                        fiche["match_id"], marche.MARCHE_LIBELLES
                    ),
                )
            )

    return {
        "fiches_en_attente": len(fiches),
        "fiches_cotees": len(prets),
        "releves_enregistres": releves,
        "incidents": incidents,
        # Les matchs que l'agregateur couvre peut-etre mais dont le nom n'a pas
        # ete reconnu : ils sont dits, jamais devines.
        "non_apparies": non_apparies,
        "hors_couverture": sorted(hors_couverture),
        # Une ligne par match : l'option la plus favorable. Un match dont trois
        # options sortent n'a pas trois fois plus de valeur qu'un autre.
        "par_match": marche.par_match(prets),
        "paris": marche.selection(prets),
    }


def run_backtest(matches: list[dict], tz_name: str, args) -> dict:
    """Rejoue les matchs termines avec coupure temporelle a leur coup d'envoi."""
    finished = [
        m
        for m in matches
        if m["statut"] == api_client.FINISHED and m.get("score_domicile") is not None
    ][:MAX_RANK_MATCHES]
    if len(matches) > len(finished):
        print(
            "  %d match(s) retenu(s) pour l'evaluation (termines uniquement)."
            % len(finished),
            file=sys.stderr,
        )

    cache_baselines: dict[str, Any] = {}

    def baseline_for(match: dict) -> dict | None:
        # Coupure a minuit plutot qu'au coup d'envoi : legerement plus stricte
        # (elle ecarte aussi les matchs plus tot dans la journee), donc toujours
        # sans fuite, mais partagee par tous les matchs d'un meme jour et d'une
        # meme competition. Sans cela, chaque match recalculerait dix historiques.
        cutoff = "%sT00:00:00+00:00" % match["date"]
        key = "%s|%s|%s" % (match["championnat"], match["pays"], cutoff)
        if key not in cache_baselines:
            try:
                cache_baselines[key] = api_client.league_baseline(
                    match, tz_name, not args.no_cache, before=cutoff,
                    # La reference detaillee (corners, tirs, cartons, xG) ne sert
                    # qu'au contexte : les criteres 4 et 11 s'appuient sur les
                    # forces par grandeur et sur la moyenne de xG de la
                    # competition. Sans elle, ils retomberaient sur la moyenne
                    # brute et on mesurerait une version affaiblie de ce qu'on
                    # cherche a mesurer. Elle coute une requete par match de la
                    # competition, d'ou l'activation par --contexte seulement.
                    with_stats=bool(getattr(args, "contexte", False)),
                )
            except api_client.ApiError:
                cache_baselines[key] = None
        return cache_baselines[key]

    with_context = bool(getattr(args, "contexte", False))

    def build(
        match: dict,
        collecte_aussi: bool = False,
        params: predict.Params = predict.DEFAULT_PARAMS,
    ) -> dict | None:
        try:
            form = api_client.get_form(
                match,
                count=api_client.FORM_DEFAULT,
                tz_name=tz_name,
                use_cache=not args.no_cache,
                # Les statistiques detaillees ne servent pas a l'evaluation des
                # issues, et couteraient une requete par match d'historique.
                # Le contexte, lui, en a besoin : le style de jeu et les xG s'y
                # lisent, et nulle part ailleurs.
                with_stats=collecte_aussi,
                before=match["kickoff_utc"],
            )
            collecte = None
            if collecte_aussi:
                # `retrospectif` coupe le classement et l'arbitre : sur un match
                # deja joue, le premier contient le resultat qu'on cherche a
                # prevoir et le second n'aurait pas ete connu avant. Les lire
                # ici donnerait une mesure flatteuse et fausse.
                collecte = context.collecter(
                    match, form, baseline_for(match), tz_name=tz_name,
                    use_cache=not args.no_cache, retrospectif=True,
                    avec_systemes=0, avec_arbitre=False,
                )
            # `with_candidates` : l'evaluation mesure la calibration de toutes
            # les propositions envisageables, pas seulement des trois retenues.
            return predict.build(
                match, form, baseline_for(match), with_candidates=True,
                collecte=collecte, params=params,
            )
        except (api_client.ApiError, predict.NotEnoughData):
            return None

    def actual_metrics(match: dict) -> dict[str, tuple[float, float]] | None:
        """Valeurs reelles par grandeur, pour juger corners, tirs et cartons.

        Lues dans les statistiques deja attachees au match : elles n'y sont que
        si --stats a ete demande, et on ne les va pas les chercher ici -- ce
        serait une requete par match, a l'insu de l'utilisateur.
        """
        values: dict[str, tuple[float, float]] = {
            "Buts": (
                float(match["score_domicile"]), float(match["score_exterieur"])
            )
        }
        stats = match.get("stats")
        if not stats:
            return values
        for metric in predict.METRICS:
            if metric.field is None:
                continue
            home = api_client.stat_number(stats["domicile"].get(metric.field))
            away = api_client.stat_number(stats["exterieur"].get(metric.field))
            if home is not None and away is not None:
                values[metric.label] = (home, away)
        return values

    # Un banc d'essai qui tourne sur des notes perimees mesure autre chose que
    # ce qui est en service : il faut le dire avant de lire les chiffres.
    retard = forces.avertissement()
    if retard:
        print("Attention : %s\n" % retard, file=sys.stderr)

    report = backtest.run(finished, build, baseline_for, actual_metrics)

    if getattr(args, "regler", None):
        # Meme protocole que la comparaison avec/sans contexte : les reglages
        # voient exactement les memes rencontres, avec le meme historique et la
        # meme coupure. Un reglage juge sur d'autres matchs qu'un autre ne se
        # compare a rien.
        cle, candidats = parse_reglages(args.regler)
        report["reglages"] = {
            "cle": cle,
            "classement": backtest.tune(
                finished,
                lambda match, params: build(match, with_context, params),
                candidats,
            ),
        }

    if with_context:
        # Comparaison appariee : les deux variantes voient exactement les memes
        # rencontres, avec le meme historique et la meme coupure. C'est le seul
        # protocole qui permette de dire si le contexte apporte quelque chose,
        # plutot que de comparer deux moyennes sur deux echantillons.
        report["contexte"] = backtest.tune(
            finished,
            lambda match, mode: build(match, mode == "avec contexte"),
            ["sans contexte", "avec contexte"],
        )
    return report


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.clear_cache:
        removed = cache.clear()
        print("Cache vide : %d fichier(s) supprime(s)." % removed)
        return 0

    if args.list_leagues:
        print("\n  Competitions couvertes par le mode journee (--league / --round) :\n")
        for canonical, (league_id, label) in sorted(api_client.TSDB_LEAGUES.items()):
            aliases = sorted(
                alias for alias, target in api_client.TSDB_ALIASES.items()
                if target == canonical
            )
            suffix = "  [alias: %s]" % ", ".join(aliases) if aliases else ""
            print("  %-18s %-28s id=%d%s" % (canonical, label, league_id, suffix))
        print()
        return 0

    if args.cote:
        import marche
        import store

        for brut in args.cote:
            identifiant, _, reste = brut.partition(":")
            libelle, _, valeur = reste.rpartition("=")
            libelle = libelle.strip().strip('"').strip("'")
            if not identifiant or not libelle or not valeur:
                print(
                    "Erreur : format attendu MATCH_ID:LIBELLE=COTE. Recu : %r"
                    % brut,
                    file=sys.stderr,
                )
                return 2
            try:
                releve = store.save_odds(
                    identifiant.strip(),
                    {libelle: float(valeur)},
                    operateur="saisie",
                    marche=marche.MARCHE_LIBELLES,
                )
            except (ValueError, TypeError) as exc:
                print("Erreur : %s" % exc, file=sys.stderr)
                return 2
            print(
                "  %s  %s = %s  (releve le %s)"
                % (identifiant, libelle, valeur, releve["releve_le"])
            )
        return 0

    if args.valeur:
        import marche
        import store

        _, tz_name = api_client.resolve_settings(args.provider, args.tz)
        print(
            exporter.render_selection(
                paris_de_valeur(
                    tz_name, not args.no_cache,
                    etendus=bool(getattr(args, "marches_etendus", False)),
                )
            )
        )
        return 0

    if args.bilan:
        # Lit la base et rien d'autre : ni date, ni reseau, ni cle. C'est ce
        # qui permet de le lancer n'importe quand, y compris quand la source du
        # jour est indisponible.
        import criteres
        import marche
        import verify as _verify

        # Les propositions emises d'abord : c'est ce que le lecteur a
        # reellement engage, et la seule epreuve qui porte sur du vecu plutot
        # que sur des matchs rejoues.
        print(exporter.render_options(_verify.bilan_par_option()))
        print(exporter.render_bilan(marche.bilan(), criteres.bilan()))
        return 0

    if args.round is not None and not args.league:
        print("Erreur : --round necessite --league.", file=sys.stderr)
        return 2

    if args.output and args.export == "both":
        print(
            "Erreur : --output ne peut pas etre utilise avec --export both "
            "(deux fichiers seraient ecrits au meme chemin).",
            file=sys.stderr,
        )
        return 2

    try:
        matches, title, hint = collect(args)
    except api_client.ApiError as exc:
        print("Erreur : %s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrompu.", file=sys.stderr)
        return 130

    # Le filtre par equipe s'applique apres coup : il vaut pour le mode journee
    # comme pour le mode date, et aucune source ne sait filtrer cote serveur.
    if args.team:
        narrowed = api_client.filter_by_team(matches, args.team, args.all_teams)
        # Les declinaisons ecartees sont signalees, jamais supprimees en
        # silence : sans cela, un jour ou seule la reserve joue, l'utilisateur
        # verrait "aucun match" sans savoir pourquoi.
        skipped = 0
        if not args.all_teams:
            skipped = len(
                api_client.filter_by_team(matches, args.team, True)
            ) - len(narrowed)
            if skipped:
                print(
                    "  Note : %d match(s) ecarte(s) (equipe feminine, de jeunes "
                    "ou reserve). --all-teams pour les inclure." % skipped
                )
        if matches and not narrowed:
            if skipped:
                hint = (
                    "  Astuce : les seuls matchs correspondant a %r ce jour-la sont\n"
                    "  ceux d'une equipe feminine, de jeunes ou reserve. Relancez\n"
                    "  avec --all-teams pour les voir.\n" % args.team
                )
            else:
                hint = (
                    "  Astuce : aucune equipe ne correspond a %r parmi les %d match(s)\n"
                    "  de cette selection. Le filtre est une sous-chaine : essayez un\n"
                    "  fragment plus court, ou verifiez l'orthographe du club.\n"
                    % (args.team, len(matches))
                )
        matches = narrowed
        title = "%s - %s" % (args.team, title)

    if not args.quiet:
        print(exporter.render_console(matches, title))
        if hint:
            print(hint)

    if not matches:
        return 0

    if args.stats:
        warnings = enrich_with_stats(matches, args.max_stats, not args.no_cache)
        if not args.quiet:
            print(exporter.render_stats(matches))
        for warning in warnings:
            print(warning, file=sys.stderr)

    # --predict a besoin de la forme et des statistiques detaillees : on les
    # active plutot que de refuser la commande pour une option manquante.
    ranking = args.top is not None
    want_form = args.form is not None or args.predict or ranking
    if want_form:
        _, tz_name = api_client.resolve_settings(args.provider, args.tz)
        count = args.form if args.form is not None else api_client.FORM_DEFAULT

        studied = matches
        if ranking:
            # Une prevision ne vaut que pour un match a venir : les matchs joues
            # ou en cours sont ecartes du classement.
            studied = [m for m in matches if m["statut"] == api_client.SCHEDULED]
            if not studied:
                print(
                    "  Aucun match a venir dans cette selection : rien a classer.",
                    file=sys.stderr,
                )
            if len(studied) > MAX_RANK_MATCHES:
                print(
                    "  %d matchs a venir, les %d premiers sont analyses."
                    % (len(studied), MAX_RANK_MATCHES),
                    file=sys.stderr,
                )
                studied = studied[:MAX_RANK_MATCHES]

        limit = MAX_RANK_MATCHES if ranking else MAX_FORM_MATCHES
        warnings = enrich_with_form(
            studied,
            count,
            tz_name,
            not args.no_cache,
            args.stats or args.predict or ranking,
            limit,
        )
        # --quiet supprime le tableau des matchs, pas une sortie explicitement
        # demandee : --form, --predict et --top affichent toujours leur bloc.
        if args.form is not None:
            print(exporter.render_form(matches))
        for warning in warnings:
            print(warning, file=sys.stderr)

    if args.predict or ranking:
        for warning in build_predictions(
            studied, tz_name, not args.no_cache, not args.sans_contexte
        ):
            print(warning, file=sys.stderr)
        if ranking:
            print(exporter.render_ranking(studied, args.top))
        else:
            print(exporter.render_prediction(matches))

    if args.backtest:
        _, tz_name = api_client.resolve_settings(args.provider, args.tz)
        print(exporter.render_backtest(run_backtest(matches, tz_name, args)))

    if args.open:
        for warning in open_in_browser(matches):
            print(warning, file=sys.stderr)

    if args.export in ("csv", "both"):
        path = exporter.to_csv(matches, args.date, args.output)
        print("CSV  ecrit : %s" % path)
    if args.export in ("json", "both"):
        path = exporter.to_json(matches, args.date, args.output)
        print("JSON ecrit : %s" % path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
