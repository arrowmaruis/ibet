"""Tests du parseur de statistiques, sans appel reseau.

Le chemin API-Football ne peut pas etre teste en conditions reelles sans cle.
On verifie donc le parsing sur une reponse conforme au format documente de
`/fixtures?id=`, pour garantir que corners, fautes, cartons et arbitre seront
correctement extraits des que la cle sera renseignee.

Usage : python -m ibet tests
"""

from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ibet.evaluation import verify
from ibet.interfaces import exporter
from ibet.prevision import context, forces, forecast, marche, predict
from ibet.sources import api_client
from ibet.stockage import store

FIXTURE_RESPONSE = {
    "errors": [],
    "response": [
        {
            "fixture": {"id": 1550095, "referee": "Daniele Chiffi, Italy"},
            "teams": {"home": {"name": "Udinese"}, "away": {"name": "Como"}},
            "statistics": [
                {
                    "team": {"name": "Udinese"},
                    "statistics": [
                        {"type": "Shots on Goal", "value": 3},
                        {"type": "Total Shots", "value": 16},
                        {"type": "Corner Kicks", "value": 5},
                        {"type": "Fouls", "value": 14},
                        {"type": "Yellow Cards", "value": 2},
                        {"type": "Red Cards", "value": None},
                        {"type": "Ball Possession", "value": "45%"},
                        {"type": "Offsides", "value": 3},
                    ],
                },
                {
                    "team": {"name": "Como"},
                    "statistics": [
                        {"type": "Shots on Goal", "value": 6},
                        {"type": "Total Shots", "value": 20},
                        {"type": "Corner Kicks", "value": 8},
                        {"type": "Fouls", "value": 11},
                        {"type": "Yellow Cards", "value": 3},
                        {"type": "Red Cards", "value": 1},
                        {"type": "Ball Possession", "value": "55%"},
                    ],
                },
            ],
        }
    ],
}

MATCH = {
    "provider": "thesportsdb",
    "match_id": "2482138",
    "api_football_id": "1550095",
    "domicile": "Udinese",
    "exterieur": "Como",
    "score_domicile": 1,
    "score_exterieur": 1,
    "statut": api_client.FINISHED,
    "date": "2026-08-22",
    "heure": "18:30",
    "championnat": "Italian Serie A",
    "pays": "Italy",
    "kickoff_utc": "2026-08-22T16:30:00",
    "journee": "1",
}

# --------------------------------------------------------------------------
# Fixtures Flashscore
#
# Le flux est un texte delimite ; on le reconstruit avec les separateurs exposes
# par le module plutot qu'avec des litteraux non-ASCII, que la console Windows
# et les editeurs mal configures abimeraient silencieusement.
# --------------------------------------------------------------------------

# Le flux ne s'adresse que par decalage en jours autour d'aujourd'hui : une date
# figee ferait echouer le test des qu'elle sortirait de la fenetre de +/-7 jours.
FS_DATE = datetime.now(ZoneInfo("Europe/Paris")).date().isoformat()
FS_PREVIOUS_DATE = (
    datetime.now(ZoneInfo("Europe/Paris")).date() - timedelta(days=1)
).isoformat()


def _fs_block(**fields: str) -> str:
    return api_client.FS_FIELD.join(
        key + api_client.FS_KV + value for key, value in fields.items()
    )


def _fs_feed(*blocks: str) -> str:
    return api_client.FS_BLOCK.join(blocks)


def _fs_ts(date: str, hour: str) -> str:
    """Timestamp unix d'une heure locale Europe/Paris, comme le renvoie le flux."""
    naive = datetime.strptime(date + " " + hour, "%Y-%m-%d %H:%M")
    return str(int(naive.replace(tzinfo=ZoneInfo("Europe/Paris")).timestamp()))


FS_DAY_FEED = _fs_feed(
    _fs_block(SA="1"),
    _fs_block(ZA="FRANCE: Ligue 1", ZY="France"),
    # Termine, avec score.
    _fs_block(
        AA="m1", AD=_fs_ts(FS_DATE, "17:15"), AB="3", AC="3",
        AE="Lens", AF="Lorient", AG="2", AH="1",
    ),
    # A venir, sans score.
    _fs_block(
        AA="m2", AD=_fs_ts(FS_DATE, "20:45"), AB="1", AC="1",
        AE="Nice", AF="Le Mans",
    ),
    # Reporte : statut global "termine", statut detaille 4, aucun score.
    _fs_block(
        AA="m3", AD=_fs_ts(FS_DATE, "21:00"), AB="3", AC="4",
        AE="Brest", AF="Le Havre",
    ),
    # Debordement du flux sur la veille : doit etre filtre.
    _fs_block(ZA="TUNISIE: Ligue 1", ZY="Tunisie"),
    _fs_block(
        AA="m4", AD=_fs_ts(FS_PREVIOUS_DATE, "23:30"), AB="3", AC="3",
        AE="CA Bizertin", AF="Olympique Beja", AG="0", AH="0",
    ),
)

FS_STATS_FEED = _fs_feed(
    _fs_block(SE="Match"),
    _fs_block(SF="Top stats"),
    _fs_block(SD="12", SG="Possession de balle", SH="47%", SI="53%"),
    _fs_block(SD="13", SG="Tirs cadres", SH="3", SI="6"),
    _fs_block(SD="34", SG="Tirs totaux", SH="15", SI="12"),
    _fs_block(SD="16", SG="Corners", SH="10", SI="5"),
    _fs_block(SD="21", SG="Fautes", SH="10", SI="15"),
    _fs_block(SD="23", SG="Cartons jaunes", SH="2", SI="2"),
    # Deuxieme periode : memes libelles, valeurs partielles, doit etre ignoree.
    _fs_block(SE="1. mi-temps"),
    _fs_block(SD="21", SG="Fautes", SH="4", SI="7"),
    _fs_block(SD="16", SG="Corners", SH="1", SI="2"),
)

# Flux "confrontations" : deux groupes de derniers matchs, puis les sections par
# lieu, qui repetent les memes matchs et ne doivent pas etre comptees deux fois.
FS_FORM_FEED = _fs_feed(
    _fs_block(SA="1"),
    _fs_block(KA="Global", IS=""),
    _fs_block(KB="Derniers matchs: Gladbach"),
    # Le match consulte lui-meme : doit etre exclu de sa propre forme.
    _fs_block(
        KC=_fs_ts(FS_DATE, "15:30"), KP="THISMATCH", KF="Bundesliga", AC="3",
        KJ="Gladbach", KK="*Elversberg", KU="3", KT="4", WIS="l", KS="home",
    ),
    _fs_block(
        KC=_fs_ts(FS_PREVIOUS_DATE, "18:30"), KP="m10", KF="Bundesliga", AC="3",
        KI="BUN", KJ="*RB Leipzig", KK="Gladbach", KU="3", KT="0",
        WIS="l", KS="away",
    ),
    _fs_block(
        KC=_fs_ts(FS_PREVIOUS_DATE, "16:00"), KP="m11", KF="Amical Club", AC="3",
        KI="AMI", KJ="*Gladbach", KK="SSVg Velbert", KU="8", KT="0",
        WIS="w", KS="home",
    ),
    # Match a venir : ne compte pas dans la forme.
    _fs_block(
        KC=_fs_ts(FS_DATE, "20:00"), KP="m12", KF="Bundesliga", AC="1",
        KJ="Gladbach", KK="Mayence", WIS="", KS="home",
    ),
    _fs_block(KB="Derniers matchs: Elversberg"),
    _fs_block(
        KC=_fs_ts(FS_PREVIOUS_DATE, "18:30"), KP="m20", KF="Bundesliga", AC="3",
        KI="BUN", KJ="*Elversberg", KK="Leverkusen", KU="3", KT="2",
        WIS="w", KS="home",
    ),
    # Groupe sans nom d'equipe : ignore.
    _fs_block(KB="Confrontations"),
    _fs_block(
        KC=_fs_ts(FS_PREVIOUS_DATE, "12:00"), KP="m99", KF="Bundesliga", AC="3",
        KJ="Gladbach", KK="Elversberg", KU="1", KT="1", KS="home",
    ),
    # Section par lieu : repete les memes matchs, doit etre ignoree.
    _fs_block(KA="Gladbach - Domicile"),
    _fs_block(KB="Derniers matchs: Gladbach"),
    _fs_block(
        KC=_fs_ts(FS_PREVIOUS_DATE, "10:00"), KP="m30", KF="Bundesliga", AC="3",
        KJ="*Gladbach", KK="Hoffenheim", KU="4", KT="0", WIS="w", KS="home",
    ),
)

failures: list[str] = []


def check(label: str, got, expected) -> None:
    if got == expected:
        print("  ok    %-34s %r" % (label, got))
    else:
        print("  FAIL  %-34s attendu %r, obtenu %r" % (label, expected, got))
        failures.append(label)


def main(argv: list[str] | None = None) -> int:
    # Sans reseau : l'horloge ne va pas mesurer son ecart sur Internet.
    from ibet.sources import horloge as _horloge
    _horloge.fixer(0.0)
    calls: list[dict] = []

    def fake_request(url, headers, params):
        calls.append({"url": url, "params": params})
        return FIXTURE_RESPONSE

    api_client._request = fake_request
    os.environ["API_FOOTBALL_KEY"] = "cle-de-test"

    stats = api_client.get_stats(MATCH, use_cache=False)

    print("\n1. Extraction depuis API-Football")
    check("source", stats["source"], "api-football")
    check("arbitre", stats["arbitre"], "Daniele Chiffi, Italy")
    check("corners domicile", stats["domicile"]["corners"], 5)
    check("corners exterieur", stats["exterieur"]["corners"], 8)
    check("fautes domicile", stats["domicile"]["fautes"], 14)
    check("fautes exterieur", stats["exterieur"]["fautes"], 11)
    check("cartons jaunes domicile", stats["domicile"]["cartons_jaunes"], 2)
    check("cartons jaunes exterieur", stats["exterieur"]["cartons_jaunes"], 3)
    check("cartons rouges domicile", stats["domicile"]["cartons_rouges"], None)
    check("cartons rouges exterieur", stats["exterieur"]["cartons_rouges"], 1)
    check("tirs cadres domicile", stats["domicile"]["tirs_cadres"], 3)
    check("tirs cadres exterieur", stats["exterieur"]["tirs_cadres"], 6)
    check("possession domicile", stats["domicile"]["possession"], "45%")

    print("\n2. Requete emise")
    check("une seule requete", len(calls), 1)
    check("fixture interrogee", calls[0]["params"], {"id": "1550095"})

    print("\n3. Aplatissement CSV")
    MATCH["stats"] = stats
    flat = exporter.flatten_stats(MATCH)
    check("arbitre", flat["arbitre"], "Daniele Chiffi, Italy")
    check("corners_dom", flat["corners_dom"], 5)
    check("corners_ext", flat["corners_ext"], 8)
    check("cartons_jaunes_ext", flat["cartons_jaunes_ext"], 3)
    check("colonnes generees", len(exporter.stat_columns()), 15)

    print("\n4. Rendu console")
    rendered = exporter.render_stats([MATCH])
    for needle in ["Daniele Chiffi", "Corners", "Fautes", "Cartons jaunes"]:
        check("contient %r" % needle, needle in rendered, True)

    print("\n5. Parsing du flux Flashscore (matchs)")
    api_client._request_text = lambda url, headers: FS_DAY_FEED
    fs = api_client._fetch_flashscore(FS_DATE, "Europe/Paris")
    check("matchs de la date demandee", len(fs), 3)
    check("veille ecartee", [m["date"] for m in fs], [FS_DATE] * 3)
    check("championnat sans prefixe pays", fs[0]["championnat"], "Ligue 1")
    check("pays", fs[0]["pays"], "France")
    check("domicile", fs[0]["domicile"], "Lens")
    check("score termine", (fs[0]["score_domicile"], fs[0]["score_exterieur"]), (2, 1))
    check("statut termine", fs[0]["statut"], api_client.FINISHED)
    check("statut a venir", fs[1]["statut"], api_client.SCHEDULED)
    check("score a venir absent", fs[1]["score_domicile"], None)
    check("statut reporte (AC=4)", fs[2]["statut"], api_client.POSTPONED)

    print("\n6. Parsing des statistiques Flashscore")
    api_client._request_text = lambda url, headers: FS_STATS_FEED
    fstats = api_client.get_stats(
        {"provider": "flashscore", "match_id": "abcd1234"}, use_cache=False
    )
    check("source", fstats["source"], "flashscore")
    check("tirs cadres domicile", fstats["domicile"]["tirs_cadres"], 3)
    check("tirs total exterieur", fstats["exterieur"]["tirs_total"], 12)
    check("corners domicile", fstats["domicile"]["corners"], 10)
    check("possession exterieur", fstats["exterieur"]["possession"], "53%")
    # La 2e periode du flux porte les memes libelles : elle ne doit pas ecraser
    # le total du match.
    check("mi-temps ignoree", fstats["domicile"]["fautes"], 10)
    # Ligne absente du flux, mais les cartons jaunes sont suivis -> zero.
    check("cartons rouges deduits", fstats["domicile"]["cartons_rouges"], 0)
    check("aucune stat manquante", fstats["indisponible"], [])

    print("\n7. Barres comparatives et filtre par equipe")
    left, right = exporter._bars(10, 5)
    check("barre 10 vs 5 (2/3 - 1/3)", (left.count("#"), right.count("#")), (8, 4))
    check("barre alignee sur l'axe", (left[-1], right[0]), ("#", "#"))
    left, right = exporter._bars(0, 0)
    check("total nul -> aucune barre", (left.strip(), right.strip()), ("", ""))
    left, right = exporter._bars(None, 7)
    check("valeur manquante -> aucune barre", left.strip(), "")
    left, right = exporter._bars(1, 200)
    check("minorite visible malgre l'ecart", left.count("#"), 1)
    left, right = exporter._bars("47%", "53%")
    check("pourcentages acceptes", (left.count("#") > 0, right.count("#") > 0), (True, True))

    teams = [
        {"domicile": "Boulogne", "exterieur": "Dijon"},
        {"domicile": "Laval", "exterieur": "Red Star"},
    ]
    for row in teams:
        row.setdefault("championnat", "Ligue 2")
    check("filtre equipe a domicile", len(api_client.filter_by_team(teams, "boulogne")), 1)
    check("filtre equipe a l'exterieur", len(api_client.filter_by_team(teams, "Red")), 1)
    check("filtre equipe inconnue", api_client.filter_by_team(teams, "Nantes"), [])

    # Declinaisons d'un club : ecartees sauf si la recherche les vise.
    for name, expected in (
        ("Genoa F", True), ("Dortmund II", True), ("FC Porto B", True),
        ("Columbus Crew 2", True), ("Itabaiana -20", True), ("Angleterre -17 F", True),
        ("Genoa", False), ("Boulogne", False), ("Red Star", False),
        ("Bayern Munich", False), ("Le Havre", False),
    ):
        check("variante %-18r" % name, api_client.is_variant_team(name), expected)
    check(
        "variante signalee par la competition",
        api_client.is_variant_team("Bologne", "Bundesliga - Femmes"),
        True,
    )

    club = [
        {"domicile": "B. Monchengladbach", "exterieur": "Elversberg",
         "championnat": "Bundesliga"},
        {"domicile": "B. Monchengladbach II", "exterieur": "Dortmund II",
         "championnat": "Regionalliga West"},
        {"domicile": "Bologne F", "exterieur": "Monchengladbach F",
         "championnat": "Bundesliga - Femmes"},
    ]
    check(
        "recherche simple -> equipe premiere",
        [m["domicile"] for m in api_client.filter_by_team(club, "Monchengladbach")],
        ["B. Monchengladbach"],
    )
    check(
        "recherche explicite de la reserve",
        len(api_client.filter_by_team(club, "Monchengladbach II")),
        1,
    )
    check(
        "include_variants rend tout",
        len(api_client.filter_by_team(club, "Monchengladbach", True)),
        3,
    )

    print("\n8. Forme recente (flux confrontations)")
    api_client._request_text = lambda url, headers: FS_FORM_FEED
    form = api_client.get_form(
        {"provider": "flashscore", "match_id": "THISMATCH",
         "domicile": "Gladbach", "exterieur": "Elversberg"},
        count=10, use_cache=False,
    )
    dom, ext = form["domicile"], form["exterieur"]
    check("equipe a domicile", dom["equipe"], "Gladbach")
    check("equipe a l'exterieur", ext["equipe"], "Elversberg")
    check("match consulte exclu", [e["match_id"] for e in dom["matchs"]], ["m10", "m11"])
    check("match a venir exclu", all(e["match_id"] != "m12" for e in dom["matchs"]), True)
    check("sections par lieu ignorees", all(e["match_id"] != "m30" for e in dom["matchs"]), True)
    check("confrontations ignorees", all(e["match_id"] != "m99" for e in dom["matchs"]), True)
    # m10 : defaite 0-3 a Leipzig. m11 : victoire 8-0 en amical.
    check("bilan", (dom["bilan"]["V"], dom["bilan"]["N"], dom["bilan"]["D"]), (1, 0, 1))
    check("amicaux reperes", dom["amicaux"], 1)
    # Defaite 0-3 a l'exterieur : les buts sont vus depuis l'equipe suivie.
    defeat = dom["matchs"][0]
    check("lieu", defeat["lieu"], "exterieur")
    check("buts pour/contre inverses a l'ext.", (defeat["buts_pour"], defeat["buts_contre"]), (0, 3))
    check("adversaire sans asterisque", defeat["adversaire"], "RB Leipzig")
    check("buts cumules", (dom["buts_pour"], dom["buts_contre"]), (8, 3))
    check("2e equipe parsee", [e["match_id"] for e in ext["matchs"]], ["m20"])

    rendered = exporter.render_form([
        {"domicile": "Gladbach", "exterieur": "Elversberg", "form": form}
    ])
    for needle in ["Forme recente", "Gladbach", "RB Leipzig", "(A)", "amical"]:
        check("rendu contient %r" % needle, needle in rendered, True)

    print("\n9. Modele de prevision (valeurs verifiables a la main)")
    # P(X=0) pour Poisson(2) vaut exp(-2) = 0.135335...
    check("poisson_pmf(0, 2)", round(predict.poisson_pmf(0, 2.0), 6), 0.135335)
    # P(X=2) pour Poisson(2) = exp(-2)*4/2 = 2*exp(-2) = 0.270671
    check("poisson_pmf(2, 2)", round(predict.poisson_pmf(2, 2.0), 6), 0.270671)
    check(
        "distribution sommant a 1",
        round(sum(predict.poisson_distribution(2.0, 30)), 6),
        1.0,
    )
    # Deux lambdas egaux : par symetrie, victoire domicile et exterieur egales.
    sym = predict.outcome_probabilities(1.5, 1.5)
    check(
        "symetrie a lambdas egaux",
        round(sym["domicile"] - sym["exterieur"], 9),
        0.0,
    )
    check(
        "probabilites sommant a 1",
        round(sym["domicile"] + sym["nul"] + sym["exterieur"], 9),
        1.0,
    )
    # Les deux marquent : (1 - e^-1.5)^2 = 0.77686984^2 = 0.60352676
    check(
        "les deux marquent",
        round(predict.both_teams_score(1.5, 1.5), 6),
        0.603527,
    )
    # Total ~ Poisson(3). P(total > 2.5) = 1 - P(0) - P(1) - P(2)
    expected_over = 1 - sum(predict.poisson_pmf(k, 3.0) for k in range(3))
    # Compare a 7 decimales : la probabilite est sommee sur la matrice jointe,
    # tronquee a MAX_EVENTS buts par equipe. L'ecart avec la valeur analytique
    # vaut 7e-9 (nul si la matrice est elargie), soit tres au-dela de ce qui
    # compte pour un score de football.
    check(
        "P(plus de 2.5 buts)",
        round(predict.over_probability(1.5, 1.5, 2.5), 7),
        round(expected_over, 7),
    )

    # Propriete du modele de Maher : deux equipes exactement moyennes doivent
    # redonner les moyennes de la competition, sans deformation.
    baseline = {
        "competition": "Test",
        "matchs": 100,
        "moyenne_domicile": 1.9,
        "moyenne_exterieur": 1.4,
        "moyenne_globale": 1.65,
    }
    # Un echantillon enorme annule la regularisation : on retrouve Maher pur.
    huge = 10 ** 6
    lam_home, lam_away = predict._maher_lambdas(
        1.65, 1.65, 1.65, 1.65, baseline, huge, huge
    )
    check("Maher, equipes moyennes -> mu_dom", round(lam_home, 4), 1.9)
    check("Maher, equipes moyennes -> mu_ext", round(lam_away, 4), 1.4)
    # Une attaque deux fois meilleure double le nombre de buts attendus.
    lam_home, _ = predict._maher_lambdas(3.30, 1.65, 1.65, 1.65, baseline, huge, huge)
    check("Maher, attaque doublee", round(lam_home, 4), 3.8)

    # Regularisation : (n * force + k) / (n + k). La valeur de k vit dans
    # predict.SHRINKAGE, seule source : le test la lit la aussi, sinon il
    # cesserait de tester le code reellement execute.
    k = predict.SHRINKAGE
    check("shrink sans echantillon -> moyenne", predict._shrink(2.0, 0), 1.0)
    check(
        "shrink a n = k -> milieu",
        round(predict._shrink(2.0, k), 6),
        1.5,
    )
    check(
        "shrink pilote par le parametre",
        (round(predict._shrink(2.0, 4, 2.0), 4), round(predict._shrink(2.0, 4, 16.0), 4)),
        (1.6667, 1.2),
    )
    check("shrink a n enorme -> inchange", round(predict._shrink(2.0, huge), 4), 2.0)
    check("shrink d'une force moyenne -> 1", round(predict._shrink(1.0, 4), 6), 1.0)
    # Sur petit echantillon, la regularisation doit reduire l'ecart au centre.
    raw_home, _ = predict._maher_lambdas(3.30, 1.65, 1.65, 1.65, baseline, huge, huge)
    small_home, _ = predict._maher_lambdas(3.30, 1.65, 1.65, 1.65, baseline, 4, 4)
    check("petit echantillon -> prevision moins extreme", small_home < raw_home, True)

    # Le perimetre doit etre le meme pour les deux equipes, ou aucun des deux.
    league = [{"competition": "L1"}] * 5
    mixed = [{"competition": "L1"}] * 2 + [{"competition": "Coupe"}] * 3
    home_scope, away_scope, scoped = predict._scope(league, mixed, "L1")
    check("perimetre commun refuse le melange", scoped, False)
    check("perimetre commun : tout garde", (len(home_scope), len(away_scope)), (5, 5))
    home_scope, away_scope, scoped = predict._scope(league, league, "L1")
    check("perimetre commun accepte si les deux ont assez", scoped, True)

    check(
        "amicaux ecartes du calcul",
        len(predict._official([{"amical": True}, {"amical": False}])),
        1,
    )

    # Correction du niveau des adversaires. Forces mesurees sur un echantillon
    # enorme pour que la regularisation ne les deplace pas.
    stable = 10 ** 6
    strong = {"attaque": 2.0, "defense": 0.5, "matchs": stable}   # forte partout
    weak = {"attaque": 0.5, "defense": 2.0, "matchs": stable}     # faible partout
    graded = {"Costaud": strong, "Faiblard": weak}

    # 3 buts contre une defense deux fois trop permissive n'en valent que 1.5.
    scored, conceded, fixed, weight = predict._adjusted_averages(
        [{"buts_pour": 3, "buts_contre": 0, "adversaire": "Faiblard"}], None, graded
    )
    check("buts devalues contre un faible", round(scored, 4), 1.5)
    check("matchs corriges comptes", fixed, 1)

    # Les memes 3 buts contre une bonne defense en valent 6.
    scored, _, _, _ = predict._adjusted_averages(
        [{"buts_pour": 3, "buts_contre": 0, "adversaire": "Costaud"}], None, graded
    )
    # 3 / 0.5 = 6, a la precision pres du retrecissement applique a la force de
    # l'adversaire (neutre ici, son echantillon etant enorme).
    check("buts valorises contre un fort", round(scored, 3), 6.0)

    # 2 buts encaisses contre une attaque deux fois trop forte n'en valent que 1.
    _, conceded, _, _ = predict._adjusted_averages(
        [{"buts_pour": 0, "buts_contre": 2, "adversaire": "Costaud"}], None, graded
    )
    check("buts encaisses relativises", round(conceded, 4), 1.0)

    # Adversaire hors championnat : aucune force connue, valeur brute conservee.
    scored, conceded, fixed, weight = predict._adjusted_averages(
        [{"buts_pour": 3, "buts_contre": 1, "adversaire": "Inconnu"}], None, graded
    )
    check("adversaire inconnu -> brut", (scored, conceded, fixed), (3.0, 1.0, 0))
    check("effectif efficace sans date", weight, 1.0)
    check(
        "sans reference -> brut",
        predict._adjusted_averages(
            [{"buts_pour": 3, "buts_contre": 1, "adversaire": "Costaud"}], None, None
        ),
        (3.0, 1.0, 0, 1.0),
    )

    print("\n10. Perimetre, ponderation et facteur de Dixon-Coles")
    # Le flux du jour suffixe la phase, les historiques non : compares tels
    # quels, les deux ne coincidaient jamais dans les championnats a phases, et
    # la restriction au perimetre commun ne s'appliquait donc jamais la ou elle
    # etait demandee.
    phased = [{"competition": "Liga Profesional"}] * 5
    _, _, scoped = predict._scope(phased, phased, "Liga Profesional - Cloture")
    check("perimetre insensible au suffixe de phase", scoped, True)

    # Ponderation par anciennete : a la demi-vie le match compte pour moitie,
    # au double pour un quart.
    kickoff = "2026-09-06T20:00:00+00:00"
    half = 21.0

    def aged(days):
        stamp = datetime.fromisoformat(kickoff) - timedelta(days=days)
        return {"kickoff_utc": stamp.isoformat()}

    check("match du jour -> poids 1", predict._recency_weight(aged(0), kickoff, half), 1.0)
    check(
        "match a la demi-vie -> poids 1/2",
        round(predict._recency_weight(aged(half), kickoff, half), 6),
        0.5,
    )
    check(
        "match a deux demi-vies -> poids 1/4",
        round(predict._recency_weight(aged(2 * half), kickoff, half), 6),
        0.25,
    )
    check(
        "demi-vie nulle -> aucune ponderation",
        predict._recency_weight(aged(365), kickoff, 0.0),
        1.0,
    )
    check("match sans date -> poids 1", predict._recency_weight({}, kickoff, half), 1.0)
    # La ponderation est desactivee par defaut : c'est le reglage retenu, et un
    # test qui la supposerait active masquerait un changement de defaut.
    check("ponderation desactivee par defaut", predict.DEFAULT_PARAMS.half_life, 0.0)

    old_first = [
        dict(aged(4 * half), buts_pour=3, buts_contre=0, adversaire=""),
        dict(aged(0), buts_pour=1, buts_contre=0, adversaire=""),
    ]
    weighted, _, _, effective = predict._adjusted_averages(
        old_first, None, {}, None, predict.Params(0.0, half, 10.0), kickoff
    )
    check("moyenne tiree vers le match recent", weighted < 2.0, True)
    check("effectif efficace < nombre de matchs", effective < 2.0, True)
    check(
        "sans ponderation -> moyenne simple",
        round(
            predict._adjusted_averages(
                old_first, None, {}, None, predict.Params(0.0, 0.0, 10.0), kickoff
            )[0],
            6,
        ),
        2.0,
    )

    # Facteur tau de Dixon & Coles : identite a rho = 0, et il ne touche que les
    # quatre petits scores.
    check("tau neutre a rho = 0", predict.dixon_coles_tau(0, 0, 1.5, 1.2, 0.0), 1.0)
    check("tau hors des quatre cases", predict.dixon_coles_tau(2, 1, 1.5, 1.2, -0.05), 1.0)
    check("tau en 1-1", round(predict.dixon_coles_tau(1, 1, 1.5, 1.2, -0.05), 6), 1.05)
    check("tau en 0-0", round(predict.dixon_coles_tau(0, 0, 1.5, 1.2, -0.05), 6), 1.09)
    check("tau en 0-1", round(predict.dixon_coles_tau(0, 1, 1.5, 1.2, -0.05), 6), 0.925)
    check("tau en 1-0", round(predict.dixon_coles_tau(1, 0, 1.5, 1.2, -0.05), 6), 0.94)
    # Un rho negatif rehausse les nuls serres : c'est tout l'objet du facteur.
    plain = predict.outcome_probabilities(1.5, 1.2, 0.0)
    corrected = predict.outcome_probabilities(1.5, 1.2, -0.05)
    check("rho negatif -> plus de nuls", corrected["nul"] > plain["nul"], True)
    check("issues corrigees sommant a 1", round(sum(corrected.values()), 9), 1.0)
    check("rho nul par defaut", predict.DEFAULT_PARAMS.rho, 0.0)

    # Troncature : la matrice doit s'elargir avec lambda, sinon la queue perdue
    # cesse d'etre negligeable des que lambda depasse quelques unites.
    check("matrice minimale pour des buts", predict._grid_size(1.5), predict.MAX_EVENTS)
    check(
        "matrice elargie pour des corners",
        predict._grid_size(6.0) > predict.MAX_EVENTS,
        True,
    )
    # A lambda = 6 par equipe, la loi du total est Poisson(12) : le seuil doit
    # retomber sur la valeur analytique. Compare a 5 decimales, ce qui est la
    # precision qu'une matrice bornee a six ecarts-types garantit ; avec la
    # borne fixe d'avant, l'ecart etait de 2 pour mille, soit mille fois plus.
    check(
        "seuil exact a lambda eleve",
        round(predict.over_probability(6.0, 6.0, 11.5), 5),
        round(1 - sum(predict.poisson_pmf(k, 12.0) for k in range(12)), 5),
    )

    # Correction du niveau des adversaires. Forces mesurees sur un echantillon
    # enorme pour que la regularisation ne les deplace pas.
    stable = 10 ** 6
    strong = {"attaque": 2.0, "defense": 0.5, "matchs": stable}   # forte partout
    weak = {"attaque": 0.5, "defense": 2.0, "matchs": stable}     # faible partout
    graded = {"Costaud": strong, "Faiblard": weak}

    # 3 buts contre une defense deux fois trop permissive n'en valent que 1.5.
    scored, conceded, fixed, weight = predict._adjusted_averages(
        [{"buts_pour": 3, "buts_contre": 0, "adversaire": "Faiblard"}], None, graded
    )
    check("buts devalues contre un faible", round(scored, 4), 1.5)
    check("matchs corriges comptes", fixed, 1)
    check("effectif efficace sans ponderation", weight, 1.0)

    # Les memes 3 buts contre une bonne defense en valent 6.
    scored, _, _, _ = predict._adjusted_averages(
        [{"buts_pour": 3, "buts_contre": 0, "adversaire": "Costaud"}], None, graded
    )
    check("buts valorises contre un fort", round(scored, 3), 6.0)

    # 2 buts encaisses contre une attaque deux fois trop forte n'en valent que 1.
    _, conceded, _, _ = predict._adjusted_averages(
        [{"buts_pour": 0, "buts_contre": 2, "adversaire": "Costaud"}], None, graded
    )
    check("buts encaisses relativises", round(conceded, 4), 1.0)

    # Adversaire hors championnat : aucune force connue, valeur brute conservee.
    scored, conceded, fixed, _ = predict._adjusted_averages(
        [{"buts_pour": 3, "buts_contre": 1, "adversaire": "Inconnu"}], None, graded
    )
    check("adversaire inconnu -> brut", (scored, conceded, fixed), (3.0, 1.0, 0))
    check(
        "sans reference -> brut",
        predict._adjusted_averages(
            [{"buts_pour": 3, "buts_contre": 1, "adversaire": "Costaud"}], None, None
        ),
        (3.0, 1.0, 0, 1.0),
    )

    # Reference detaillee : corners normalises comme les buts, forces remises a
    # plat depuis l'indexation equipe -> grandeur de `league_baseline`.
    full = {
        "competition": "Liga", "matchs": 100,
        "moyenne_domicile": 1.9, "moyenne_exterieur": 1.4, "moyenne_globale": 1.65,
        "forces": {},
        "stats": {"corners": {"matchs": 60, "moyenne_domicile": 5.5,
                              "moyenne_exterieur": 4.5, "moyenne_globale": 5.0}},
        "forces_stats": {"Costaud": {"corners": strong}},
    }
    goal_means, _ = predict._reference(full, None)
    corner_means, corner_forces = predict._reference(full, "corners")
    check("reference des buts", goal_means["moyenne_globale"], 1.65)
    check("reference des corners", corner_means["moyenne_globale"], 5.0)
    check("forces detaillees remises a plat", corner_forces["Costaud"], strong)
    check("grandeur sans reference", predict._reference(full, "tirs_cadres"), (None, {}))
    check("sans reference du tout", predict._reference(None, None), (None, {}))

    # Une grandeur n'est moyennee que sur les matchs qui la portent : sans ce
    # filtre, l'effectif annonce et celui passe a la regularisation etaient tous
    # deux surestimes.
    partial = [
        {"stats": {"pour": {"corners": 6}, "contre": {"corners": 4}}},
        {"stats": {"pour": {"corners": None}, "contre": {"corners": 4}}},
        {"buts_pour": 1, "buts_contre": 0},
    ]
    check("corners : matchs exploitables", len(predict._usable(partial, "corners")), 1)
    check("buts : matchs exploitables", len(predict._usable(partial, None)), 1)

    print("\n11. Fiche complete : echelles, propositions, confrontations")
    fiche_form = {
        "domicile": {"equipe": "Alpha", "matchs": [
            {"buts_pour": 2, "buts_contre": 1, "adversaire": "Z", "lieu": "domicile",
             "competition": "Liga", "amical": False,
             "stats": {"pour": {"corners": 6}, "contre": {"corners": 4}}},
            {"buts_pour": 1, "buts_contre": 1, "adversaire": "Z", "lieu": "exterieur",
             "competition": "Liga", "amical": False,
             "stats": {"pour": {"corners": 5}, "contre": {"corners": 5}}},
        ]},
        "exterieur": {"equipe": "Beta", "matchs": [
            {"buts_pour": 0, "buts_contre": 2, "adversaire": "Z", "lieu": "exterieur",
             "competition": "Liga", "amical": False,
             "stats": {"pour": {"corners": 3}, "contre": {"corners": 7}}},
            {"buts_pour": 1, "buts_contre": 0, "adversaire": "Z", "lieu": "domicile",
             "competition": "Liga", "amical": False,
             "stats": {"pour": {"corners": 4}, "contre": {"corners": 6}}},
        ]},
        "confrontations": [{"date": "2025-01-01", "competition": "Liga",
                            "buts_domicile": 1, "buts_exterieur": 0}],
    }
    fiche = predict.build(
        {"championnat": "Liga - Cloture", "statut": "A venir"}, fiche_form, full
    )
    metrics = {g["cle"]: g for g in fiche["grandeurs"]}
    check("buts prevus", "buts" in metrics, True)
    check("corners prevus", "corners" in metrics, True)
    check("grandeur sans donnee absente", "tirs_cadres" in metrics, False)
    check("confrontations transmises", len(fiche["confrontations"]), 1)
    check(
        "corners normalises par la competition",
        metrics["corners"]["methode"].startswith("Maher sur 60 matchs"),
        True,
    )
    check(
        "buts normalises par la competition",
        metrics["buts"]["methode"].startswith("Maher sur 100 matchs"),
        True,
    )
    ladders = metrics["buts"]["echelles"]
    check("echelle par equipe dans la gamme des bookmakers",
          all(0.5 <= l <= 3.5 for l in ladders["seuils_equipe"]) and len(ladders["seuils_equipe"]) >= 2,
          True)
    check(
        "echelle decroissante",
        all(a >= b for a, b in zip(ladders["total"], ladders["total"][1:])),
        True,
    )
    check(
        "echelle coherente avec le seuil affiche",
        round(ladders["total"][ladders["seuils_total"].index(2.5)], 9),
        round(metrics["buts"]["p_plus_de_seuil"], 9),
    )
    # Une proposition n'est retenue que si elle apprend quelque chose : ni
    # incertaine, ni vraie par construction.
    check(
        "propositions dans la plage d'interet",
        all(
            predict.OFFER_MIN <= offer["p"] <= predict.OFFER_CEILING
            for metric in fiche["grandeurs"]
            for offer in metric["offres"]
        ),
        True,
    )
    check(
        "propositions plafonnees",
        len(metrics["buts"]["offres"]) <= predict.OFFER_MAX,
        True,
    )
    # Les libelles doivent rester tranchables apres coup par verify.check_offer :
    # c'est ce qui permet de confronter une fiche a son resultat sans reecriture.
    undecidable = [
        offer["libelle"]
        for metric in fiche["grandeurs"]
        for offer in metric["offres"]
        if verify.check_offer(offer["libelle"], ("Alpha", "Beta"), (2.0, 1.0)) is None
    ]
    check("propositions toutes tranchables", undecidable, [])
    check(
        "matchs utilises = matchs moyennes",
        metrics["corners"]["matchs_utilises"],
        (2, 2),
    )

    # Surdispersion : la loi de comptage doit avoir exactement la moyenne
    # demandee et le rapport variance / moyenne demande, sinon elle ne modelise
    # pas ce qu'elle pretend.
    phi = predict.DISPERSION["corners"]
    law = predict.count_distribution(5.32, phi, 200)
    check("loi de comptage sommant a 1", round(sum(law), 9), 1.0)
    law_mean = sum(k * x for k, x in enumerate(law))
    law_var = sum((k - law_mean) ** 2 * x for k, x in enumerate(law))
    check("moyenne conservee", round(law_mean, 6), 5.32)
    check("dispersion obtenue", round(law_var / law_mean, 4), round(phi, 4))
    # A dispersion 1 (ou moins), c'est Poisson, exactement.
    check(
        "dispersion 1 -> Poisson",
        [round(x, 12) for x in predict.count_distribution(2.0, 1.0, 8)],
        [round(predict.poisson_pmf(k, 2.0), 12) for k in range(9)],
    )
    # Sous 1, la loi doit se RESSERRER : une binomiale negative ne sait pas le
    # faire, une binomiale si. Les cartons sont la seule grandeur concernee.
    etroite = predict.count_distribution(2.05, 0.847, 60)
    m_etroite = sum(k * x for k, x in enumerate(etroite))
    v_etroite = sum((k - m_etroite) ** 2 * x for k, x in enumerate(etroite))
    check("loi resserree : somme", round(sum(etroite), 9), 1.0)
    check("loi resserree : moyenne", round(m_etroite, 6), 2.05)
    check("loi resserree : dispersion", round(v_etroite / m_etroite, 3), 0.847)
    check(
        "sous-dispersion plus etroite que Poisson",
        v_etroite / m_etroite < 1.0,
        True,
    )
    # Une loi plus large met plus de masse dans les queues.
    check(
        "surdispersion -> queue plus lourde",
        predict.team_over_probability(5.32, 6.5, phi)
        > predict.team_over_probability(5.32, 6.5, 1.0),
        True,
    )
    # Dispersions mesurees, chacune sur des milliers de matchs (voir DISPERSION).
    check("buts legerement surdisperses", predict.DISPERSION["buts"], 1.173)
    check("cartons sous-disperses", predict.DISPERSION["cartons_jaunes"] < 1.0, True)
    check("corners les plus disperses",
          max(predict.DISPERSION, key=predict.DISPERSION.get), "corners")

    # Le seuil mis en avant et l'echelle doivent donner la meme valeur a la
    # meme ligne : reparties entre trois endroits, les deux lois (matrice jointe
    # pour les buts, somme fermee pour les grandeurs surdispersees) avaient
    # fini par diverger.
    for metric in fiche["grandeurs"]:
        ladder = metric["echelles"]
        if metric["seuil"] not in ladder["seuils_total"]:
            continue
        check(
            "%s : seuil affiche = echelle" % metric["libelle"],
            round(ladder["total"][ladder["seuils_total"].index(metric["seuil"])], 9),
            round(metric["p_plus_de_seuil"], 9),
        )

    # Le reglage voyage avec la fiche : sans lui, elle n'est pas verifiable
    # apres coup, puisqu'on ignore avec quoi elle a ete produite.
    check("reglage enregistre", fiche["reglage"], predict.DEFAULT_PARAMS._asdict())
    tuned = predict.build(
        {"championnat": "Liga - Cloture", "statut": "A venir"}, fiche_form, full,
        predict.Params(0.0, 7.0, 3.0),
    )
    check("reglage transmis", tuned["reglage"]["half_life"], 7.0)
    tuned_goals = next(g for g in tuned["grandeurs"] if g["cle"] == "buts")
    check(
        "regularisation plus faible -> prevision plus tranchee",
        max(tuned_goals["resultat"].values())
        > max(metrics["buts"]["resultat"].values()),
        True,
    )

    print("\n12. Options de paris : familles, coherence, tranchage")
    bettor = ("Marseille", "Paris FC")
    goals = next(m for m in predict.METRICS if m.key == "buts")
    corners = next(m for m in predict.METRICS if m.key == "corners")
    issues = predict.outcome_probabilities(1.25, 1.29)
    marque = predict.both_teams_score(1.25, 1.29)
    goal_offers = predict.offer_candidates(
        goals, bettor, 1.25, 1.29, issues, marque
    )
    corner_offers = predict.offer_candidates(corners, bettor, 5.32, 4.42)
    families = {c["famille"] for c in goal_offers}
    check(
        "familles de buts",
        families >= {"total", "equipe", "fourchette", "issue", "double chance",
                     "les deux marquent", "ecart", "parite", "cage inviolee",
                     "combine"},
        True,
    )
    check(
        "duel reserve aux grandeurs par equipe",
        ({c["famille"] for c in corner_offers} & {"duel"}, "duel" in families),
        ({"duel"}, False),
    )

    def probability(offers, label):
        return next(c["p"] for c in offers if c["libelle"] == label)

    # Toute proposition est une probabilite.
    check(
        "probabilites dans [0, 1]",
        all(0.0 <= c["p"] <= 1.0 for c in goal_offers + corner_offers),
        True,
    )
    # Parite : les deux moities forment une partition.
    check(
        "pair + impair = 1",
        round(
            probability(goal_offers, "Nombre total de buts pair")
            + probability(goal_offers, "Nombre total de buts impair"),
            9,
        ),
        1.0,
    )
    # Duel : "plus de" est strictement inclus dans "autant ou plus de".
    check(
        "duel strict plus rare que le duel large",
        probability(corner_offers, "Plus de corners pour Marseille")
        < probability(corner_offers, "Autant ou plus de corners pour Marseille"),
        True,
    )
    # Un ecart plus large est plus rare, et reste sous la victoire simple.
    ecart2 = probability(goal_offers, "Marseille gagne par 2 buts ou plus")
    ecart3 = probability(goal_offers, "Marseille gagne par 3 buts ou plus")
    check("ecart plus large -> plus rare", ecart3 < ecart2, True)
    check("ecart inclus dans la victoire", ecart2 < issues["domicile"], True)
    # Un combine ne peut pas depasser la moins probable de ses deux conditions.
    combine = probability(
        goal_offers, "Les deux equipes marquent et plus de 1.5 buts"
    )
    check("combine borne par ses conditions", combine <= marque + 1e-12, True)
    # Fourchette : coherente avec les deux seuils qui la bornent.
    check(
        "fourchette = difference de deux seuils",
        round(probability(goal_offers, "Entre 1 et 3 buts au total"), 9),
        round(
            predict._metric_total_over(goals, 1.25, 1.29, 0.5, 0.0)
            - predict._metric_total_over(goals, 1.25, 1.29, 3.5, 0.0),
            9,
        ),
    )
    # Cage inviolee : c'est la marginale de l'adversaire a zero.
    check(
        "cage inviolee = adversaire a zero",
        round(probability(goal_offers, "Marseille n'encaisse aucun but"), 6),
        round(predict.poisson_pmf(0, 1.29), 6),
    )

    # Tout ce qui est propose doit rester tranchable apres coup, sur un score
    # connu : c'est la seule garantie qui rende une fiche verifiable.
    undecidable = [
        c["libelle"]
        for c in goal_offers + corner_offers
        if verify.check_offer(c["libelle"], bettor, (2.0, 1.0)) is None
    ]
    check("toutes les propositions tranchables", undecidable, [])
    # Tranchage sur un 2-1 : chaque famille jugee sur une valeur connue.
    verdicts = {
        "Entre 2 et 4 buts au total": True,
        "Entre 4 et 5 buts au total": False,
        "Marseille gagne par 2 buts ou plus": False,
        "Nombre total de buts impair": True,
        "Marseille n'encaisse aucun but": False,
        "Plus de corners pour Marseille": True,
        "Autant ou plus de corners pour Paris FC": False,
        "Victoire Marseille et plus de 2.5 buts": True,
        "Victoire Marseille et plus de 3.5 buts": False,
    }
    for label, expected in verdicts.items():
        check("2-1 : %s" % label, verify.check_offer(label, bettor, (2.0, 1.0)), expected)
    # Un combine dont une moitie est fausse est faux.
    check(
        "combine faux si une moitie l'est",
        verify.check_offer(
            "Les deux equipes marquent et plus de 1.5 buts",
            bettor, (2.0, 0.0),
        ),
        False,
    )

    # La selection ne doit pas servir six fois le meme pari sous six libelles.
    picked = predict.select_offers(goal_offers)
    counts = {}
    for offer in picked:
        counts[offer["famille"]] = counts.get(offer["famille"], 0) + 1
    check("selection plafonnee", len(picked) <= predict.OFFER_MAX, True)
    check(
        "pas plus de N par famille",
        max(counts.values()) <= predict.OFFER_PER_FAMILY,
        True,
    )
    check("plusieurs familles servies", len(counts) > 1, True)
    check(
        "selection dans la plage d'interet",
        all(predict.OFFER_MIN <= o["p"] <= predict.OFFER_CEILING for o in picked),
        True,
    )
    # La liste se remplit meme quand une seule famille tient dans la plage.
    single = [
        {"libelle": "p%d" % i, "p": 0.80 - i / 100, "famille": "total"}
        for i in range(6)
    ]
    check(
        "liste complete malgre une famille unique",
        len(predict.select_offers(single)),
        min(predict.OFFER_MAX, 6),
    )

    print("\n14. Statistiques de reussite exprimees en pourcentage")
    # Le flux ecrit "86% (450/526)" : c'est le taux qui compare deux equipes,
    # 450 passes reussies ne voulant rien dire sans le nombre tente.
    check("taux lu dans '86% (450/526)'", api_client.stat_number("86% (450/526)"), 86.0)
    check("pourcentage simple", api_client.stat_number("51%"), 51.0)
    check("nombre entier", api_client.stat_number(7), 7.0)
    check("valeur illisible", api_client.stat_number("-"), None)

    print("\n15. Catalogue des criteres et poids")
    check("quatorze criteres", len(context.CATALOGUE), 14)
    check(
        "numerotation continue",
        [c["numero"] for c in context.CATALOGUE],
        list(range(1, 15)),
    )
    # Chaque poids de `Poids` doit correspondre a un critere du catalogue, et
    # reciproquement : sans cette verification, un critere ajoute sans reglage
    # (ou l'inverse) passerait inapercu jusqu'a la premiere fiche.
    check(
        "poids et catalogue accordes",
        {c["poids"] for c in context.CATALOGUE if c["poids"]},
        set(context.Poids._fields),
    )

    print("\n16. Les corrections de contexte sont plafonnees")
    # Sept criteres a +10 % feraient 1,95 sur les buts. Le plafond agit sur le
    # PRODUIT, la ou chaque critere ne connait que sa propre part.
    empiles = [
        context.Critere(
            i, "c%d" % i, "critere %d" % i, True, "test", {}, "", {"buts": (1.10, 1.10)}
        )
        for i in range(7)
    ]
    combine = context.combiner(empiles)
    check(
        "produit plafonne",
        combine["buts"]["domicile"] <= context.PLAFOND["buts"] + 1e-9,
        True,
    )
    check("plafond atteint", combine["buts"]["domicile"], context.PLAFOND["buts"])
    # Et symetriquement vers le bas.
    baisse = [
        context.Critere(
            i, "c%d" % i, "critere %d" % i, True, "test", {}, "", {"buts": (0.90, 0.90)}
        )
        for i in range(7)
    ]
    check(
        "plancher symetrique",
        round(context.combiner(baisse)["buts"]["domicile"], 4),
        round(1.0 / context.PLAFOND["buts"], 4),
    )
    check("aucun critere, aucune correction", context.combiner([]), {})

    print("\n17. Probabilites implicites du marche (critere 13)")
    # Cotes equilibrees a 3.00 : la marge est nulle et chaque issue vaut 1/3.
    equilibre = context.probabilites_implicites(
        {"domicile": 3.0, "nul": 3.0, "exterieur": 3.0}
    )
    check("issues equiprobables", round(equilibre["nul"], 4), round(1 / 3, 4))
    marge = context.probabilites_implicites(
        {"domicile": 2.0, "nul": 3.5, "exterieur": 4.0}
    )
    check("somme ramenee a 1", round(sum(marge.values()), 6), 1.0)
    check("favori en tete", max(marge, key=marge.get), "domicile")
    check("cote impossible ecartee", context.probabilites_implicites({"nul": 0.5}), {})

    print("\n18. Similarite de profil (critere 4)")
    ordinaire = {"buts_attaque": 1.0, "buts_defense": 1.0}
    check("profils identiques", context._similarite(ordinaire, ordinaire), 1.0)
    eloigne = {"buts_attaque": 2.0, "buts_defense": 0.4}
    check(
        "profil eloigne moins pese",
        context._similarite(ordinaire, eloigne) < 0.5,
        True,
    )
    # Un adversaire inconnu de la reference ne doit ni etre privilegie ni etre
    # ecarte : ne pas savoir n'est pas une raison d'effacer un match.
    check("profil inconnu compte comme les autres", context._similarite({}, ordinaire), 1.0)

    print("\n19. Meteo : seuils et sens de l'effet (critere 10)")
    calme = context._critere_meteo(
        {"temperature_c": 18.0, "precipitation_mm": 0.0, "vent_kmh": 10.0}, {}, 1.0
    )
    check("temps calme, aucun effet", calme.effet, {})
    tempete = context._critere_meteo(
        {"temperature_c": 5.0, "precipitation_mm": 6.0, "vent_kmh": 45.0}, {}, 1.0
    )
    check("pluie et vent : moins de buts", tempete.effet["buts"][0] < 1.0, True)
    check("pluie et vent : plus de corners", tempete.effet["corners"][0] > 1.0, True)
    check(
        "a poids nul, la meteo ne deplace rien",
        context._critere_meteo(
            {"temperature_c": 5.0, "precipitation_mm": 6.0, "vent_kmh": 45.0}, {}, 0.0
        ).effet,
        {},
    )
    check(
        "sans meteo, critere indisponible",
        context._critere_meteo({}, {}, 1.0).disponible,
        False,
    )

    print("\n20. Options de paris : les deux faces, une seule mise en avant")
    fiche = {
        "match": "Lens - Lille",
        "grandeurs": [
            {
                "cle": "corners",
                "grandeur": "Corners",
                "attendu_domicile": 5.2,
                "attendu_exterieur": 4.3,
                "attendu_total": 9.5,
                "marche_privilegie": "plus",
                "echelle_total": {"seuils": [8.5, 9.5], "probabilites": [0.62, 0.48]},
                "echelle_par_equipe": {
                    "seuils": [3.5, 4.5], "Lens": [0.7, 0.5], "Lille": [0.6, 0.4],
                },
                "offres": [
                    {"pari": "Plus de 8.5 corners au total", "probabilite": 0.62},
                    {"pari": "Moins de 11.5 corners au total", "probabilite": 0.81},
                    {"pari": "Plus de corners pour Lens", "probabilite": 0.61},
                ],
            }
        ],
    }
    options = forecast.betting_options(fiche)
    ligne = options["grandeurs"][0]
    check("les deux faces conservees", len(ligne["propositions"]), 3)
    check(
        "seule la face privilegiee est mise en avant",
        [o["pari"] for o in ligne["propositions_privilegiees"]],
        ["Plus de 8.5 corners au total", "Plus de corners pour Lens"],
    )
    # Le complement du total est deduit, jamais recalcule : deux lectures de la
    # meme fiche doivent coincider a la quatrieme decimale.
    check("complement du total", ligne["total"]["moins_de"], [0.38, 0.52])
    check("echelle par equipe indexee par nom", ligne["par_equipe"]["Lens"], [0.7, 0.5])

    print("\n21. Un critere indisponible ne corrige rien")
    absent = context._absent(12, "arbitre", "Discipline", "test", "pas designe")
    check("aucun effet", absent.effet, {})
    check("declare indisponible", absent.disponible, False)
    # Indisponible et neutre ne sont pas la meme chose : le premier dit que la
    # donnee manque, le second qu'elle a ete regardee et ne change rien.
    neutre = context._critere_meteo(
        {"temperature_c": 18.0, "precipitation_mm": 0.0, "vent_kmh": 5.0}, {}, 1.0
    )
    check("neutre reste disponible", neutre.disponible, True)

    print("\n22. Rapprochement des equipes entre deux sources")
    # La source des absences ne partage pas les identifiants de match : il faut
    # rapprocher par le nom, et c'est la que les assemblages de sources cassent.
    check(
        "abreviation reconnue",
        api_client._same_team("Manchester Utd", "Manchester United"),
        True,
    )
    check(
        "nom tronque reconnu",
        api_client._same_team("Nottingham", "Nottingham Forest"),
        True,
    )
    check(
        "deux clubs d'une meme ville distingues",
        api_client._same_team("Manchester United", "Manchester City"),
        False,
    )
    check(
        "suffixe de club ignore",
        api_client._same_team("FC Cologne", "Cologne"),
        True,
    )
    # Deux correspondances valent aucune : mieux vaut un critere indisponible
    # qu'un critere qui attribue a une equipe les absents d'une autre.
    ambigu = {"Manchester United": ["a"], "Manchester City": ["b"]}
    check(
        "nom ambigu : aucun rapprochement",
        api_client._absents_de(ambigu, "Manchester"),
        None,
    )
    check(
        "nom sans ambiguite : rapproche",
        api_client._absents_de(ambigu, "Manchester Utd"),
        ["a"],
    )

    print("\n23. Absences ponderees par poste (critere 3)")
    # Une absence en attaque retire des buts a son equipe, une absence en
    # defense en donne a l'adversaire ; un milieu compte moitie pour chaque.
    manque = context._poids_absences([
        {"joueur": "A", "motif": "blesse", "poste": "attaquant"},
        {"joueur": "B", "motif": "blesse", "poste": "defenseur"},
        {"joueur": "C", "motif": "blesse", "poste": "milieu"},
    ])
    check("secteur offensif", manque["offensif"], 1.5)
    check("secteur defensif", manque["defensif"], 1.5)
    # Un joueur douteux joue une fois sur deux.
    doute = context._poids_absences([
        {"joueur": "A", "motif": "incertain", "poste": "attaquant"},
    ])
    check("joueur incertain compte moitie", doute["offensif"], 0.5)
    # Le plafond evite qu'une liste melant titulaires et blesses de longue
    # duree produise un effectif fantome.
    beaucoup = context._poids_absences([
        {"joueur": str(i), "motif": "blesse", "poste": "attaquant"} for i in range(9)
    ])
    check("plafond des absences", beaucoup["offensif"], context.ABSENTS_MAX)
    check("aucun absent", context._poids_absences([]), {"offensif": 0.0, "defensif": 0.0})

    print("\n24. Cotes : deux formes de releve, une seule lecture")
    # Le releve automatique porte les deux jeux, celui fourni a la main un seul.
    moyennes, meilleures = context._deux_cotes(
        {"moyennes": {"domicile": 2.0}, "meilleures": {"domicile": 2.2}}
    )
    check("releve complet : moyennes", moyennes["domicile"], 2.0)
    check("releve complet : meilleures", meilleures["domicile"], 2.2)
    plat_m, plat_b = context._deux_cotes({"domicile": 2.0, "nul": 3.4, "exterieur": 3.2})
    check("releve plat : une cote sert des deux cotes", plat_m, plat_b)
    check("aucun releve", context._deux_cotes(None), ({}, {}))

    print("\n25. Valeur esperee d'un pari (critere 13)")
    critere = context.critere_cotes(
        {"moyennes": {"domicile": 2.0, "nul": 3.4, "exterieur": 4.0},
         "meilleures": {"domicile": 2.2, "nul": 3.5, "exterieur": 4.2}},
        {"domicile": 0.50, "nul": 0.28, "exterieur": 0.22},
    )
    check("critere renseigne", critere.disponible, True)
    # p x cote - 1 : a 50 % et 2.20, on gagne 10 centimes par euro engage.
    check(
        "esperance aux meilleures cotes",
        critere.valeur["valeur_esperee"]["domicile"],
        round(0.50 * 2.2 - 1.0, 4),
    )
    check(
        "marge des operateurs positive",
        critere.valeur["marge_operateurs"] > 0,
        True,
    )
    # Le critere n'a jamais d'effet : s'aligner sur le marche reviendrait a le
    # recopier en croyant le prevoir.
    check("aucune correction", critere.effet, {})
    check(
        "sans cote, critere indisponible",
        context.critere_cotes(None, {"domicile": 0.5}).disponible,
        False,
    )
    # Le mouvement de ligne ne se lit que dans deux releves.
    avec_mouvement = context.critere_cotes(
        {"domicile": 1.9, "nul": 3.5, "exterieur": 4.3},
        {"domicile": 0.50, "nul": 0.28, "exterieur": 0.22},
        [
            {"releve_le": "2026-09-10T09:00:00", "cotes": {"domicile": 2.1, "nul": 3.4, "exterieur": 4.0}},
            {"releve_le": "2026-09-12T09:00:00", "cotes": {"domicile": 1.9, "nul": 3.5, "exterieur": 4.3}},
        ],
    )
    check("derive relevee", avec_mouvement.valeur["mouvement"]["derive"]["domicile"], -0.2)
    check(
        "resserrement signale",
        avec_mouvement.valeur["mouvement"]["resserrement"],
        ["domicile"],
    )

    print("\n26. Composition probable : rapprochement des joueurs")
    # Les deux sources n'ecrivent pas les joueurs pareil : « Cunha M. » d'un
    # cote, « Matheus Cunha » de l'autre.
    check("initiale contre prenom", context._meme_joueur("Cunha M.", "Matheus Cunha"), True)
    check("particule conservee", context._meme_joueur("de Ligt M.", "Matthijs de Ligt"), True)
    check("nom complet des deux cotes",
          context._meme_joueur("Andrey Santos", "Andrey Santos"), True)
    check("joueurs differents",
          context._meme_joueur("Maguire H.", "Bruno Fernandes"), False)
    check("nom vide", context._meme_joueur("", "Tom Heaton"), False)

    print("\n27. Composition probable : le onze")
    historique = {
        "matchs_couverts": 5,
        "systeme": "1-4-4-2",
        "joueurs": {
            "Gardien A.": {"titularisations": 5, "place": 1, "numero": 1},
            "Defense B.": {"titularisations": 5, "place": 2, "numero": 2},
            "Defense C.": {"titularisations": 4, "place": 3, "numero": 3},
            "Defense D.": {"titularisations": 5, "place": 4, "numero": 4},
            "Defense E.": {"titularisations": 3, "place": 5, "numero": 5},
            "Milieu F.": {"titularisations": 5, "place": 6, "numero": 6},
            "Milieu G.": {"titularisations": 4, "place": 7, "numero": 7},
            "Milieu H.": {"titularisations": 5, "place": 8, "numero": 8},
            "Avant I.": {"titularisations": 5, "place": 9, "numero": 9},
            "Avant J.": {"titularisations": 4, "place": 10, "numero": 10},
            "Avant K.": {"titularisations": 5, "place": 11, "numero": 11},
            "Doublure L.": {"titularisations": 1, "place": 11, "numero": 12},
        },
    }
    compo = context._equipe_probable(historique, [])
    check("onze complet", len(compo["onze"]), 11)
    check("systeme repris", compo["systeme"], "1-4-4-2")
    check("place tenue par le plus titularise",
          [j["joueur"] for j in compo["onze"] if j["place"] == 11], ["Avant K."])
    check("ordonne par place", [j["place"] for j in compo["onze"]], list(range(1, 12)))
    check("lignes deduites",
          [compo["onze"][0]["ligne"], compo["onze"][4]["ligne"], compo["onze"][10]["ligne"]],
          ["gardien", "defense", "attaque"])

    # Un absent est ecarte, et sa place revient au suivant.
    ampute = context._equipe_probable(
        historique, [{"joueur": "Kevin Avant K.", "motif": "blesse", "poste": "attaquant"}]
    )
    check("absent ecarte du onze",
          "Avant K." not in [j["joueur"] for j in ampute["onze"]], True)
    check("absent liste a part", [j["joueur"] for j in ampute["ecartes"]], ["Avant K."])
    check("sa place revient au suivant",
          [j["joueur"] for j in ampute["onze"] if j["place"] == 11], ["Doublure L."])
    # Un joueur douteux joue une fois sur deux : il reste, signale.
    doute = context._equipe_probable(
        historique, [{"joueur": "Kevin Avant K.", "motif": "incertain", "poste": "attaquant"}]
    )
    check("incertain conserve",
          [j["incertain"] for j in doute["onze"] if j["joueur"] == "Avant K."], [True])
    check("aucun historique, aucun onze", context._equipe_probable({}, []), {})

    print("\n28. Erreur de quota remontee comme QuotaError")
    def quota_request(url, headers, params):
        return {"errors": {"requests": "You have reached the request limit"}}

    api_client._request = quota_request
    try:
        api_client.get_stats(MATCH, use_cache=False)
        check("QuotaError levee", False, True)
    except api_client.QuotaError:
        check("QuotaError levee", True, True)
    except Exception as exc:  # noqa: BLE001
        check("QuotaError levee", type(exc).__name__, "QuotaError")

    print()
    print("29. Melange des buts attendus selon l'echantillon")
    # A k = 0 le melange doit rendre EXACTEMENT le poids du critere : c'est ce
    # qui garantit que le reglage par defaut ne change pas une seule prevision.
    check(
        "k = 0 : poids inchange",
        [predict.melange_xg_effectif(0.25, n, 0.0) for n in (0, 3, 10, 40)],
        [0.25, 0.25, 0.25, 0.25],
    )
    poids = [predict.melange_xg_effectif(0.25, n, 6.0) for n in (2, 4, 8, 16, 32)]
    check("k > 0 : decroit avec l'echantillon", poids == sorted(poids, reverse=True), True)
    check("borne haute : jamais au-dessus de 1", max(poids) <= 1.0, True)
    check("borne basse : jamais sous le poids du critere", min(poids) >= 0.25, True)
    check(
        "echantillon nul : le xG prend tout",
        round(predict.melange_xg_effectif(0.25, 0.0, 6.0), 6),
        1.0,
    )
    check("reglage present et neutre", predict.DEFAULT_PARAMS.xg_echantillon, 0.0)

    print()
    print("30. Calibration ventilee par desequilibre de l'affiche")
    from ibet.evaluation import backtest

    grandeur = {"lambda_domicile": 5.6, "lambda_exterieur": 4.0}
    check(
        "desequilibre sans echelle",
        round(backtest.desequilibre(grandeur), 4),
        round(1.6 / 9.6, 4),
    )
    check("desequilibre sans lambda", backtest.desequilibre({}), None)
    equipes = ("Alpha", "Beta")
    check(
        "cote visee : le plus attendu est le favori",
        backtest.cote_visee("Alpha : plus de 4.5 corners", equipes, grandeur),
        "favori",
    )
    check(
        "cote visee : l'autre est l'outsider",
        backtest.cote_visee("Beta : plus de 4.5 corners", equipes, grandeur),
        "outsider",
    )
    check(
        "cote visee : sans sujet, c'est le total",
        backtest.cote_visee("Plus de 9.5 corners au total", equipes, grandeur),
        "total",
    )
    check(
        "cote visee : une issue n'engage aucun cote",
        backtest.cote_visee("Match nul", equipes, grandeur),
        "",
    )

    # Le detecteur doit trouver un effet plante ET rester muet sans effet. Les
    # deux comptent autant : un test qui ne crie jamais ne sert a rien, un test
    # qui crie toujours non plus.
    import random as _random

    def _ventiler(biais_partage: float, biais_total: float, graine: int):
        _random.seed(graine)
        mesure = backtest.CalibrationConditionnelle("essai")
        # Mille MATCHS, trois propositions chacun. Le compte de matchs est ce
        # qui gouverne la precision : les propositions d'un meme match sont
        # correlees, et l'erreur type les groupe.
        for numero in range(1000):
            ecart = _random.uniform(0.0, 0.30)
            cle = "m%d" % numero
            decale = biais_partage * ecart
            for _ in range(3):
                p = _random.uniform(0.55, 0.90)
                mesure.add(cle, ecart, "favori", p, _random.random() < p - decale)
                mesure.add(
                    cle, ecart, "outsider", p,
                    _random.random() < min(0.999, p + decale),
                )
                mesure.add(
                    cle, ecart, "total", p,
                    _random.random() < p + biais_total * ecart,
                )
        return mesure.summary()["tranches"]

    avec = _ventiler(0.35, 0.0, 4)
    check("effet plante : trois tranches rendues", len(avec), 3)
    # L'erreur type est groupee par match : la case doit compter des MATCHS,
    # pas les propositions qu'ils fournissent.
    check(
        "le compte affiche est celui des matchs",
        avec[0]["total"]["matchs"] < avec[0]["total"]["propositions"],
        True,
    )
    check(
        "effet plante : asymetrie croissante",
        [t["asymetrie"]["valeur"] for t in avec]
        == sorted(t["asymetrie"]["valeur"] for t in avec),
        True,
    )
    check(
        "effet plante : signale sur la tranche extreme",
        avec[-1]["asymetrie"]["significatif"],
        True,
    )
    check(
        "effet plante sur le partage : le total reste plat",
        abs(avec[-1]["total"]["ecart"]) < 2 * avec[-1]["total"]["erreur_type"],
        True,
    )
    # Signature des cartons : l'effet porte sur le TOTAL, pas sur le partage.
    cartons = _ventiler(0.0, -0.30, 4)
    check(
        "effet sur le total : le total decroche",
        abs(cartons[-1]["total"]["ecart"]) > 2 * cartons[-1]["total"]["erreur_type"],
        True,
    )
    # La signature attendue des cartons est un ecart sur le TOTAL et rien sur
    # le partage. Comparer les deux valeurs d'un tirage unique compare deux
    # bruits ; on compte donc, sur plusieurs tirages, laquelle des deux cases
    # s'allume -- c'est cela, la signature.
    total_signale = asymetrie_signalee = 0
    for graine in range(8):
        derniere = _ventiler(0.0, -0.30, 200 + graine)[-1]
        total_signale += abs(derniere["total"]["ecart"]) > 2 * derniere["total"][
            "erreur_type"
        ]
        asymetrie_signalee += bool(derniere["asymetrie"]["significatif"])
    check(
        "signature des cartons : c'est le total qui s'allume",
        total_signale > asymetrie_signalee,
        True,
    )

    # Le temoin ne se teste pas sur une seule graine. Un test a deux erreurs
    # types se trompe cinq fois sur cent PAR CONSTRUCTION, et il y a trois cases
    # par tirage : exiger zero faux positif d'un tirage unique, c'est tester la
    # chance et non le code. On mesure donc le TAUX sur plusieurs tirages, et
    # l'absence de biais -- les deux proprietes qui font qu'un detecteur est
    # honnete.
    faux, cases, valeurs = 0, 0, []
    for graine in range(12):
        for tranche in _ventiler(0.0, 0.0, 100 + graine):
            mesure = tranche.get("asymetrie")
            if not mesure:
                continue
            cases += 1
            valeurs.append(mesure["valeur"])
            faux += bool(mesure["significatif"])
    check("temoin : au moins trente cases testees", cases >= 30, True)
    check(
        "temoin : le taux de faux positifs reste proche des 5 %% attendus",
        faux / cases < 0.20,
        True,
    )
    check(
        "temoin : le detecteur ne penche d'aucun cote",
        abs(sum(valeurs) / len(valeurs)) < 0.02,
        True,
    )
    # La face ou le modele ne penche pas est ecartee, sans quoi annonce et
    # observe vaudraient 50 % par construction.
    muette = backtest.CalibrationConditionnelle("muette")
    muette.add("m1", 0.1, "favori", 0.30, True)
    check("face minoritaire ecartee", muette.observations, [])
    inconnue = backtest.CalibrationConditionnelle("inconnue")
    inconnue.add("m1", 0.1, "issue", 0.80, True)
    check("cote inconnu ignore", inconnue.observations, [])

    print()
    print("31. Valeur au marche et bilan des paris")
    from ibet.prevision import marche

    check("valeur : p x cote - 1", round(marche.valeur(0.55, 2.10), 4), 0.155)
    check("cote inexploitable", marche.valeur(0.55, 1.0), None)
    check(
        "cle de marche : victoire a domicile",
        marche.cle_de_marche("Victoire Alpha", equipes),
        "domicile",
    )
    check(
        "cle de marche : une ligne de corners n'en a pas",
        marche.cle_de_marche("Plus de 9.5 corners au total", equipes),
        "",
    )
    check(
        "cle de marche : la double chance non plus",
        marche.cle_de_marche("Alpha ou nul", equipes),
        "",
    )
    # Le releve et le coup d'envoi ne sont pas ecrits dans la meme forme : les
    # comparer tels quels ecartait TOUS les releves d'avant-match, en silence.
    check(
        "horodatages ramenes a la meme forme",
        marche._horodatage("2026-01-02T09:00+01:00")
        < marche._horodatage("2026-01-02 20:00"),
        True,
    )
    check(
        "releve d'apres-match reconnu comme tel",
        marche._horodatage("2026-01-02T23:00+01:00")
        > marche._horodatage("2026-01-02 20:00"),
        True,
    )
    # Regression : "Victoire de l'une ou l'autre (PAS DE nul)" contient la
    # sous-chaine "s de " et se faisait compter comme un total, polluant la
    # colonne qui porte la signature des cartons.
    check(
        "une issue n'est pas un total, meme si elle contient 'pas de'",
        [
            backtest.cote_visee(x, equipes, grandeur)
            for x in (
                "Victoire de l'une ou l'autre (pas de nul)",
                "Pas de match nul",
                "Les deux equipes marquent",
            )
        ],
        ["", "", ""],
    )
    check(
        "un vrai seuil reste un total",
        [
            backtest.cote_visee(x, equipes, grandeur)
            for x in ("Plus de 2.5 buts au total", "Moins de 9.5 corners au total")
        ],
        ["total", "total"],
    )

    check(
        "sans cote, aucune proposition valorisee",
        marche.offres_valorisees(
            "Alpha - Beta", [{"pari": "Victoire Alpha", "probabilite": 0.5}], None
        ),
        [],
    )
    valorisees = marche.offres_valorisees(
        "Alpha - Beta",
        [
            {"pari": "Victoire Alpha", "probabilite": 0.60},
            {"pari": "Plus de 2.5 buts au total", "probabilite": 0.70},
        ],
        {"domicile": 2.0, "nul": 3.4, "exterieur": 4.0},
    )
    check("seules les issues sont valorisables", len(valorisees), 1)
    check("valeur calculee", round(valorisees[0]["valeur"], 4), 0.20)
    # `selection` valorise les trois ISSUES d'une fiche, et non ses
    # propositions retenues : celles-ci sont des totaux et des doubles chances
    # qu'aucune cote 1X2 ne peut pricer.
    fiche_selection = {
        "match": "Alpha - Beta",
        "match_id": "x",
        "grandeurs": [
            {
                "grandeur": "Buts",
                "issue": {"domicile": 0.60, "nul": 0.20, "exterieur": 0.20},
            }
        ],
        "cotes": {"domicile": 2.0, "nul": 3.0, "exterieur": 9.0},
    }
    retenues = marche.selection([fiche_selection])
    check("selection : deux paris au-dessus du seuil", len(retenues), 2)
    check("selection : le raisonnable en tete", retenues[0]["pari"], "Victoire Alpha")
    check("selection : l'ecart demesure signale", retenues[1].get("demesure"), True)
    check(
        "selection : l'issue sous le seuil est ecartee",
        [ligne["pari"] for ligne in retenues],
        ["Victoire Alpha", "Victoire Beta"],
    )
    sous_seuil = marche.selection(
        [dict(fiche_selection, cotes={"domicile": 1.6, "nul": 3.0, "exterieur": 4.0})]
    )
    check("selection : sous le seuil, rien", sous_seuil, [])

    # Les trois issues d'une fiche, valorisees. C'est la matiere du bilan : les
    # propositions RETENUES sont des totaux et des doubles chances (elles seules
    # depassent 60 %), alors que la base ne cote que le 1X2. Les deux ensembles
    # ne se rencontraient jamais et le bilan serait reste vide.
    check(
        "issue reelle lue dans le score",
        [
            marche.issue_reelle({"score": x})
            for x in ("2 - 1", "1 - 1", "0 - 2", "n/d")
        ],
        ["domicile", "nul", "exterieur", None],
    )
    fiche_type = {
        "match": "Alpha - Beta",
        "match_id": "m1",
        "resultat_reel": {"score": "2 - 1"},
        "grandeurs": [
            {
                "grandeur": "Buts",
                "issue": {"domicile": 0.50, "nul": 0.25, "exterieur": 0.25},
            }
        ],
    }
    trois = marche.paris_d_issue(
        fiche_type, {"domicile": 2.2, "nul": 3.4, "exterieur": 4.0}
    )
    check("trois paris par fiche, un par issue", len(trois), 3)
    check(
        "seule l'issue survenue est gagnante",
        [p["verifie"] for p in trois],
        [True, False, False],
    )
    check(
        "le nom de l'equipe est repris dans le libelle",
        [p["pari"] for p in trois],
        ["Victoire Alpha", "Match nul", "Victoire Beta"],
    )
    check(
        "valeur calculee sur la probabilite du modele",
        round(trois[0]["valeur"], 4),
        0.10,
    )
    check(
        "fiche non tranchee : verifie reste indetermine",
        [
            p["verifie"]
            for p in marche.paris_d_issue(
                dict(fiche_type, resultat_reel=None),
                {"domicile": 2.2, "nul": 3.4, "exterieur": 4.0},
            )
        ],
        [None, None, None],
    )
    check(
        "sans cote, aucune issue valorisee",
        marche.paris_d_issue(fiche_type, None),
        [],
    )
    # Une prevision de repli -- pas de reference de competition -- n'a pas la
    # meme valeur, et rien ne le disait une fois la proposition sortie de sa
    # fiche. C'est le cas de toute la Coupe d'Europe.
    cotes_test = {"domicile": 2.2, "nul": 3.4, "exterieur": 4.0}
    check(
        "prevision complete : pas de reserve",
        trois[0]["sans_reference"],
        False,
    )
    repli = dict(fiche_type, grandeurs=[
        dict(
            fiche_type["grandeurs"][0],
            methode="moyenne production / concession",
        )
    ])
    check(
        "prevision de repli : signalee",
        [p["sans_reference"] for p in marche.paris_d_issue(repli, cotes_test)],
        [True, True, True],
    )
    # Trois paris d'un meme match sont mutuellement exclusifs : l'erreur type
    # doit compter le match, pas les paris.
    un_seul = marche._rendement(
        [dict(p, verifie=bool(p["verifie"])) for p in trois]
    )
    check("un seul match : pas d'erreur type possible", un_seul["matchs"], 1)
    check(
        "erreur type indefinie sur un groupe unique",
        un_seul["rendement_erreur_type"],
        None,
    )
    # Regression : sans coup d'envoi connu, le filtre anti-fuite etait saute et
    # une cote relevee APRES le match entrait dans le bilan.
    check(
        "horodatage vide : le releve est refuse, pas accepte",
        marche._horodatage(""),
        "",
    )

    print()
    print("32. Mesure prospective des criteres a poids zero")
    import random as _rnd

    from ibet.evaluation import criteres as _criteres

    check(
        "total lu depuis le score",
        _criteres._reel({"score": "2 - 1"}, "Buts"),
        3.0,
    )
    check(
        "total lu depuis le libelle de la grandeur",
        _criteres._reel({"Cartons jaunes": "3 - 4"}, "Cartons jaunes"),
        7.0,
    )
    check("resultat illisible", _criteres._reel({"score": "n/d"}, "Buts"), None)
    check("grandeur absente", _criteres._reel({}, "Corners"), None)

    # Un critere qui a EXACTEMENT raison donne une pente de 1 : son signal est
    # deja le facteur qu'il appliquerait. Un critere muet donne 0.
    _rnd.seed(2)
    juste = []
    muet = []
    for _ in range(400):
        signal = 1.0 + _rnd.gauss(0, 0.15)
        juste.append((signal, signal + _rnd.gauss(0, 0.25)))
        muet.append((signal, 1.0 + _rnd.gauss(0, 0.25)))
    mesure = _criteres._pente(juste, 1.0)
    check("critere juste : pente proche de 1", abs(mesure["pente"] - 1.0) < 0.2, True)
    check("critere juste : signale", mesure["significatif"], True)
    mesure = _criteres._pente(muet, 1.0)
    check("critere muet : pente proche de 0", abs(mesure["pente"]) < 0.2, True)
    check("critere muet : non signale", mesure["significatif"], False)
    plat = _criteres._pente([(0.0, 1.0), (0.0, 1.1), (0.0, 0.9)] * 10, 0.0)
    check("signal constant reconnu", plat.get("signal_constant"), True)
    check("pente absente si signal constant", "pente" in plat, False)
    check("trop peu de points", _criteres._pente([(1.0, 1.0)], 1.0), {"fiches": 1})

    # Extraction du signal depuis une fiche complete, et filtrage des fiches
    # inexploitables : le critere indisponible ce jour-la ne doit pas compter.
    def _fiche(rapport, dispo=True, reel="3 - 2"):
        return {
            "resultat_reel": {"score": "1 - 0", "Cartons jaunes": reel},
            "grandeurs": [{"grandeur": "Cartons jaunes", "attendu_total": 5.0}],
            "contexte": {
                "criteres": [
                    {"cle": "arbitre", "disponible": dispo,
                     "valeur": {"rapport": rapport}}
                ]
            },
        }

    couples = _criteres.observations("arbitre", [_fiche(1.2)])
    check("signal et residu extraits", couples, [(1.2, 1.0)])
    check(
        "critere indisponible ecarte",
        _criteres.observations("arbitre", [_fiche(1.2, dispo=False)]),
        [],
    )
    check(
        "fiche non tranchee ecartee",
        _criteres.observations("arbitre", [{"contexte": {"criteres": []}}]),
        [],
    )
    check("critere inconnu", _criteres.observations("inexistant", [_fiche(1.2)]), [])
    check(
        "les criteres a poids zero sont couverts",
        sorted(_criteres.SIGNAUX),
        ["arbitre", "effectif", "enjeu", "motivation", "systeme"],
    )

    # Le dispositif : c'est sa VARIATION qui informe, jamais son niveau. Le
    # niveau habituel est deja dans le style (critere 1, poids 1), lu des
    # comptages plutot que d'une etiquette.
    check(
        "indice defensif d'un dispositif",
        [_criteres._defensivite(x) for x in ("5-3-2", "4-4-2", "4-3-3", "3-4-3")],
        [3.0, 2.0, 1.0, 0.0],
    )
    check(
        "dispositif illisible ou incomplet",
        [_criteres._defensivite(x) for x in ("", "11", "n/d", "4-4-4")],
        [None, None, None, None],
    )
    check(
        "repli par rapport a l'habitude",
        _criteres._bascule_defensive(
            {
                "domicile": {"systeme_annonce": "5-4-1", "systeme_habituel": "4-3-3"},
                "exterieur": {"systeme_annonce": "4-3-3", "systeme_habituel": "4-3-3"},
            }
        ),
        3.0,
    )
    check(
        "sans composition annoncee, aucun changement constatable",
        _criteres._bascule_defensive(
            {
                "domicile": {"systeme_annonce": "", "systeme_habituel": "4-3-3"},
                "exterieur": {"systeme_annonce": "", "systeme_habituel": "5-3-2"},
            }
        ),
        None,
    )
    check(
        "deux signaux lus dans le meme critere 3",
        [_criteres.CRITERE_PORTEUR[k] for k in ("effectif", "systeme")],
        ["systeme_et_effectif", "systeme_et_effectif"],
    )

    print()
    print("33. Robustesse de la verification")
    # Ce chemin conditionne TOUTE la mesure du projet : sans fiche tranchee, ni
    # le bilan des propositions ni celui du marche n'ont de matiere. Une seule
    # fiche mal formee y faisait echouer le lot entier, silencieusement.
    class _Piegee(dict):
        """Fiche dont la lecture explose : l'imprevu, pas le mal forme."""

        def get(self, cle, defaut=None):
            if cle == "match_id":
                raise RuntimeError("enregistrement corrompu")
            return super().get(cle, defaut)

    # Le fichier est sans reseau : la journee est simulee, sinon le test
    # mesurerait la disponibilite de Flashscore et non la robustesse du code.
    _vrai_get_matches = api_client.get_matches
    api_client.get_matches = lambda *a, **k: [
        {"match_id": "connu", "url": "http://exemple/connu",
         "statut": api_client.SCHEDULED, "domicile": "A", "exterieur": "B"}
    ]

    saine = {
        "match": "A - B", "coup_denvoi_local": "2026-01-02 20:00",
        "url": "u", "match_id": "zzz", "grandeurs": [],
    }
    lot = [
        {"match": "Sans date", "coup_denvoi_local": "", "url": "u",
         "match_id": "m", "grandeurs": []},
        _Piegee(dict(saine, match="Piegee")),
        dict(saine, match="Sans url", url=None),
    ]
    rapport = verify.verify_records(lot)
    check("aucune fiche n'emporte les autres", len(rapport), 3)
    check(
        "chaque cause est nommee",
        [ligne["statut"] for ligne in rapport],
        ["coup d'envoi illisible", "erreur", "en attente"],
    )
    check(
        "l'imprevu est rapporte avec son type",
        "RuntimeError" in rapport[1].get("detail", ""),
        True,
    )
    # Une fiche sans identifiant NI url ne doit se rapprocher de rien : deux
    # valeurs vides ne font pas une correspondance.
    check(
        "pas de rapprochement sur deux valeurs vides",
        verify.verify_records([{"match": "Nue", "coup_denvoi_local": "2026-01-02 20:00",
                                "grandeurs": []}])[0]["statut"],
        "en attente",
    )

    api_client.get_matches = _vrai_get_matches

    # Le rapport distingue ce qui attend de ce qui est bloque : une fiche
    # bloquee ne se tranchera jamais et doit se voir.
    rendu = verify.render([
        {"match": "X - Y", "statut": "en attente"},
        {"match": "A - B", "statut": "erreur", "detail": "RuntimeError : x"},
    ])
    check("l'attente est comptee", "En attente : 1 fiche(s)" in rendu, True)
    check("le blocage est detaille", "Bloquees : 1 fiche(s)" in rendu, True)
    check("la cause du blocage est montree", "RuntimeError" in rendu, True)

    print()
    print("34. Grille des scores et incertitude d'estimation")
    # La grille produit les scores exacts ET les issues. Elle ignorait que
    # lambda est ESTIME, alors que les echelles en tenaient compte : la fiche
    # affichait cote a cote des seuils qui reconnaissaient l'incertitude et des
    # scores qui la niaient.
    check(
        "dispersion 1.0 : grille inchangee",
        predict.score_matrix(1.55, 1.20, 0.0)
        == predict.score_matrix(1.55, 1.20, 0.0, 1.0, 1.0),
        True,
    )
    check(
        "issue inchangee",
        predict.outcome_probabilities(1.55, 1.20, 0.0)
        == predict.outcome_probabilities(1.55, 1.20, 0.0, 1.0, 1.0),
        True,
    )
    check(
        "scores probables inchanges",
        predict.most_likely_scores(1.55, 1.20, 0.0)
        == predict.most_likely_scores(1.55, 1.20, 0.0, 3, 1.0, 1.0),
        True,
    )
    # Une dispersion superieure aplatit la grille : moins de masse sur le mode,
    # davantage sur les scores rares. C'est ce qu'exige un lambda estime sur
    # quatre matchs.
    serree = predict.score_matrix(1.55, 1.20, 0.0, 1.0, 1.0)
    large = predict.score_matrix(1.55, 1.20, 0.0, 1.4, 1.4)
    check("le mode perd de la masse", large[1][1] < serree[1][1], True)
    check("les scores rares en gagnent", large[0][0] > serree[0][0], True)
    check(
        "la grille reste une loi de probabilite",
        abs(sum(sum(ligne) for ligne in large) - 1.0) < 1e-9,
        True,
    )
    check(
        "le reglage atteint desormais la grille",
        predict.effective_dispersion(
            "buts", 4.0, predict.DEFAULT_PARAMS._replace(estimation_dispersion=1.0)
        )
        > 1.0,
        True,
    )

    # La mesure des scores : elle distingue un modele honnete d'un modele qui
    # promet plus qu'il ne tient, ce qu'aucune autre mesure du module ne fait.
    import random as _rnd

    from ibet.evaluation import backtest as _bt

    _rnd.seed(5)

    def _tirer(lam):
        seuil = _rnd.random()
        cumul = 0.0
        for k in range(12):
            cumul += predict.poisson_pmf(k, lam)
            if seuil < cumul:
                return k
        return 12

    def _juger(bruit, n=1500):
        mesure = _bt.ScoreCheck()
        for _ in range(n):
            lh = max(0.2, _rnd.gauss(1.55, 0.45))
            la = max(0.2, _rnd.gauss(1.25, 0.40))
            reel = (_tirer(lh), _tirer(la))
            mesure.add(
                predict.score_matrix(
                    max(0.2, lh + _rnd.gauss(0, bruit)),
                    max(0.2, la + _rnd.gauss(0, bruit)),
                    0.0,
                ),
                predict.score_matrix(1.45, 1.20, 0.0),
                reel,
            )
        return mesure.summary()

    juste, mauvais = _juger(0.0), _juger(0.9)
    check(
        "un modele juste tient ce qu'il annonce",
        abs(juste["taux_tete"] - juste["tete_annoncee"]) < 0.02,
        True,
    )
    check(
        "un mauvais modele promet plus qu'il ne tient",
        mauvais["tete_annoncee"] - mauvais["taux_tete"] > 0.04,
        True,
    )
    check(
        "le log-loss les separe",
        mauvais["log_loss"] > juste["log_loss"] + 0.3,
        True,
    )
    check(
        "le juste bat la reference, le mauvais non",
        (
            juste["vs_reference"]["ecart"] < 0,
            mauvais["vs_reference"]["ecart"] > 0,
        ),
        (True, True),
    )
    check(
        "un score hors grille ne rend pas le log-loss infini",
        _bt.ScoreCheck._probabilite([[0.5, 0.5]], 9, 9) > 0,
        True,
    )

    print()
    print("35. Bilan des propositions emises, par option")
    check(
        "familles reconnues au libelle",
        [
            verify.famille_de(x)
            for x in (
                "Plus de 2.5 buts au total",
                "Arsenal : moins de 1.5 buts",
                "Victoire Arsenal",
                "Match nul",
                "Arsenal ou nul",
                "Entre 2 et 4 buts au total",
                "Les deux equipes marquent",
                "Nombre total de buts pair",
                "Arsenal gagne par 2 buts ou plus",
                "Plus de corners pour Arsenal",
            )
        ],
        [
            "total", "equipe", "issue", "issue", "double chance", "fourchette",
            "les deux marquent", "parite", "ecart", "duel",
        ],
    )
    # « Entre 2 et 4 » contient " et " sans etre un combine : l'ordre des
    # regles compte, et une inversion ferait passer toutes les fourchettes
    # pour des combines.
    check(
        "une fourchette n'est pas un combine",
        verify.famille_de("Entre 2 et 4 buts au total"),
        "fourchette",
    )

    # Le groupement par match, et le plancher qui empeche une fausse etoile.
    #
    # Les matchs doivent DIFFERER entre eux, sinon il n'y a rien a grouper : des
    # groupes identiques ont une variance inter-groupes nulle, et l'erreur type
    # groupee vaut zero -- ce qui est exact, mais ne dit rien du cas reel. Ici
    # un match sur deux voit ses douze propositions se realiser, l'autre non :
    # c'est la correlation extreme, celle qu'une fiche produit quand le score
    # tranche toutes ses lignes dans le meme sens.
    correle = [
        ("m%d" % (i // 12), 0.50, (i // 12) % 2 == 0) for i in range(120)
    ]
    groupe = verify._mesure_groupee(correle)
    check(
        "dix matchs, cent vingt propositions",
        (groupe["matchs"], groupe["propositions"]),
        (10, 120),
    )
    naif = (groupe["observe"] * (1 - groupe["observe"]) / 120) ** 0.5
    check(
        "l'erreur type groupee depasse largement la naive",
        groupe["erreur_type"] > 2 * naif,
        True,
    )
    # Des groupes identiques n'ont rien a apporter : l'erreur type doit alors
    # tomber a zero, et non rester a la valeur naive.
    identiques = [("m%d" % (i // 12), 0.80, i % 12 < 8) for i in range(120)]
    check(
        "groupes identiques : plus aucune incertitude entre matchs",
        verify._mesure_groupee(identiques)["erreur_type"] < 1e-9,
        True,
    )
    maigre = verify._mesure_groupee([("a", 0.7, True), ("b", 0.7, True)])
    check("sous cinq matchs, aucune erreur type", maigre["erreur_type"], None)
    check("et donc aucune etoile", maigre["significatif"], False)
    check("aucune proposition tranchee", verify._mesure_groupee([]), None)

    print()
    print("36. La meilleure option de chaque match")
    fiche_complete = {
        "match": "Alpha - Beta", "match_id": "m1",
        "grandeurs": [
            {"grandeur": "Buts", "cle": "buts",
             "issue": {"domicile": 0.53, "nul": 0.22, "exterieur": 0.25},
             "methode": "Maher sur 73 matchs",
             "offres": [{"pari": "Plus de 1.5 buts au total", "probabilite": 0.82}]},
            {"grandeur": "Corners",
             "offres": [{"pari": "Plus de 9.5 corners au total", "probabilite": 0.55}]},
        ],
    }
    issues = {"domicile": 2.10, "nul": 3.60, "exterieur": 3.90}
    libelles = {"Plus de 1.5 buts au total": 1.28,
                "Plus de 9.5 corners au total": 2.05}
    tous = marche.paris_du_match(fiche_complete, issues, libelles)
    check("les deux marches sont valorises", len(tous), 5)
    check(
        "un libelle sans cote n'est pas valorise",
        len(marche.paris_du_match(fiche_complete, issues, {})),
        3,
    )
    check(
        "le rapprochement se fait sur le libelle EXACT",
        marche.paris_du_match(
            fiche_complete, None, {"Plus de 1,5 buts": 1.28}
        ),
        [],
    )
    meilleure = marche.meilleure_option(tous)
    check(
        "la meilleure option est la plus rentable",
        meilleure["pari"],
        "Plus de 9.5 corners au total",
    )
    # Un ecart demesure ne doit PAS etre recommande : il n'y a qu'une ligne par
    # match, et l'y laisser reviendrait a conseiller ce que le garde-fou refuse.
    demesure = marche.paris_du_match(
        fiche_complete, {"domicile": 9.0, "nul": 3.60, "exterieur": 3.90}, None
    )
    check(
        "l'ecart demesure existe bien dans les options",
        max(p["valeur"] for p in demesure) > marche.VALEUR_DEMESUREE,
        True,
    )
    check(
        "mais il n'est jamais retenu comme meilleure option",
        marche.meilleure_option(demesure),
        None,
    )
    check(
        "donc un match qui n'a que des demesurees ne sort pas",
        marche.par_match(
            [dict(fiche_complete,
                  cotes={"domicile": 9.0, "nul": 3.60, "exterieur": 3.90})]
        ),
        [],
    )
    check(
        "aucune option positive : le match ne sort pas",
        marche.meilleure_option(
            [{"pari": "x", "valeur": -0.1}, {"pari": "y", "valeur": -0.3}]
        ),
        None,
    )
    check(
        "une ligne par match, pas une par option",
        len(marche.par_match([dict(fiche_complete, cotes=issues, cotes_libelles=libelles)])),
        1,
    )
    check(
        "le nombre d'options examinees est rendu",
        marche.par_match(
            [dict(fiche_complete, cotes=issues, cotes_libelles=libelles)]
        )[0]["options_examinees"],
        5,
    )

    print()
    print("37. Cotes agregees : appariement et traduction")
    # Le rapprochement se fait sur les NOMS : les identifiants de l'agregateur
    # ne sont pas ceux de Flashscore. C'est le maillon le plus dangereux de la
    # chaine -- un appariement faux valorise un pari avec le prix d'un autre
    # match, sans jamais lever d'erreur.
    check(
        "abreviations et suffixes reconnus",
        [
            api_client.memes_equipes(g, d)
            for g, d in (
                ("Manchester Utd", "Manchester United"),
                ("Arsenal", "Arsenal FC"),
                ("Paris SG", "Paris Saint-Germain"),
                ("Atl. Madrid", "Atletico Madrid"),
                ("Nottingham", "Nottingham Forest"),
            )
        ],
        [True, True, True, True, True],
    )
    check(
        "deux clubs distincts ne se confondent jamais",
        [
            api_client.memes_equipes(g, d)
            for g, d in (
                ("Manchester United", "Manchester City"),
                ("Real Madrid", "Real Sociedad"),
                ("Inter", "Internazionale"),
            )
        ],
        [False, False, False],
    )
    # Feminines, jeunes et reserves : leurs noms ne different que par un
    # suffixe que la normalisation efface, et une fiche masculine se retrouvait
    # valorisee aux cotes du match feminin.
    check(
        "une variante n'est pas le club premier",
        [
            api_client.memes_equipes(g, d)
            for g, d in (
                ("Everton", "Everton F"),
                ("Arsenal", "Arsenal -19"),
                ("Chelsea", "Chelsea II"),
            )
        ],
        [False, False, False],
    )
    check("nom vide n'apparie rien", api_client.memes_equipes("", "Arsenal"), False)

    fiche_agregee = {"match": "Manchester Utd - Arsenal", "match_id": "m1",
                     "grandeurs": []}
    evenement = {
        "domicile": "Manchester United", "exterieur": "Arsenal FC",
        "cotes": {
            "issues": {"Manchester United": 2.55, "Arsenal FC": 2.70, "Draw": 3.45},
            "totaux": {"Over|2.5": 1.85, "Under|2.5": 1.95, "Over|1.5": 1.28},
        },
    }
    issues, libelles = marche.libelles_depuis_agregateur(fiche_agregee, evenement)
    check(
        "les trois issues sont traduites",
        issues,
        {"domicile": 2.55, "exterieur": 2.70, "nul": 3.45},
    )
    check(
        "les totaux prennent le libelle EXACT de la fiche",
        sorted(libelles),
        ["Moins de 2.5 buts au total", "Plus de 1.5 buts au total",
         "Plus de 2.5 buts au total"],
    )
    # Sans les trois issues, la marge de l'operateur ne peut pas etre retiree et
    # rien ne dit que le rapprochement a porte sur le bon match.
    partiel = dict(evenement, cotes={"issues": {"Draw": 3.45}, "totaux": {}})
    check(
        "des issues incompletes ne sont pas exploitees",
        marche.libelles_depuis_agregateur(fiche_agregee, partiel)[0],
        {},
    )
    autre = dict(evenement, cotes={
        "issues": {"Liverpool": 2.0, "Everton": 3.0, "Draw": 3.4}, "totaux": {}})
    check(
        "un evenement d'un autre match ne donne rien",
        marche.libelles_depuis_agregateur(fiche_agregee, autre)[0],
        {},
    )
    check(
        "sans cle, l'agregateur rend une liste vide et non une erreur",
        api_client.aggregated_odds("2026-09-10") if not os.getenv("ODDS_API_KEY") else [],
        [],
    )

    print()
    print("38. Valorisation par les echelles de la fiche")
    # Le blocage etait la : `select_offers` ne retient que ce qui depasse 60 %,
    # donc « Moins de 5.5 buts », tandis qu'un operateur cote la ligne
    # d'equilibre, « Plus de 2.5 buts ». Les deux ensembles ne se rencontraient
    # jamais et l'agregateur ne servait a rien. Les echelles, elles, portent
    # toutes les lignes.
    fiche_echelles = {
        "match": "Alpha - Beta", "match_id": "m1",
        "grandeurs": [{
            "grandeur": "Buts",
            "echelle_total": {"seuils": [1.5, 2.5],
                              "probabilites": [0.82, 0.61]},
            "echelle_par_equipe": {"seuils": [0.5, 1.5],
                                   "Alpha": [0.85, 0.55],
                                   "Beta": [0.78, 0.44]},
            "offres": [{"pari": "Plus de 1.5 buts au total", "probabilite": 0.82}],
        }],
    }
    lignes = marche.propositions_des_echelles(fiche_echelles)
    check("les deux faces de chaque seuil sont rendues", len(lignes), 12)
    check(
        "le libelle du total est celui d'offer_candidates",
        lignes.get("Plus de 2.5 buts au total"),
        0.61,
    )
    check(
        "la face complementaire vaut 1 - p",
        round(lignes.get("Moins de 2.5 buts au total"), 4),
        0.39,
    )
    check(
        "le libelle par equipe aussi",
        (
            lignes.get("Alpha : plus de 1.5 buts"),
            round(lignes.get("Beta : moins de 0.5 buts"), 4),
        ),
        (0.55, 0.22),
    )
    check("la ligne 'seuils' n'est pas prise pour une equipe",
          any("seuils :" in libelle for libelle in lignes), False)

    # Une proposition RETENUE garde son entree : elle porte le verdict du
    # verificateur, que l'echelle ne connait pas.
    tranchee = {
        "match": "Alpha - Beta", "match_id": "m1",
        "resultat_reel": {"score": "2 - 1"},
        "grandeurs": [dict(
            fiche_echelles["grandeurs"][0],
            offres=[{"pari": "Plus de 1.5 buts au total",
                     "probabilite": 0.82, "verifie": True}],
        )],
    }
    valorisees = marche.paris_du_match(
        tranchee, None, {"Plus de 1.5 buts au total": 1.30,
                         "Plus de 2.5 buts au total": 1.84}
    )
    check("les deux lignes sont valorisees", len(valorisees), 2)
    verdicts = {p["pari"]: p["verifie"] for p in valorisees}
    check(
        "le verdict de la proposition retenue est conserve",
        verdicts.get("Plus de 1.5 buts au total"),
        True,
    )
    check(
        "une ligne d'echelle non tranchee n'invente aucun verdict",
        verdicts.get("Plus de 2.5 buts au total"),
        None,
    )
    check(
        "une fiche sans echelle ne produit rien",
        marche.propositions_des_echelles({"grandeurs": [{"grandeur": "Buts"}]}),
        {},
    )

    # La competition doit etre reconnue AVEC son pays : « Premier League »
    # designe une dizaine de championnats dans le monde.
    check(
        "cle d'agregateur resolue par (pays, competition)",
        [
            api_client.cle_agregateur(c, p)
            for c, p in (
                ("Premier League", "Angleterre"),
                ("Premier League", "Kazakhstan"),
                ("Ligue des Champions - Phase de ligue", "Europe"),
                ("Girabola", "Angola"),
            )
        ],
        ["soccer_epl", "", "soccer_uefa_champs_league", ""],
    )
    check(
        "sans cle de competition, aucune requete",
        api_client.aggregated_odds("", "2026-09-10"),
        [],
    )

    print("\nX. Marge de l'operateur, prix de reference, valeur a la cloture")
    # Marge : somme des inverses moins 1. Un 1X2 a 1.90 / 3.60 / 4.20.
    cotes_1x2 = {"domicile": 1.90, "nul": 3.60, "exterieur": 4.20}
    check("marge lue", round(marche.overround(cotes_1x2), 4), 0.0422)
    check("marge d'un livre juste", round(marche.overround(
        {"domicile": 3.0, "nul": 3.0, "exterieur": 3.0}), 6), 0.0)
    check("cote inexploitable -> pas de marge",
          marche.overround({"domicile": 1.0, "nul": 3.0, "exterieur": 3.0}), None)

    # Toutes les methodes rendent une vraie distribution, sauf "brute".
    for methode in ("proportionnelle", "puissance", "shin"):
        p = marche.probabilites_implicites(cotes_1x2, methode)
        check("%s somme a 1" % methode, round(sum(p.values()), 9), 1.0)
        check("%s garde l'ordre" % methode,
              p["domicile"] > p["nul"] > p["exterieur"], True)
    brute = marche.probabilites_implicites(cotes_1x2, "brute")
    check("brute = inverses", round(brute["domicile"], 6), round(1 / 1.90, 6))
    check("brute somme au-dessus de 1", sum(brute.values()) > 1.0, True)

    # Le biais favori-outsider : Shin et la puissance retirent PLUS de marge a
    # l'outsider qu'au favori. La normalisation proportionnelle, elle, la
    # repartit uniformement -- c'est ce qui faisait surestimer les outsiders,
    # donc voir de la valeur la ou il n'y en avait pas.
    prop = marche.probabilites_implicites(cotes_1x2, "proportionnelle")
    shin = marche.probabilites_implicites(cotes_1x2, "shin")
    check("Shin releve le favori", shin["domicile"] > prop["domicile"], True)
    check("Shin abaisse l'outsider", shin["exterieur"] < prop["exterieur"], True)
    check("aucun releve -> rien", marche.probabilites_implicites({}), {})

    # Operateurs de reference : ceux dont le prix sert d'estimation.
    check("Pinnacle est une reference", marche.est_reference("pinnacle"), True)
    check("Betfair est une reference", marche.est_reference("Betfair Exchange"), True)
    check("un operateur quelconque ne l'est pas",
          marche.est_reference("bookmaker du coin"), False)
    check("le rang classe les references",
          marche.rang_operateur("pinnacle") < marche.rang_operateur("inconnu"), True)

    # Une ligne composite -- le meilleur prix de chaque issue chez des
    # operateurs differents -- a une marge nulle ou negative et ne doit JAMAIS
    # servir de reference : les trois prix ne viennent pas du meme livre.
    composite = {"domicile": 2.10, "nul": 3.90, "exterieur": 4.60}
    check("la ligne composite a une marge negative",
          marche.overround(composite) < 0, True)
    releves = [
        {"operateur": "assemblage", "releve_le": "2026-09-07T12:00", "cotes": composite},
        {"operateur": "operateur", "releve_le": "2026-09-07T11:00", "cotes": cotes_1x2},
    ]
    reference = marche.prix_de_reference(releves)
    check("la composite est ecartee", reference["operateur"], "operateur")
    check("la reference porte sa marge", round(reference["marge"], 4), 0.0422)
    check("la reference porte ses probabilites",
          round(sum(reference["probabilites"].values()), 9), 1.0)
    # Un operateur de reference passe devant, meme avec une marge plus forte.
    releves.append({"operateur": "pinnacle", "releve_le": "2026-09-07T10:00",
                    "cotes": {"domicile": 1.88, "nul": 3.55, "exterieur": 4.10}})
    check("Pinnacle passe devant",
          marche.prix_de_reference(releves)["operateur"], "pinnacle")
    check("marge hors bornes -> aucune reference",
          marche.prix_de_reference([
              {"operateur": "x", "releve_le": "", "cotes":
               {"domicile": 1.5, "nul": 3.0, "exterieur": 3.0}}]),
          {})

    # Valeur a la cloture : a-t-on pris un meilleur prix que celui vers lequel
    # le marche a converge ?
    check("cloture battue", round(marche.valeur_a_la_cloture(2.10, 2.00), 4), 0.05)
    check("cloture perdue", round(marche.valeur_a_la_cloture(1.90, 2.00), 4), -0.05)
    check("cote inexploitable", marche.valeur_a_la_cloture(2.0, 1.0), None)

    # Melange : les deux extremes rendent chacune des deux sources.
    modele = {"domicile": 0.50, "nul": 0.30, "exterieur": 0.20}
    marche_p = {"domicile": 0.70, "nul": 0.20, "exterieur": 0.10}
    check("poids 1 -> le modele seul",
          marche.melanger(modele, marche_p, 1.0), modele)
    check("poids 0 -> le marche seul",
          marche.melanger(modele, marche_p, 0.0), marche_p)
    milieu = marche.melanger(modele, marche_p, 0.5)
    check("melange somme a 1", round(sum(milieu.values()), 9), 1.0)
    check("melange entre les deux",
          modele["domicile"] < milieu["domicile"] < marche_p["domicile"], True)
    check("sans marche -> le modele", marche.melanger(modele, {}, 0.3), modele)

    print("\nY. Force des equipes sur une echelle commune")
    # L'historique est vu depuis l'equipe suivie : il faut le remettre a
    # l'endroit avant d'en faire un match, sinon l'avantage du terrain -- qui
    # est un terme du modele -- serait estime sur un melange des deux lieux.
    entree = {"match_id": "m1", "kickoff_utc": "2026-01-01T00:00:00+00:00",
              "adversaire": "Adverse", "lieu": "exterieur", "buts_pour": 0,
              "buts_contre": 3, "competition": "L1", "amical": False}
    remis = forces._match_depuis_entree("Suivie", entree)
    check("le receveur est l'adversaire", remis["domicile"], "Adverse")
    check("le score suit le receveur",
          (remis["buts_domicile"], remis["buts_exterieur"]), (3, 0))
    check("l'amical est ecarte",
          forces._match_depuis_entree("Suivie", dict(entree, amical=True)), None)

    # Groupes : les mises a jour sont a somme nulle entre les deux equipes, donc
    # la moyenne d'un groupe ferme ne bouge jamais. Deux equipes qui ne se sont
    # jamais rencontrees, meme indirectement, portent des notes construites dans
    # deux jeux separes : les soustraire reviendrait a comparer des Celsius a
    # des Fahrenheit.
    corpus_groupes = [
        {"kickoff_utc": "2026-01-01", "domicile": "A", "exterieur": "B",
         "buts_domicile": 1, "buts_exterieur": 0},
        {"kickoff_utc": "2026-01-02", "domicile": "B", "exterieur": "C",
         "buts_domicile": 2, "buts_exterieur": 1},
        {"kickoff_utc": "2026-01-03", "domicile": "X", "exterieur": "Y",
         "buts_domicile": 3, "buts_exterieur": 0},
    ]
    groupes = forces.composantes(corpus_groupes)
    check("A, B et C dans le meme groupe",
          groupes["A"] == groupes["B"] == groupes["C"], True)
    check("X et Y a part", groupes["X"] == groupes["Y"] != groupes["A"], True)
    check("le plus grand groupe porte le numero 0", groupes["A"], 0)

    print("\nZ. Notes attaque / defense : construction et garde-fous")
    # La Forte bat la Faible 3-0, chez elle comme chez l'autre : l'alternance
    # separe la force de l'avantage du terrain, que l'estimation mesure aussi.
    corpus_forces = [
        {"kickoff_utc": "2026-01-%02d" % (j + 1),
         "domicile": "Forte" if j % 2 == 0 else "Faible",
         "exterieur": "Faible" if j % 2 == 0 else "Forte",
         "buts_domicile": 3 if j % 2 == 0 else 0,
         "buts_exterieur": 0 if j % 2 == 0 else 3,
         "xg_domicile": 2.8, "xg_exterieur": 0.4}
        for j in range(20)
    ]
    notes = forces.construire(corpus_forces, "buts_domicile", "buts_exterieur")
    lam_dom, lam_ext = notes.lambdas("Forte", "Faible")
    check("l'attaque qui marque monte", notes.attaque["Forte"] > 0, True)
    check("la defense qui encaisse baisse", notes.defense["Faible"] < 0, True)
    check("les lambdas suivent", lam_dom > lam_ext, True)
    check("notes etablies apres 20 matchs",
          notes.etablies("Forte", "Faible"), True)
    check("notes non etablies pour une inconnue",
          notes.etablies("Forte", "Jamais vue"), False)
    # Les notes sont bornees : une serie aberrante ne les envoie pas a l'infini.
    check("notes bornees",
          max(abs(v) for v in notes.attaque.values()) <= forces.PLAFOND, True)
    # L'estimation retrouve le score moyen : vingt 3-0 donnent un lambda proche
    # de 3 pour la Forte, et proche de 0 pour la Faible. L'a priori (un match
    # fictif a la moyenne) les retient un peu, sans les ecraser : c'est ce que
    # l'apprentissage en ligne ne savait pas faire.
    check("le lambda dominant approche le score reel",
          2.5 < lam_dom <= 3.0, True)
    check("le lambda domine approche zero", lam_ext < 0.3, True)

    # Trois equipes, aller et retour, tous les matchs a 1-1. Personne n'est
    # meilleur ni avantage par le terrain : les notes doivent etre egales, et
    # la moyenne de reference celle du corpus, un but par equipe.
    cercle = []
    for j, (dom, ext) in enumerate(
        [("P", "Q"), ("Q", "R"), ("R", "P"), ("Q", "P"), ("R", "Q"), ("P", "R")] * 4
    ):
        cercle.append({"kickoff_utc": "2026-02-%02d" % (j + 1), "domicile": dom,
                       "exterieur": ext, "buts_domicile": 1, "buts_exterieur": 1})
    egales = forces.construire(cercle, "buts_domicile", "buts_exterieur")
    check("forces egales, attaques egales",
          max(egales.attaque.values()) - min(egales.attaque.values()) < 1e-6, True)
    check("la moyenne de reference est celle du corpus",
          round(math.exp(egales.base + egales.attaque["P"]), 4), 1.0)

    # L'oubli : un vieux match pese moins qu'un recent. La meme equipe gagne
    # 4-0 il y a deux ans puis perd 0-4 hier ; sa note doit pencher vers hier.
    oubli = [
        {"kickoff_utc": "2024-03-01", "domicile": "U", "exterieur": "V",
         "buts_domicile": 4, "buts_exterieur": 0},
        {"kickoff_utc": "2026-03-01", "domicile": "U", "exterieur": "V",
         "buts_domicile": 0, "buts_exterieur": 4},
    ]
    recente = forces.construire(oubli, "buts_domicile", "buts_exterieur")
    check("un vieux match pese moins qu'un recent",
          recente.attaque["V"] > recente.attaque["U"], True)
    sans_oubli = forces.construire(
        oubli, "buts_domicile", "buts_exterieur", oubli=0.0
    )
    check("sans oubli, les deux matchs se compensent",
          abs(sans_oubli.attaque["U"] - sans_oubli.attaque["V"]) < 1e-6, True)

    # Une cible absente (xG non releve) ne deplace aucune note, et le match ne
    # compte pas : des notes de xG ne s'etablissent que sur des xG.
    sans_cible = forces.construire(
        [{"kickoff_utc": "2026-01-01", "domicile": "S", "exterieur": "T",
          "buts_domicile": 2, "buts_exterieur": 1}],
        "xg_domicile", "xg_exterieur",
    )
    check("sans cible, aucune note", sans_cible.attaque, {})
    check("et le match n'etablit rien", sans_cible.joues, {})

    paquet_forces = {
        "base": 0.34, "avantage": 0.10,
        "groupes": {"A": 0, "B": 0, "X": 1},
        "buts": {"attaque": {"A": 0.3, "B": 0.0, "X": 0.5},
                 "defense": {"A": 0.1, "B": -0.2, "X": 0.0},
                 "joues": {"A": 30, "B": 30, "X": 30}},
        "xg": {"attaque": {}, "defense": {}, "joues": {}},
    }
    dans_le_groupe = forces.lambdas_attendus("A", "B", paquet_forces)
    check("lambdas rendus dans le meme groupe", dans_le_groupe is not None, True)
    check("deux lambdas positifs", min(dans_le_groupe) > 0, True)
    check("refus entre groupes",
          forces.lambdas_attendus("A", "X", paquet_forces), None)
    check("refus si notes non etablies",
          forces.lambdas_attendus("A", "Inconnue", paquet_forces), None)
    # L'avantage du terrain joue dans le bon sens, a forces egales.
    egal = {
        "base": 0.34, "avantage": 0.10,
        "groupes": {"P": 0, "Q": 0},
        "buts": {"attaque": {"P": 0.0, "Q": 0.0},
                 "defense": {"P": 0.0, "Q": 0.0},
                 "joues": {"P": 30, "Q": 30}},
        "xg": {"attaque": {}, "defense": {}, "joues": {}},
    }
    couple = forces.lambdas_attendus("P", "Q", egal)
    check("l'avantage du terrain penche a domicile", couple[0] > couple[1], True)

    print("\nZ1. Les deux jeux de notes : les xG pour le volume")
    # Le TOTAL vient des notes sur les xG, l'ECART de celles sur les buts.
    deux_jeux = {
        "base": 0.34, "avantage": 0.0,
        "groupes": {"M": 0, "N": 0},
        # Sur les buts : M bien plus fort que N.
        "buts": {"attaque": {"M": 0.5, "N": -0.5},
                 "defense": {"M": 0.0, "N": 0.0},
                 "joues": {"M": 30, "N": 30}},
        # Sur les xG : match beaucoup plus ouvert des deux cotes.
        "xg": {"attaque": {"M": 0.4, "N": 0.4},
               "defense": {"M": 0.0, "N": 0.0},
               "joues": {"M": 30, "N": 30}},
    }
    melange = forces.lambdas_attendus("M", "N", deux_jeux)
    jeu_buts = forces._jeu(deux_jeux, "buts").lambdas("M", "N")
    jeu_xg = forces._jeu(deux_jeux, "xg").lambdas("M", "N")
    check("le total vient des xG",
          round(sum(melange), 6), round(sum(jeu_xg), 6))
    check("l'ecart vient des buts",
          round(melange[0] - melange[1], 6),
          round(jeu_buts[0] - jeu_buts[1], 6))
    # Sans notes de xG, les notes sur les buts font les deux moities.
    sans_xg = dict(deux_jeux, xg={"attaque": {}, "defense": {}, "joues": {}})
    check("repli sur les buts sans xG",
          tuple(round(x, 6) for x in forces.lambdas_attendus("M", "N", sans_xg)),
          tuple(round(x, 6) for x in jeu_buts))

    # Le total est resserre vers la moyenne de la competition, l'ecart garde.
    # Nom normalise : le suffixe de phase du flux du jour ne doit pas perdre la
    # moyenne.
    resserre = dict(sans_xg, total_moyen=2.5, totaux={"Ligue Test": 2.0})
    brut = sum(jeu_buts)
    lam_r = forces.lambdas_attendus("M", "N", resserre, "Ligue Test - Cloture")
    check("total resserre vers la competition",
          round(sum(lam_r), 6),
          round(2.0 + forces.RESSERREMENT_TOTAL * (brut - 2.0), 6))
    check("l'ecart n'est pas resserre",
          round(lam_r[0] - lam_r[1], 6), round(jeu_buts[0] - jeu_buts[1], 6))
    lam_g = forces.lambdas_attendus("M", "N", resserre, "Inconnue")
    check("competition inconnue -> moyenne globale",
          round(sum(lam_g), 6),
          round(2.5 + forces.RESSERREMENT_TOTAL * (brut - 2.5), 6))
    globale, par_competition = forces.totaux_par_competition([
        {"competition": "Ligue Test - Aller", "buts_domicile": 3, "buts_exterieur": 1},
        {"competition": "Autre", "buts_domicile": 0, "buts_exterieur": 0},
    ])
    check("moyenne globale des buts", globale, 2.0)
    check("moyenne de competition tiree vers la globale",
          round(par_competition["Ligue Test"], 6),
          round((4 + forces.TOTAL_A_PRIORI * 2.0) / (1 + forces.TOTAL_A_PRIORI), 6))

    print("\nZ1b. Issue combinee aux cotes du marche")
    from ibet.modeles import buts as modele_buts

    marche_p = predict.probabilites_du_marche(
        {"domicile": 1.40, "nul": 4.80, "exterieur": 8.00}
    )
    check("probabilites du marche sommees a 1",
          round(sum(marche_p.values()), 6), 1.0)
    check("cotes incompletes -> pas de marche",
          predict.probabilites_du_marche({"domicile": 1.40}), None)
    check("sans cotes -> pas de marche", predict.probabilites_du_marche(None), None)

    lam = (1.3, 1.2)
    modele_b = modele_buts.ModeleButs()
    intact = modele_b.caler(lam, {"marche": None}, 0.0, 1.173, 1.173)
    check("sans cotes, rien n'est releve", intact, (1.3, 1.2, None))
    lh, la, trace = modele_b.caler(lam, {"marche": marche_p}, 0.0, 1.173, 1.173)
    check("les nombres attendus ne bougent pas", (lh, la), lam)

    # La fiche complete : issue combinee, nombres de buts intacts.
    estimation_b = {"lambda": lam, "effectif_efficace": (10.0, 10.0),
                    "matchs_utilises": (10, 10), "matchs_corriges": (0, 0),
                    "methode": "test", "restreint_competition": False}
    seule = modele_b.prevoir(estimation_b, ("A", "B"), None, predict.DEFAULT_PARAMS)
    combinee = modele_b.prevoir(estimation_b, ("A", "B"), None, predict.DEFAULT_PARAMS,
                                apports={"marche": marche_p})
    w = modele_buts.POIDS_MODELE_ISSUE
    attendu = {k: w * seule["resultat"][k] + (1 - w) * marche_p[k] for k in marche_p}
    check("issue = melange modele / bookmaker",
          {k: round(v, 6) for k, v in combinee["resultat"].items()},
          {k: round(v, 6) for k, v in attendu.items()})
    check("somme de l'issue combinee",
          round(sum(combinee["resultat"].values()), 6), 1.0)
    check("les seuils de buts ne changent pas",
          combinee["echelles"]["total"], seule["echelles"]["total"])
    check("la trace garde l'issue du modele seul",
          combinee["marche"]["issue_modele"]["domicile"],
          round(seule["resultat"]["domicile"], 4))
    victoire = next(o for o in modele_b.issue.candidats(
        ("A", "B"), combinee["resultat"], 0.5) if o["libelle"] == "Victoire A")
    check("les propositions d'issue suivent le melange",
          round(victoire["p"], 6), round(attendu["domicile"], 6))
    check("sans cotes, fiche identique a la version sans marche",
          modele_b.prevoir(estimation_b, ("A", "B"), None, predict.DEFAULT_PARAMS,
                           apports={"marche": None})["resultat"], seule["resultat"])

    print("\nZ2. Notes attaque / defense dans le modele")
    # Sans notes, le modele est rendu intact : une prevision n'est jamais
    # degradee par l'absence d'une information.
    check("sans notes -> le modele intact",
          predict.melanger_lambdas((1.35, 1.20), None), (1.35, 1.20))
    check("poids 1 -> le modele seul",
          predict.melanger_lambdas((1.35, 1.20), (2.60, 0.90), 1.0), (1.35, 1.20))
    check("poids 0 -> les notes seules",
          predict.melanger_lambdas((1.35, 1.20), (2.60, 0.90), 0.0), (2.60, 0.90))
    milieu = predict.melanger_lambdas((1.35, 1.20), (2.60, 0.90), 0.5)
    check("melange entre les deux",
          (round(milieu[0], 4), round(milieu[1], 4)), (1.975, 1.05))
    # Le melange porte aussi sur le TOTAL, ce qu'un simple classement de force
    # ne saurait pas faire.
    check("le total suit les notes",
          round(sum(predict.melanger_lambdas((1.35, 1.20), (2.60, 0.90), 0.0)), 4),
          3.5)
    check("lambda toujours positif",
          min(predict.melanger_lambdas((1.0, 1.0), (0.0, 0.0), 0.0))
          >= predict.LAMBDA_MIN, True)
    # Le poids retenu est zero, et deux mesures independantes le disent.
    check("poids du modele mesure a zero", predict.POIDS_MODELE, 0.0)

    # Le retrecissement, mesure sur le SEUIL et non sur l'issue : le modele
    # sur-etalait ses lambdas (pente 0.43 sur les corners), ce qui poussait les
    # probabilites de seuil trop loin vers la certitude.
    check("retrecissement mesure a 22", predict.SHRINKAGE, 22.0)
    # Un ratio de 1.6 observe sur 5 matchs doit ressortir plus pres de 1
    # qu'avant : c'est tout l'effet recherche.
    check("un k plus grand ramene plus pres de 1",
          predict._shrink(1.6, 5, 22.0) < predict._shrink(1.6, 5, 10.0), True)
    check("et n'inverse jamais le sens", predict._shrink(1.6, 5, 22.0) > 1.0, True)
    # A echantillon infini, le retrecissement s'efface : l'observation gagne.
    check("un grand echantillon l'emporte",
          round(predict._shrink(1.6, 10000, 22.0), 2), 1.6)

    print("\nZ3. Fraicheur des notes de force")
    # Le defaut que cette mesure attrape : un fichier de notes se perime en
    # silence. Il repond toujours, avec des valeurs plausibles, simplement
    # calculees sur moins de matchs qu'il n'en existe -- et des equipes
    # pourtant couvertes restent sans note. Les selections y ont laisse la
    # totalite de leurs fiches.
    vrai_corpus = forces.corpus_depuis_cache
    try:
        forces.corpus_depuis_cache = lambda *a, **k: [{}] * 1000
        check("a jour -> aucun retard",
              forces.fraicheur({"matchs": 1000})["a_refaire"], False)
        check("un leger retard reste tolere",
              forces.fraicheur({"matchs": 950})["a_refaire"], False)
        etat = forces.fraicheur({"matchs": 700})
        check("un tiers de retard appelle une reconstruction",
              etat["a_refaire"], True)
        check("et le retard est chiffre", round(etat["retard"], 2), 0.3)
        # Un paquet vide, c'est l'absence de notes, pas leur peremption :
        # avertir la serait envoyer refaire un fichier qui n'existe pas.
        check("aucune note -> pas d'avertissement",
              forces.fraicheur({})["a_refaire"], False)
        forces._forces = {"matchs": 700}
        check("la phrase nomme les deux nombres",
              all(x in forces.avertissement() for x in ("700", "1000")), True)
        forces._forces = {"matchs": 1000}
        check("et se tait quand tout va bien", forces.avertissement(), "")
    finally:
        forces.corpus_depuis_cache = vrai_corpus
        forces._forces = None

    print("\nZ4. Archive des resultats")
    # La base est celle du projet : on n'y ecrit que des identifiants a soi,
    # et on les retire a la fin. Ecrire dans une base a part ne testerait pas
    # le schema reellement en service.
    TEMOIN = "ZZ-test-archive"
    store.init()
    try:
        vivant = {
            "match_id": TEMOIN, "domicile": "A", "exterieur": "B",
            "statut": "En cours", "score_domicile": 0, "score_exterieur": 0,
        }
        # Le garde-fou central : un score de match EN COURS n'est pas un
        # resultat. L'archiver reviendrait a trancher une fiche sur un 0-0 de
        # la vingtieme minute -- une erreur silencieuse, pire que le trou.
        check("un match en cours n'est pas archive",
              store.archiver_resultat(vivant), False)
        check("et rien n'a ete ecrit", store.resultat(TEMOIN), None)

        fini = dict(vivant, statut="Termine", score_domicile=2, score_exterieur=1)
        check("un match termine est archive", store.archiver_resultat(fini), True)
        check("relu tel quel",
              (store.resultat(TEMOIN)["score_domicile"],
               store.resultat(TEMOIN)["score_exterieur"]), (2, 1))

        # Les statistiques arrivent souvent au second passage : elles doivent
        # s'ajouter sans effacer le score, et surtout ne pas etre effacees par
        # un re-archivage qui, lui, ne les a pas.
        store.archiver_resultat(fini, {"domicile": {"corners": 5}})
        check("les statistiques s'ajoutent",
              store.resultat(TEMOIN)["stats"], {"domicile": {"corners": 5}})
        store.archiver_resultat(fini)
        check("et survivent a un re-archivage",
              store.resultat(TEMOIN)["stats"], {"domicile": {"corners": 5}})

        # Une journee n'archive que ses matchs finis, en une transaction.
        journee = [fini, vivant, dict(vivant, match_id=TEMOIN + "2")]
        check("une journee n'archive que les termines",
              store.archiver_journee(journee), 1)

        # L'historique est vu depuis une equipe : "j'ai marque 2 a l'exterieur"
        # doit ressortir en "0 - 2". C'est la seule voie qui franchisse la
        # fenetre de sept jours de la source.
        sides = [{"equipe": "B", "matchs": [{
            "match_id": TEMOIN + "3", "adversaire": "C", "lieu": "exterieur",
            "buts_pour": 2, "buts_contre": 0, "kickoff_utc": "2026-01-01T00:00:00+00:00",
        }]}]
        lignes = store.lignes_dun_historique(sides)
        remis = lignes[TEMOIN + "3"]
        check("le receveur est l'adversaire", remis["domicile"], "C")
        check("le score est remis a l'endroit",
              (remis["score_domicile"], remis["score_exterieur"]), (0, 2))
        check("archive depuis un historique",
              store.archiver_historique(sides), 1)

        # Une ligne sans identifiant ou sans score ne doit pas entrer : une
        # ligne vide empecherait la vraie de s'ecrire plus tard.
        check("pas d'identifiant, pas de ligne",
              store.archiver_resultat(dict(fini, match_id="")), False)
        check("pas de score, pas de ligne",
              store.archiver_resultat(dict(fini, score_domicile=None)), False)
    finally:
        with store.connect() as connexion:
            connexion.execute(
                "DELETE FROM resultats WHERE match_id LIKE ?", (TEMOIN + "%",)
            )
    check("les temoins sont retires", store.resultat(TEMOIN), None)

    print("\nZ5. Versions des modeles et journal")
    import re

    from ibet import modeles
    from ibet.evaluation import etude

    versions = modeles.versions()
    check("chaque modele a une version",
          sorted(versions),
          sorted(["moteur", "issue"] + [m.cle for m in modeles.MODELES + modeles.AUXILIAIRES]))
    check("versions au format MAJEURE.MINEURE.CORRECTIF",
          all(re.fullmatch(r"\d+\.\d+\.\d+", v) for v in versions.values()), True)
    # Une version en service sans section dans son journal est une version
    # qu'on ne saura pas relire : le test l'interdit.
    non_documentees = []
    for cle, version in versions.items():
        chemin = modeles.JOURNAL / ("%s.md" % cle)
        texte = chemin.read_text(encoding="utf-8") if chemin.exists() else ""
        if not re.search(r"^## %s\b" % re.escape(version), texte, re.MULTILINE):
            non_documentees.append("%s %s" % (cle, version))
    check("chaque version en service est documentee", non_documentees, [])
    check("les versions sont inscrites dans la fiche",
          predict.build({"championnat": "Liga", "statut": "A venir"},
                        fiche_form, full)["versions"], versions)
    check("l'issue est attribuee a son modele",
          etude.modele_de("Buts", "double chance"), "issue")
    check("les seuils restent au modele de la grandeur",
          etude.modele_de("Corners", "total"), "corners")

    print("\nZ6. Cartons 2.0.0 : feuille de match et discipline")
    from ibet.modeles.cartons import ModeleCartonsJaunes
    from ibet.modeles.discipline import Discipline, groupe_de_poste
    from ibet.sources.api_client import (
        FS_BLOCK,
        FS_FIELD,
        FS_KV,
        _fs_parse_incidents,
        _temps_de_jeu,
    )

    def _bloc_fs(*paires: tuple[str, str]) -> str:
        return FS_FIELD.join("%s%s%s" % (k, FS_KV, v) for k, v in paires) + FS_FIELD

    # Un changement porte deux incidents dans le meme bloc ; le flux annonce
    # "entrant" le titulaire qui sort. Le sens doit se deduire de l'onze.
    fil = FS_BLOCK.join([
        _bloc_fs(("III", "a"), ("IA", "1"), ("IB", "65'"),
                 ("IE", "6"), ("IF", "Titulaire T."), ("IK", "Changement - Entrant"), ("IM", "t1"),
                 ("IE", "7"), ("IF", "Remplacant R."), ("IK", "Changement - Sortant"), ("IM", "r1")),
        _bloc_fs(("III", "b"), ("IA", "1"), ("IB", "70'"),
                 ("IE", "1"), ("IF", "Remplacant R."), ("IK", "Carton Jaune"), ("IM", "r1")),
        _bloc_fs(("III", "c"), ("IA", "2"), ("IB", "90+3'"),
                 ("IE", "2"), ("IF", "Autre A."), ("IK", "Carton Rouge"), ("IM", "x1")),
    ])
    incidents = _fs_parse_incidents(fil)
    check("incidents : un changement et deux cartons",
          sorted(i["type"] for i in incidents), ["changement", "changement", "jaune", "rouge"])
    check("temps additionnel ramene a sa periode",
          [i["minute"] for i in incidents if i["type"] == "rouge"], [90])
    onze = {"onze": [{"joueur": "Titulaire T.", "id": "t1", "place": 6}] +
            [{"joueur": "J%d" % k, "id": "j%d" % k, "place": k} for k in range(1, 6)],
            "banc": [{"joueur": "Remplacant R.", "id": "r1"}]}
    joues = {j["id"]: j for j in _temps_de_jeu(onze, "domicile", incidents)}
    check("le titulaire sort malgre le libelle 'entrant'", joues["t1"]["minutes"], 65)
    check("le remplacant entre et joue 25 minutes", joues["r1"]["minutes"], 25)
    check("le remplacant herite du poste", joues["r1"]["place"], 6)

    check("poste : gardien", groupe_de_poste(1, "1-4-2-3-1"), "G")
    check("poste : defenseur", groupe_de_poste(4, "1-4-2-3-1"), "D")
    check("poste : milieu", groupe_de_poste(8, "1-4-2-3-1"), "M")
    check("poste : attaquant", groupe_de_poste(11, "1-4-2-3-1"), "A")

    def _feuille(date, arbitre, jd, je, dom="A", ext="B"):
        return {"date": date, "competition": "L", "domicile": dom, "exterieur": ext,
                "arbitre": arbitre, "feuille": {"arbitre": arbitre, "cartons": []},
                "stats": {"domicile": {"cartons_jaunes": jd}, "exterieur": {"cartons_jaunes": je}}}

    # Equipes toutes differentes : sinon l'historique des equipes absorberait
    # l'arbitre, ce qui est voulu -- l'attendu d'un arbitre est celui des
    # equipes qu'il a eues.
    index = Discipline().alimenter(
        [_feuille("2026-01-%02d" % k, "Severe S.", 4, 4, "S%d" % k, "T%d" % k) for k in range(1, 21)]
        + [_feuille("2026-01-%02d" % k, "Doux D.", 1, 1, "D%d" % k, "E%d" % k) for k in range(1, 21)]
    )
    severe = index.arbitre("Severe S.", "2026-03-01")
    check("un arbitre severe a un rapport > 1", severe["rapport"] > 1.0, True)
    check("un arbitre doux a un rapport < 1", index.arbitre("Doux D.", "2026-03-01")["rapport"] < 1.0, True)
    check("aucune fuite : avant son premier match, l'arbitre est inconnu",
          index.arbitre("Severe S.", "2026-01-01")["matchs"], 0)
    inconnu = index.arbitre("Personne P.", "2026-03-01")
    check("arbitre inconnu : rapport neutre", inconnu["rapport"], 1.0)
    check("arbitre inconnu : plus incertain qu'un arbitre vu vingt fois",
          inconnu["variance"] > severe["variance"] / severe["rapport"] ** 2, True)

    cartons = ModeleCartonsJaunes()
    check("sans apport, les cartons 2.0.0 valent le 1.0.0",
          cartons.ajuster((2.0, 2.5), None)[:2], (2.0, 2.5))
    lam = cartons.ajuster((2.0, 2.5), {"arbitre": {"rapport": 1.2, "variance": 0.01}})
    check("l'arbitre deplace les deux cotes dans le meme sens",
          (round(lam[0], 3), round(lam[1], 3)),
          (round(2.0 * 1.2 ** cartons.poids_arbitre, 3), round(2.5 * 1.2 ** cartons.poids_arbitre, 3)))
    check("un arbitre inconnu elargit la correlation du total",
          cartons.correlation_du_match(2.0, 2.5, {"arbitre": {"rapport": 1.0, "variance": 0.08}})
          >= cartons.correlation, True)

    print("\nZ7. Styles des joueurs : profils et onze du jour")
    from ibet.modeles import styles as st

    def _joueur(jid: str, poste: str, stats: dict, titulaire: bool = True) -> dict:
        return {"id": jid, "nom": jid, "poste": poste, "titulaire": titulaire,
                "minutes": 90, "stats": dict(stats, minutes=90)}

    def _match_styles(jour: int, centreur: str) -> dict:
        onze = [_joueur("g", "G", {})] + [
            _joueur("d%d" % k, "D", {"centres": 1}) for k in range(1, 5)
        ] + [_joueur("m%d" % k, "M", {"centres": 1, "tirs": 1}) for k in range(1, 6)]
        onze.append(_joueur(centreur, "A", {"centres": 12 if centreur == "ailier" else 1, "tirs": 2}))
        adverse = [_joueur("x%d" % k, "M", {"centres": 1}) for k in range(11)]
        return {"date": "2026-01-%02d" % jour, "domicile": "Club", "exterieur": "Adv%d" % jour,
                "joueurs": {"domicile": onze, "exterieur": adverse}}

    index = st.Styles().alimenter(
        [_match_styles(k, "ailier") for k in range(1, 11)]
        + [_match_styles(k, "pivot") for k in range(11, 13)]
    )
    ailier = index.profil("ailier", "2026-02-01")
    check("un centreur a un taux de centres eleve", ailier["par_90"]["centres"] > 5.0, True)
    check("aucune fuite : avant son premier match, le joueur est inconnu",
          index.profil("ailier", "2026-01-01")["matchs"], 0)
    peu_vu = index.profil("pivot", "2026-02-01")
    check("un joueur peu vu reste pres de son poste",
          peu_vu["par_90"]["centres"] > 1.0, True)
    sans_ailier = [j for j in index.onze_habituel("Club", "2026-02-01") if j != "ailier"] + ["pivot"]
    avec = index.facteur("Club", index.onze_habituel("Club", "2026-01-11"), "2026-02-01", "corners")
    sans = index.facteur("Club", sans_ailier[:11], "2026-02-01", "corners")
    check("sans son centreur, l'equipe fabrique moins de corners", sans["facteur"] < avec["facteur"], True)
    check("equipe inconnue : facteur neutre",
          index.facteur("Personne", None, "2026-02-01", "corners")["facteur"], 1.0)
    from ibet.modeles import CORNERS, TIRS_CADRES
    check("a poids nul, les styles ne deplacent rien",
          st.appliquer((5.0, 4.0), {"domicile": {"facteur": 1.2}, "exterieur": {"facteur": 0.8}}, 0.0)[:2],
          (5.0, 4.0))
    check("le facteur est borne",
          st.appliquer((5.0, 4.0), {"domicile": {"facteur": 3.0, "source": "aligne"}}, 1.0)[0],
          5.0 * st.BORNE)
    check("l'onze habituel ne deplace rien",
          st.appliquer((5.0, 4.0), {"domicile": {"facteur": 1.2, "source": "habituel"}}, 1.0)[0], 5.0)
    check("l'onze aligne deplace les corners au poids mesure",
          round(CORNERS.ajuster((5.0, 4.0), {"styles": {"domicile": {"facteur": 1.2, "source": "aligne"}}})[0], 4),
          round(5.0 * 1.2 ** CORNERS.poids_styles, 4))

    print("\nZ10. Propositions : lignes des bookmakers et cote minimale")
    from ibet.modeles import offres as of
    total, _ = CORNERS.lignes_du_match(7.0, 5.5)
    check("corners : la ligne principale est au milieu de l'echelle", 12.5 in total, True)
    check("corners : pas de ligne que personne ne cote", min(total) >= 9.5, True)
    check("corners : lignes dans la gamme", all(6.5 <= l <= 14.5 for l in total), True)
    faible = CORNERS.lignes_du_match(3.0, 3.0)[0]
    check("match pauvre : l'echelle descend jusqu'au bas de la gamme", min(faible), 6.5)
    evidentes = [{"libelle": "Plus de 0.5 buts au total", "p": 0.97, "famille": "total"},
                 {"libelle": "Plus de 1.5 buts au total", "p": 0.78, "famille": "total"}]
    retenues = of.select_offers(evidentes)
    check("une evidence payee sous la cote minimale n'est pas proposee",
          [o["libelle"] for o in retenues], ["Plus de 1.5 buts au total"])
    check("la cote juste accompagne chaque proposition", retenues[0]["cote_juste"], round(1 / 0.78, 2))
    check("cote estimee au-dessus du minimum", retenues[0]["cote_estimee"] >= of.COTE_MIN, True)
    from ibet.modeles import bookmakers as bk
    check("quatorze bookmakers de reference", len(bk.BOOKMAKERS), 14)
    check("dont quatre africains", sum(1 for b in bk.BOOKMAKERS if b.zone == "Afrique"), 4)
    check("le moins margine paie le mieux", bk.cotes(0.7)[0][0], "1xBet")
    check("un marche special paie moins que le 1X2",
          bk.cotes(0.7, special=True)[0][1] < bk.cotes(0.7)[0][1], True)
    check("chaque proposition dit ou la prendre",
          [n for n, _ in retenues[0]["bookmakers"]["meilleurs"]], ["1xBet", "Pinnacle", "SportyBet"])
    # A 82 %, trois operateurs paient encore 1.15 sur les buts, pas sur les corners.
    check("82 % : jouable sur les buts", of.jouable(0.82), True)
    check("82 % : pas sur un marche special", of.jouable(0.82, special=True), False)

    print("\nZ8. Cotes du marche : corners et tirs cadres")
    from ibet.modeles.apports import corriger_par_le_marche
    equilibre = {"domicile": 0.36, "nul": 0.28, "exterieur": 0.36}
    favori = {"domicile": 0.70, "nul": 0.18, "exterieur": 0.12}
    ld_eq, le_eq, _ = CORNERS.ajuster((5.0, 5.0), {"marche": equilibre})
    ld_fav, le_fav, trace = CORNERS.ajuster((5.0, 5.0), {"marche": favori})
    check("le favori obtient plus de corners", ld_fav > ld_eq and le_fav < le_eq, True)
    haut = CORNERS.ajuster((8.0, 4.5), {"marche": equilibre})[0]
    check("le marche resserre un historique extreme vers la moyenne", haut < 8.0, True)
    check("la trace dit d'ou vient le deplacement", sorted(trace), ["marche"])
    check("sans cotes, le marche ne deplace rien",
          corriger_par_le_marche((5.0, 4.0), None, CORNERS.coefs_marche)[0], (5.0, 4.0))
    check("la correction est bornee",
          corriger_par_le_marche((20.0, 0.5), favori, CORNERS.coefs_marche)[0][0] >= 20.0 / 1.6, True)
    check("tirs cadres : le favori cadre davantage",
          TIRS_CADRES.ajuster((4.0, 4.0), {"marche": favori})[0]
          > TIRS_CADRES.ajuster((4.0, 4.0), {"marche": equilibre})[0], True)
    check("sans apport, corners et tirs cadres inchanges",
          (CORNERS.ajuster((5.0, 4.0), None)[:2], TIRS_CADRES.ajuster((4.0, 3.0), None)[:2]),
          ((5.0, 4.0), (4.0, 3.0)))

    print("\nZ9. Base des entraineurs : mesures, indices, etiquettes")
    from ibet.stockage import entraineurs as coachs

    moi = {"possession": "60%", "passes": "88% (440/500)", "passes_longues": "50% (20/40)",
           "centres": "25% (5/20)", "tacles": "60% (6/10)", "fautes": 8, "corners": 7,
           "xg": "1.80", "cartons_jaunes": 2, "hors_jeux": 1}
    lui = {"possession": "40%", "passes": "75% (270/360)", "corners": 3, "xg": "0.60",
           "cartons_jaunes": 3, "hors_jeux": 4}
    m = coachs.mesures_du_match(moi, lui, 2, 0)
    check("possession lue en pourcentage", m["possession"], 60.0)
    check("passes tentees et reussies", (m["passes_tentees"], m["passes_reussies"]), (500.0, 440.0))
    check("PPDA : passes adverses / (tacles tentes + fautes)",
          m["ppda_passes_adverses"] / m["ppda_actions"], 360 / 18)
    check("evenements pour et contre", (m["pour_corners"], m["contre_corners"]), (7.0, 3.0))
    check("hors-jeux adverses = hors-jeux provoques", m["contre_hors_jeux"], 4.0)
    check("buts pris au score", (m["pour_buts"], m["contre_buts"]), (2.0, 0.0))
    check("grandeur absente : pas de mesure",
          "passes_tentees" in coachs.mesures_du_match({}, {}, None, None), False)

    def ligne_coach(coach, cote, corners, match):
        return {"match_id": "m%d" % match, "cote": cote, "entraineur_id": coach, "nom": coach,
                "equipe": "E" + coach, "adversaire": "X", "date": "2026-03-%02d" % (match % 28 + 1),
                "competition": "Ligue Test", "systeme": "1-4-3-3" if match % 3 else "1-4-4-2",
                "mesures": {"pour_corners": corners, "contre_corners": 5.0}}

    lignes_c = [ligne_coach("C", "domicile", 5.0, j) for j in range(30)]
    lignes_c += [ligne_coach("K", "domicile", 8.0, 100 + j) for j in range(30)]
    fiches_c = coachs.construire(lignes_c)
    ref = coachs.references(lignes_c)[("Ligue Test", "domicile")]["pour:corners"]
    check("reference de competition entre les deux profils", 5.0 < ref < 8.0, True)
    k = fiches_c["K"]["evenements"]["pour:corners"]
    check("valeur brute de l'entraineur", k["valeur"], 8.0)
    check("indice au-dessus de 1 pour qui obtient plus", k["indice"] > 1.0, True)
    check("l'a priori tire l'indice vers 1",
          k["indice"] < k["valeur"] / k["reference"], True)
    check("systeme principal et repartition",
          (fiches_c["K"]["systemes"]["principal"], fiches_c["K"]["systemes"]["releves"]),
          ("1-4-3-3", 30))
    peu = coachs.construire([ligne_coach("P", "domicile", 9.0, 200 + j) for j in range(3)]
                            + lignes_c)["P"]
    check("sous 10 matchs, aucune etiquette", peu["etiquettes"], [])

    # Rotation : titulaires changes par rapport au match precedent de l'equipe.
    onze_a = ["j%d" % i for i in range(11)]
    onze_b = onze_a[:8] + ["n1", "n2", "n3"]
    compo = [
        {"match_id": "r1", "equipe": "R", "date": "2026-04-01", "titulaires": onze_a,
         "entrees": [60, 75], "mesures": {"pour_buts": 1.0, "contre_buts": 1.0}},
        {"match_id": "r2", "equipe": "R", "date": "2026-04-05", "titulaires": onze_b,
         "entrees": [], "mesures": {"pour_buts": 2.0, "contre_buts": 0.0}},
        {"match_id": "r3", "equipe": "R", "date": "2026-06-30", "titulaires": onze_a,
         "entrees": [46], "mesures": {}},
    ]
    coachs.ajouter_compositions(compo)
    check("premier match : pas de rotation mesurable", "rotation" in compo[0]["mesures"], False)
    check("trois titulaires changes", compo[1]["mesures"]["rotation"], 3.0)
    check("apres une longue coupure, pas de rotation", "rotation" in compo[2]["mesures"], False)
    check("remplacements et premier changement",
          (compo[0]["mesures"]["remplacements"], compo[0]["mesures"]["premier_changement"]), (2.0, 60.0))
    check("points au score", (compo[0]["mesures"]["points"], compo[1]["mesures"]["points"]), (1.0, 3.0))

    # Resume pour la fiche de match : un entraineur inconnu reste signale.
    check("sans entraineur, pas de resume", coachs.resume("", "", "E"), None)
    check("entraineur absent de la base",
          coachs.resume("id-qui-n-existe-pas", "Inconnu I.", "E")["connu"], False)
    check("discipline vide : aucun entraineur a resumer",
          context.entraineurs_du_match(("A", "B"), {}), {})

    print("\nZ11. Cotes plus / moins 2,5 buts : consensus, total recale, tirs cadres")
    from ibet.modeles import buts as m_buts
    from ibet.modeles.apports import corriger_par_le_marche
    from ibet.modeles.tirs_cadres import COEFS_MARCHE as TC_1X2, COEFS_MARCHE_TOTAL as TC_TOT

    releve = api_client._meilleures_cotes([
        {"title": "A", "markets": [{"key": "totals", "outcomes": [
            {"name": "Over", "point": 2.5, "price": 1.80}, {"name": "Under", "point": 2.5, "price": 2.00}]}]},
        {"title": "B", "markets": [{"key": "totals", "outcomes": [
            {"name": "Over", "point": 2.5, "price": 2.00}, {"name": "Under", "point": 2.5, "price": 1.80}]}]},
    ])
    check("consensus : moyenne des operateurs, marge retiree", releve["totaux_consensus"]["2.5"], [0.5, 2])
    check("meilleure cote gardee a part", releve["totaux"]["Over|2.5"], 2.0)
    check("match termine : aucune requete",
          api_client.probabilite_plus_de_buts({"statut": "Termine"}), None)

    mb = m_buts.ModeleButs()
    lam_b = (1.4, 1.1)
    intact_b = mb.caler(lam_b, {"totaux_marche": None}, 0.0, 1.173, 1.173)
    check("sans cote 2,5, total intact", intact_b, (1.4, 1.1, None))
    lh, la, tr = mb.caler(lam_b, {"totaux_marche": 0.75}, 0.0, 1.173, 1.173)
    check("marche plus ouvert : total releve", lh + la > 2.5, True)
    check("l'ecart entre les equipes est garde", round(lh - la, 6), 0.3)
    p_apres = mb.probabilite_total(lh, la, 2.5, 0.0, 1.173)
    cible = m_buts.POIDS_MODELE_TOTAL * tr["total_marche"]["p_plus_modele"] + \
        (1 - m_buts.POIDS_MODELE_TOTAL) * 0.75
    check("P(+2,5) recalee = melange modele / bookmakers", round(p_apres, 3), round(cible, 3))
    lh2, la2, tr2 = mb.caler(lam_b, {"totaux_marche": 0.75, "marche": marche_p}, 0.0, 1.173, 1.173)
    check("total et issue ensemble", ("total_marche" in tr2, "issue_marche" in tr2), (True, True))

    un_x_deux = {"domicile": 0.5, "nul": 0.25, "exterieur": 0.25}
    seul, _ = corriger_par_le_marche((4.0, 3.5), un_x_deux, TC_1X2, TC_TOT)
    avec, tr_tc = corriger_par_le_marche((4.0, 3.5), dict(un_x_deux, plus_25=0.7), TC_1X2, TC_TOT)
    check("tirs cadres : sans cote 2,5, repli sur le 1X2 seul",
          seul, corriger_par_le_marche((4.0, 3.5), un_x_deux, TC_1X2)[0])
    check("tirs cadres : match ouvert -> plus de tirs cadres", avec[0] > seul[0] and avec[1] > seul[1], True)
    check("la trace garde p(+2,5)", tr_tc["p_plus_25"], 0.7)

    print("\nZ12. Historique des arbitres (worldfootball) : saisons, rapprochement, a priori")
    from ibet.modeles.discipline import Discipline as _Disc
    from ibet.modeles.discipline import historique_anterieur, saison_de
    from ibet.stockage import arbitres as _arb

    check("saison : septembre ouvre la saison", saison_de("2026-09-27"), "2026-2027")
    check("saison : mars ferme la precedente", saison_de("2026-03-01"), "2025-2026")
    histo = [
        {"saison": "2025-2026", "jaunes": 120.0, "attendus": 100.0, "matchs": 25.0},
        {"saison": "2026-2027", "jaunes": 50.0, "attendus": 10.0, "matchs": 3.0},
    ]
    check("la saison du match est exclue", historique_anterieur(histo, "2026-09-01"),
          (120.0, 100.0, 25.0))
    check("et toute saison posterieure aussi", historique_anterieur(histo, "2026-03-01"),
          (0.0, 0.0, 0.0))
    disc = _Disc()
    check("sans historique : a priori neutre, comme la 2.0.0",
          (disc.arbitre("X", "2026-09-01")["rapport"], disc.arbitre("X", "2026-09-01")["variance"]),
          (1.0, 1.0 / 40.0))
    avec_h = disc.arbitre("X", "2026-09-01", histo)
    check("avec historique : a priori = (120 + 20) / (100 + 20)",
          round(avec_h["rapport"], 4), round(140 / 120, 4))
    check("la trace de l'historique est dans le profil", avec_h["historique"]["matchs"], 25.0)
    check("historique posterieur au match : sans effet",
          disc.arbitre("X", "2026-03-01", histo)["rapport"], 1.0)

    # Rapprochement des noms, sur une base fictive (aucune lecture de ibet.db).
    lignes_reelles = _arb.lignes
    _arb.lignes = lambda cle=None: [
        dict(cle=_arb.cle_arbitre(nom, pays), nom=nom, pays=pays, competition="c",
             saison="2025-2026", matchs=20.0, jaunes=80.0, jaunes_moyens=4.0)
        for nom, pays in (("Jesús Gil Manzano", "Spain"), ("João Pinheiro", "Portugal"),
                          ("José Munuera Montero", "Spain"), ("Juan Martínez Munuera", "Spain"),
                          ("Dr. Matthias Jöllenbeck", "Germany"))
    ]
    _arb._INDEX.clear()
    try:
        idx = _arb._index()
        check("nom de famille partiel", idx.retrouver("Manzano J.", "Esp"), "jesus gil manzano|spain")
        check("nom de famille complet", idx.retrouver("Gil Manzano J.", "Esp"), "jesus gil manzano|spain")
        check("pays different : ecarte", idx.retrouver("Pinheiro J.", "Bra"), None)
        check("homonymes : le premier nom de famille departage",
              (idx.retrouver("Munuera J.", "Esp"), idx.retrouver("Martinez J.", "Esp")),
              ("jose munuera montero|spain", "juan martinez munuera|spain"))
        check("un titre n'est pas une initiale", idx.retrouver("Jollenbeck M.", "Ger"),
              "dr matthias jollenbeck|germany")
        check("les attendus suivent la moyenne de la competition",
              _arb.historique_de("Manzano J.", "Esp")[0]["attendus"], 80.0)
    finally:
        _arb.lignes = lignes_reelles
        _arb._INDEX.clear()

    print("\nZ13. Composeur : seulement des options disponibles chez les bookmakers")
    from ibet.prevision import marche as _mc

    fiche_coupon = {
        "match": "Alpha - Beta", "match_id": "TEST",
        "grandeurs": [{
            "cle": "corners", "grandeur": "Corners",
            # Echelle du total : la ligne principale (la plus proche de 50 %)
            # est 9.5. Les corners ouvrent 3 lignes de part et d'autre au
            # total : 6.5 a 12.5 sont ouvertes, 13.5 ne l'est pas.
            "echelle_total": {"seuils": [6.5, 9.5, 12.5, 13.5],
                              "probabilites": [0.93, 0.52, 0.20, 0.12]},
            # Par equipe : Alpha ouvert autour de 5.5 (ecart 2 lignes), donc
            # 2.5 n'est PAS ouvert pour Alpha.
            "echelle_par_equipe": {"seuils": [2.5, 4.5, 5.5],
                                   "Alpha": [0.95, 0.70, 0.50],
                                   "Beta": [0.60, 0.30, 0.20]},
        }],
    }
    ouvertes = _mc.lignes_ouvertes(fiche_coupon)
    check("ligne principale ouverte", "Plus de 9.5 corners au total" in ouvertes, True)
    check("ligne trop eloignee : fermee", "Plus de 13.5 corners au total" in ouvertes, False)
    check("ligne d'equipe trop eloignee : fermee", "Alpha : plus de 2.5 corners" in ouvertes, False)
    check("ligne d'equipe voisine : ouverte", "Alpha : plus de 4.5 corners" in ouvertes, True)
    echelles_fiche = set(_mc.propositions_des_echelles(fiche_coupon))
    check("option trop sure : cote trop basse",
          _mc.disponible("Plus de 6.5 corners au total", 0.93, ouvertes, echelles_fiche),
          "cote trop basse")
    check("option sur une ligne fermee : ecartee",
          _mc.disponible("Alpha : plus de 2.5 corners", 0.95, ouvertes, echelles_fiche),
          "ligne non ouverte")
    check("option jouable : retenue",
          _mc.disponible("Alpha : plus de 4.5 corners", 0.70, ouvertes, echelles_fiche), None)
    raisons: dict[str, int] = {}
    conseils = _mc.conseils_du_modele(fiche_coupon, combien=3, ecartees=raisons)
    check("aucun conseil au-dessus de ce que 3 bookmakers paient 1.15",
          all(c["bookmakers"]["operateurs_ok"] >= 3 for c in conseils), True)
    check("chaque conseil porte sa cote estimee", all(c["cote_estimee"] for c in conseils), True)
    check("les options ecartees sont comptees", raisons.get("cote trop basse", 0) > 0, True)
    check("cote minimale relevee a 1.30 : une option a 78 % est ecartee",
          _mc.disponible("Alpha : plus de 4.5 corners", 0.78, ouvertes, echelles_fiche, 1.30),
          "cote trop basse")
    check("et une option a 70 % reste",
          _mc.disponible("Alpha : plus de 4.5 corners", 0.70, ouvertes, echelles_fiche, 1.30), None)

    print("\nZ14. Horloge fiable : l'ecart a l'heure reelle est corrige")
    from datetime import datetime as _dt
    from datetime import timezone as _tz
    from ibet.evaluation import verify as _verify
    from ibet.sources import horloge as _h

    fiche_hier = {"coup_denvoi_local": "2026-09-27 21:00"}
    _h.fixer(0.0)
    machine = _dt.now(_tz.utc)
    _h.fixer(13 * 3600 + 32 * 60)
    check("l'heure corrigee avance de l'ecart mesure",
          round((_h.maintenant(_tz.utc) - machine).total_seconds() / 60), 13 * 60 + 32)
    check("un ecart de 13 h 32 est signale", _h.etat()["machine_a_corriger"], True)
    _h.fixer(30.0)
    check("un ecart de 30 s ne l'est pas", _h.etat()["machine_a_corriger"], False)
    # La decision « match termine » suit l'heure corrigee, pas celle de la machine.
    _h.fixer(10 * 365 * 86400.0)
    check("un match passe est vu termine avec l'heure corrigee",
          _verify.is_due(fiche_hier, "Europe/Paris"), True)
    _h.fixer(-10 * 365 * 86400.0)
    check("et pas si l'heure reelle est anterieure", _verify.is_due(fiche_hier, "Europe/Paris"), False)
    _h.fixer(0.0)

    print("\nZ15. Plus / moins : sens d'une proposition, un seul « moins » par match")
    check("sens : moins", _mc.sens_du_pari("Moins de 11.5 corners au total"), "moins")
    check("sens : plus, par equipe", _mc.sens_du_pari("Alpha : plus de 2.5 buts"), "plus")
    check("une issue n'a pas de sens", _mc.sens_du_pari("Victoire de l'une ou l'autre (pas de nul)"), None)
    check("un duel non plus", _mc.sens_du_pari("Plus de corners pour Alpha"), None)
    check("un combine non plus", _mc.sens_du_pari("Les deux equipes marquent et plus de 2.5 buts"), None)
    fiche_moins = {
        "match": "Alpha - Beta", "match_id": "TEST2",
        "grandeurs": [
            {"cle": "corners", "grandeur": "Corners",
             "echelle_total": {"seuils": [8.5, 9.5, 10.5], "probabilites": [0.62, 0.50, 0.25]}},
            {"cle": "buts", "grandeur": "Buts",
             "echelle_total": {"seuils": [1.5, 2.5, 3.5], "probabilites": [0.70, 0.50, 0.27]}},
        ],
    }
    conseils_moins = _mc.conseils_du_modele(fiche_moins, combien=3)
    check("au plus un « moins » par match",
          sum(_mc.sens_du_pari(c["pari"]) == "moins" for c in conseils_moins) <= 1, True)
    check("les autres places vont aux « plus »",
          any(_mc.sens_du_pari(c["pari"]) == "plus" for c in conseils_moins), True)

    print("\nZ16. Emission : jamais apres le coup d'envoi (heure fiable)")
    from datetime import timedelta as _td
    from ibet.prevision import forecast as _fc

    _h.fixer(0.0)
    maintenant = _dt.now(_tz.utc)
    def _match_dans(minutes: float) -> dict:
        return {"kickoff_utc": (maintenant + _td(minutes=minutes)).isoformat()}
    check("match dans 2 h : emis", _fc.trop_tard(_match_dans(120)), None)
    check("match dans 3 min : refuse", (_fc.trop_tard(_match_dans(3)) or "").startswith("coup d'envoi dans"), True)
    check("match commence : refuse", (_fc.trop_tard(_match_dans(-40)) or "").startswith("match commence"), True)
    check("coup d'envoi inconnu : refuse", _fc.trop_tard({}), "coup d'envoi inconnu")
    # L'horloge de la machine en retard de 13 h 32 : le match « dans 2 h » a
    # l'heure de la machine est en realite joue depuis longtemps.
    _h.fixer(13 * 3600 + 32 * 60)
    check("horloge en retard : le match deja joue est refuse",
          (_fc.trop_tard(_match_dans(120)) or "").startswith("match commence"), True)
    _h.fixer(0.0)

    print()
    if failures:
        print("%d test(s) en echec : %s" % (len(failures), ", ".join(failures)))
        return 1
    print("Tous les tests passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
