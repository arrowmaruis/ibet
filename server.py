"""API HTTP locale : expose les matchs normalises au front Vue (ibet-web).

Le CLI et cette API partagent exactement le meme code de recuperation
(`api_client`) et le meme cache disque : lancer le serveur ne double ni les
requetes vers la source, ni les regles de normalisation. Ce module n'est qu'une
facade HTTP -- aucune logique metier ne doit y descendre.

    .venv\\Scripts\\activate
    pip install -r requirements.txt
    uvicorn server:app --reload --port 8000

    GET  /api/matchs?date=2026-09-06
    GET  /api/matchs?date=2026-09-06&league=Ligue%201&team=Lens
    GET  /api/criteres
    GET  /api/predictions
    GET  /api/predictions/{match_id}
    GET  /api/predictions/{match_id}/options
    GET  /api/predictions/{match_id}/contexte
    GET  /api/predictions/{match_id}/cotes
    POST /api/predictions/{match_id}/cotes
    GET  /api/valeur
    GET  /api/bilan
    GET  /api/composeur?matchs=id1,id2&options=2
    GET  /api/coupons          POST /api/coupons
    GET  /api/coupons/{id}     DELETE /api/coupons/{id}
    GET  /api/marches/{match_id}
    POST /api/predictions/verifier

Usage personnel, en local : le serveur n'est pas concu pour etre expose sur
Internet (voir les CGU de Flashscore rappelees dans le README).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

import api_client
import context
import criteres
import forecast
import marche
import store
import verify

load_dotenv()

# Cree les tables au demarrage si la base n'existe pas encore. Sans effet
# ensuite : le schema est en CREATE TABLE IF NOT EXISTS.
store.init()

# Origines du serveur de developpement Vite. Le front et l'API sont deux projets
# separes servis sur deux ports : sans cette autorisation, le navigateur bloque
# chaque appel. Rien d'autre n'est autorise -- l'API reste locale.
DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app = FastAPI(
    title="iBET",
    description="Matchs de football du jour, normalises depuis Flashscore et 3 API REST.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=DEV_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _today(tz_name: str) -> str:
    """Date du jour dans le fuseau d'affichage, pas celui de la machine."""
    return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")


def _fail(exc: api_client.ApiError) -> HTTPException:
    """Traduit une erreur du client en statut HTTP parlant.

    Les trois cas sont distingues parce que la conduite a tenir differe : le
    quota se recharge tout seul (429), une cle manquante demande une action de
    l'utilisateur (503), le reste est une panne de la source (502). Un 500
    generique obligerait a lire les journaux du serveur pour le savoir.
    """
    if isinstance(exc, api_client.QuotaError):
        return HTTPException(status_code=429, detail=str(exc))
    if isinstance(exc, api_client.MissingKeyError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


@app.get("/api/matchs")
def list_matches(
    date: str | None = Query(
        default=None, description="Jour au format YYYY-MM-DD (defaut : aujourd'hui)"
    ),
    league: str | None = Query(
        default=None, description="Filtre par championnat (sous-chaine)"
    ),
    team: str | None = Query(
        default=None, description="Filtre par equipe (sous-chaine, domicile ou exterieur)"
    ),
    all_teams: bool = Query(
        default=False,
        description="Conserve equipes feminines, jeunes et reserves (ecartees par defaut)",
    ),
    provider: str | None = Query(default=None, description="flashscore | thesportsdb | ..."),
    tz: str | None = Query(default=None, description="Fuseau IANA, ex: Europe/Paris"),
    refresh: bool = Query(default=False, description="Ignore le cache local"),
) -> dict[str, Any]:
    """Matchs d'une journee, deja tries par heure puis par competition.

    Les filtres sont ceux du CLI, appliques par le meme code : `league` cote
    recuperation, `team` cote client apres normalisation (aucune des quatre
    sources ne sait filtrer par equipe).
    """
    try:
        resolved_provider, tz_name = api_client.resolve_settings(provider, tz)
        day = date or _today(tz_name)
        matches = api_client.get_matches(
            day,
            league=league,
            provider=resolved_provider,
            tz_name=tz_name,
            use_cache=not refresh,
        )
        # Avant tout filtre : le front consulte souvent des journees passees,
        # et c'est le point de capture le plus frequent de tout le projet.
        store.archiver_journee(matches, api_client.FINISHED)
        if team:
            matches = api_client.filter_by_team(matches, team, include_variants=all_teams)
    except api_client.ApiError as exc:
        raise _fail(exc) from exc

    return {
        "date": day,
        "provider": resolved_provider,
        "fuseau": tz_name,
        "total": len(matches),
        "matchs": matches,
    }


@app.get("/api/predictions")
def list_predictions(
    limit: int = Query(default=200, ge=1, le=1000, description="Nombre maximum de previsions"),
    tz: str | None = Query(default=None, description="Fuseau IANA, ex: Europe/Paris"),
) -> dict[str, Any]:
    """Previsions enregistrees, coup d'envoi le plus recent d'abord.

    Rien n'est recalcule et la source n'est pas interrogee : tout vient de la
    base locale. Une prevision est un engagement pris a une date -- la relire
    doit rendre exactement ce qui a ete annonce.
    """
    predictions = store.all_predictions(limit=limit)
    try:
        _, tz_name = api_client.resolve_settings("flashscore", tz)
    except api_client.ApiError as exc:
        raise _fail(exc) from exc
    return {
        "total": len(predictions),
        "bilan": store.tally(),
        # Combien de fiches attendent un resultat que leur match devrait deja
        # avoir donne. Se lit sans rien interroger : c'est ce compte qui decide
        # s'il vaut la peine d'aller voir.
        "a_verifier": verify.due_count(tz_name),
        "predictions": predictions,
    }


@app.get("/api/predictions/{match_id}")
def get_prediction(match_id: str) -> dict[str, Any]:
    """Prevision complete d'un match : attendus, probabilites, propositions."""
    prediction = store.get(match_id)
    if prediction is None:
        raise HTTPException(
            status_code=404,
            detail="Aucune prevision enregistree pour le match %s." % match_id,
        )
    return prediction


@app.get("/api/criteres")
def list_criteria() -> dict[str, Any]:
    """Les quatorze criteres de decision : ce qu'ils regardent et ce qu'ils font.

    Catalogue statique, sans aucun appel a la source : il decrit le systeme, pas
    un match. C'est ce qui permet a un client de construire son affichage -- une
    colonne par critere, un libelle, une explication -- avant meme d'avoir
    demande une prevision.

    `poids` est le nom du reglage correspondant dans `context.Poids`, ou `null`
    pour les criteres qui informent sans corriger. Les valeurs en vigueur sont
    rendues a part : un critere dont le poids vaut zero est calcule et affiche,
    mais ne deplace aucune probabilite.
    """
    return {
        "total": len(context.CATALOGUE),
        "criteres": context.CATALOGUE,
        "poids_en_vigueur": context.DEFAULT_POIDS._asdict(),
        "plafond_corrections": context.PLAFOND,
    }


def _prediction_or_404(match_id: str) -> dict[str, Any]:
    prediction = store.get(match_id)
    if prediction is None:
        raise HTTPException(
            status_code=404,
            detail="Aucune prevision enregistree pour le match %s." % match_id,
        )
    return prediction


@app.get("/api/predictions/{match_id}/options")
def get_options(match_id: str) -> dict[str, Any]:
    """Les options de paris d'une prevision, regroupees par marche.

    La fiche complete est organisee par grandeur, parce que c'est ainsi que le
    modele calcule ; un parieur cherche un marche. Cette vue fait la conversion
    sans rien recalculer : toutes les probabilites viennent telles quelles de la
    fiche enregistree, sinon deux lectures de la meme prevision pourraient ne
    pas coincider.
    """
    return forecast.betting_options(_prediction_or_404(match_id))


@app.get("/api/predictions/{match_id}/contexte")
def get_context(match_id: str) -> dict[str, Any]:
    """Les quatorze criteres tels qu'ils ont ete releves a l'emission.

    Rien n'est recalcule : le contexte fait partie de l'engagement pris. Une
    fiche relue avec la meteo d'aujourd'hui ou le classement d'aujourd'hui ne
    serait plus la prevision qui a ete emise.
    """
    prediction = _prediction_or_404(match_id)
    contexte = prediction.get("contexte")
    if not contexte:
        raise HTTPException(
            status_code=404,
            detail=(
                "La prevision du match %s a ete emise sans contexte. Reemettez-la "
                "avec les criteres pour l'obtenir." % match_id
            ),
        )
    return contexte


@app.get("/api/predictions/{match_id}/composition")
def get_lineups(match_id: str) -> dict[str, Any]:
    """Composition probable des deux equipes, telle qu'elle a ete etablie.

    Deux natures de composition, et la reponse les distingue par `annoncee` :
    celle publiee par la source a une heure du coup d'envoi, qui fait foi, et
    celle DEDUITE des titularisations des derniers matchs -- aucune source
    gratuite ne publie de composition probable, mais un joueur qui a commence
    les cinq derniers matchs commencera vraisemblablement le sixieme.

    Chaque joueur porte son nombre de titularisations sur la periode : un onze
    bati sur cinq matchs concordants et un onze bati sur deux titularisations
    isolees ne se valent pas.
    """
    prediction = _prediction_or_404(match_id)
    critere = next(
        (
            c
            for c in ((prediction.get("contexte") or {}).get("criteres") or [])
            if c.get("numero") == 3
        ),
        None,
    )
    if not critere:
        raise HTTPException(
            status_code=404,
            detail=(
                "La prevision du match %s a ete emise sans contexte : la "
                "composition n'a pas ete relevee." % match_id
            ),
        )

    equipes = [p.strip() for p in (prediction.get("match") or " - ").split(" - ", 1)]
    reponse: dict[str, Any] = {"match": prediction.get("match", ""), "equipes": equipes}
    for index, cote in enumerate(("domicile", "exterieur")):
        bloc = critere["valeur"].get(cote) or {}
        reponse[cote] = {
            "equipe": equipes[index] if index < len(equipes) else "",
            "systeme": (bloc.get("composition_probable") or {}).get("systeme", ""),
            "composition": bloc.get("composition_probable") or {},
            "absents": bloc.get("absents") or [],
        }
    return reponse


@app.get("/api/predictions/{match_id}/cotes")
def get_odds(match_id: str) -> dict[str, Any]:
    """Releves de cotes d'un match, mouvement de ligne et ecart au modele.

    L'ecart est la seule chose utile qu'un modele puisse dire d'une cote : de
    combien il s'en ecarte. Le mouvement, lui, ne se lit que dans deux releves
    -- d'ou le fait qu'aucun ne soit jamais remplace.
    """
    prediction = _prediction_or_404(match_id)
    historique = store.odds_history(match_id)
    if not historique:
        return {
            "match_id": match_id,
            "releves": 0,
            "cotes": [],
            "note": (
                "Aucune cote enregistree pour ce match. Flashscore ne les "
                "publie pas sur son edition francaise, mais BetExplorer les "
                "couvre : une fiche emise AVEC son contexte en enregistre un "
                "releve, et `python main.py --valeur` en rafraichit un pour "
                "toutes les fiches en attente. A defaut, envoyez-les en POST "
                "sur cette meme adresse."
            ),
        }

    buts = next(
        (g for g in prediction.get("grandeurs") or [] if g["cle"] == "buts"), None
    )
    modele = (buts or {}).get("resultat")
    critere = context.critere_cotes(historique[-1]["cotes"], modele, historique)
    return {
        "match_id": match_id,
        "releves": len(historique),
        "cotes": historique,
        "probabilites_modele": modele,
        "critere": critere.as_dict(),
    }


@app.post("/api/predictions/{match_id}/cotes")
def add_odds(
    match_id: str,
    cotes: dict[str, float] = Body(
        ..., description="Cotes decimales, ex: {\"domicile\": 2.10, \"nul\": 3.40, "
                         "\"exterieur\": 3.20}"
    ),
    operateur: str = Query(default="", description="Nom de l'operateur (facultatif)"),
) -> dict[str, Any]:
    """Enregistre un releve de cotes. N'ecrase jamais le precedent.

    Chaque appel ajoute une ligne horodatee : c'est ce qui rend le mouvement de
    ligne lisible. Poster deux fois la meme cote n'est donc pas une erreur, et
    la seconde ligne dit quelque chose que la premiere ne disait pas -- que la
    cote n'a pas bouge.
    """
    _prediction_or_404(match_id)
    try:
        releve = store.save_odds(match_id, cotes, operateur)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return dict(releve, releves=len(store.odds_history(match_id)))


@app.get("/api/valeur")
def value_bets(
    minimum: float = Query(
        default=marche.VALEUR_MIN,
        description="Esperance minimale retenue, ex: 0.05 pour 5 %.",
    )
) -> dict[str, Any]:
    """Ou se servir du modele : les paris dont l'esperance est positive.

    Toutes les autres vues repondent a « que va-t-il se passer ? ». Celle-ci
    repond a « ou parier ? », et ce n'est pas la meme question : un modele
    parfaitement calibre joue sur tous les matchs perd exactement la marge de
    l'operateur, a chaque coup.

    Lit les cotes DEJA enregistrees et n'interroge aucune source : cette vue ne
    doit ni couter de requetes ni ecrire. Pour rafraichir les releves, passer
    par `python main.py --valeur` ou poster les cotes sur
    `/api/predictions/{match_id}/cotes`.
    """
    fiches = []
    for fiche in store.pending():
        cotes = store.latest_odds(fiche.get("match_id", ""))
        if cotes:
            fiches.append(dict(fiche, cotes=cotes))
    paris = marche.selection(fiches, valeur_min=minimum)
    return {
        "fiches_en_attente": len(store.pending()),
        "fiches_cotees": len(fiches),
        "seuil": minimum,
        "paris": paris,
        # Dit a chaque appel, parce qu'un tableau vide se lirait autrement comme
        # un mauvais resultat : une selection vide est le cas NORMAL. Le marche
        # est bien calibre, et un ecart suffisant pour couvrir la marge est rare
        # par construction.
        "note": (
            "Les trois issues de chaque fiche sont valorisees, et non ses "
            "propositions retenues : celles-ci sont des totaux et des doubles "
            "chances qu'aucune cote 1X2 ne peut prixer. Une selection vide est "
            "le cas normal, pas un echec. Les lignes marquees `demesure` ne "
            "sont presque jamais des occasions : a ce niveau d'ecart, c'est le "
            "modele qui est mal informe. Celles marquees `sans_reference` "
            "viennent d'une prevision de repli, sans reference de competition "
            "-- toute la Coupe d'Europe -- ou le modele est le plus faible."
        ),
    }


@app.get("/api/bilan")
def measurement_report() -> dict[str, Any]:
    """Ce que les fiches deja emises ont appris, et que rien d'autre ne dit.

    Deux mesures que le banc d'essai ne peut pas produire, parce qu'il rejoue
    des matchs passes : il ne connait ni les cotes affichees ce jour-la, ni
    l'arbitre qui avait ete designe. Elles ne peuvent venir que de l'usage reel.

      - le **rendement** face au marche, tranche par tranche de valeur
        annoncee : la calibration dit si les pourcentages sont sinceres, le
        Brier si le classement est bon ; ni l'un ni l'autre ne dit si le modele
        bat le marche, qui est pourtant le seul adversaire ;
      - ce que valent les **criteres laisses a poids zero**, mesures
        prospectivement sur le signal que chaque fiche a enregistre.

    Lit la base et rien d'autre : ni reseau, ni ecriture.
    """
    return {"marche": marche.bilan(), "criteres": criteres.bilan()}


@app.get("/api/composeur")
def bet_composer(
    matchs: str = Query(
        default="",
        description="Identifiants de match separes par des virgules. Vide = "
                    "toutes les fiches en attente.",
    ),
    options: int = Query(default=2, ge=1, le=5,
                         description="Options rendues par match."),
    minimum: float = Query(default=marche.VALEUR_MIN,
                           description="Esperance minimale, ex: 0.05."),
    types: str = Query(
        default="",
        description="Types de conseil voulus, separes par des virgules : "
                    "buts, corners, tirs, cartons, issue, autre. Vide = tous.",
    ),
    portees: str = Query(
        default="",
        description="Portees voulues : total, equipe, issue. Vide = toutes.",
    ),
    avec_cotes: bool = Query(
        default=False,
        description="Classer sur l'esperance (p x cote - 1) plutot que sur la "
                    "confiance du modele. Exige des cotes relevees.",
    ),
) -> dict[str, Any]:
    """Un coupon : les meilleures options de chaque match CHOISI.

    Le composeur ne decide pas des matchs -- c'est l'appelant qui les
    selectionne --, il decide des options. Deux regles gouvernent son choix, et
    aucune n'est cosmetique : les ecarts demesures sont ecartes avant le
    classement, et une seule option par FAMILLE est retenue, sans quoi les deux
    meilleures seraient presque toujours deux seuils voisins de la meme echelle,
    donc deux facons de dire la meme chose.

    Le total rendu ne porte que sur UNE option par match. Deux options d'une
    meme rencontre ne sont pas independantes -- elles engagent le meme resultat
    --, leurs probabilites ne se multiplient pas, et la plupart des operateurs
    refusent d'ailleurs de les combiner.

    Lit les cotes deja enregistrees, sans reseau ni ecriture : c'est
    `python main.py --valeur --marches-etendus` qui les rafraichit.
    """
    voulus = {identifiant.strip() for identifiant in matchs.split(",") if identifiant.strip()}
    fiches = []
    for fiche in store.pending():
        identifiant = fiche.get("match_id", "")
        if voulus and identifiant not in voulus:
            continue
        fiches.append(
            dict(
                fiche,
                cotes=store.latest_odds(identifiant),
                cotes_libelles=store.cotes_courantes(
                    identifiant, marche.MARCHE_LIBELLES
                ),
            )
        )
    listes = [t for t in types.split(",") if t.strip()]
    etendues = [p for p in portees.split(",") if p.strip()]

    if not avec_cotes:
        # Sans cote, le critere n'est plus l'esperance mais la CONFIANCE du
        # modele : on rend ce qu'il juge le plus probable. Les deux repondent a
        # des questions distinctes -- une proposition a 90 % est une excellente
        # prevision et presque toujours un mauvais pari -- et melanger les deux
        # classements donnerait un conseil qu'aucune des deux lectures ne
        # justifie.
        lignes = []
        for fiche in fiches:
            conseils = marche.conseils_du_modele(
                fiche, listes, etendues, combien=options
            )
            if conseils:
                lignes.append(
                    {
                        "match": fiche.get("match", ""),
                        "match_id": fiche.get("match_id", ""),
                        "coup_denvoi": fiche.get("coup_denvoi_local", ""),
                        "options_examinees": len(
                            marche.propositions_des_echelles(fiche)
                        ),
                        "options_retenues_par_filtre": len(conseils),
                        "options": conseils,
                    }
                )
        lignes.sort(
            key=lambda ligne: ligne["options"][0]["probabilite"], reverse=True
        )
        return {
            "matchs": lignes,
            "combine_simple": {"selections": len(lignes)},
            "note_correlation": (
                "Classement sur la CONFIANCE du modele, sans cote. Ce n'est pas "
                "un conseil de pari : une proposition a 90 % est une excellente "
                "prevision et presque toujours un mauvais pari, parce qu'un "
                "operateur la cote autour de 1,10 quand il en faudrait 1,11 "
                "pour ne rien perdre."
            ),
            "demandes": sorted(voulus),
            "fiches_examinees": len(fiches),
            "types_disponibles": marche.TYPES_CONSEIL,
            "portees_disponibles": marche.PORTEES_CONSEIL,
            "avec_cotes": False,
        }

    return dict(
        marche.composer(
            fiches,
            combien=options,
            valeur_min=minimum,
            types=listes,
            portees=etendues,
        ),
        demandes=sorted(voulus),
        fiches_examinees=len(fiches),
        # Le vocabulaire des filtres est servi avec le resultat : une interface
        # qui le recopierait finirait par proposer un type que le serveur ne
        # connait plus, et le filtre ne rendrait alors rien sans dire pourquoi.
        types_disponibles=marche.TYPES_CONSEIL,
        portees_disponibles=marche.PORTEES_CONSEIL,
        avec_cotes=True,
    )


@app.get("/api/marches/{match_id}")
def match_markets(match_id: str) -> dict[str, Any]:
    """Toutes les options COTEES d'un match, pour que l'appelant choisisse.

    Le composeur rend les meilleures ; cette vue rend l'ensemble, valeur
    comprise, afin qu'une interface puisse laisser selectionner marche par
    marche plutot que de subir un classement.
    """
    prediction = _prediction_or_404(match_id)
    fiche = dict(
        prediction,
        cotes=store.latest_odds(match_id),
        cotes_libelles=store.cotes_courantes(match_id, marche.MARCHE_LIBELLES),
    )
    paris = marche.paris_du_match(
        fiche, fiche["cotes"], fiche["cotes_libelles"]
    )
    paris.sort(key=lambda pari: pari["valeur"], reverse=True)
    return {
        "match_id": match_id,
        "match": prediction.get("match", ""),
        "coup_denvoi": prediction.get("coup_denvoi_local", ""),
        "options": [
            dict(pari, famille=marche._famille_du_pari(pari)) for pari in paris
        ],
        "note": (
            "Une option marquee `demesure` (esperance au-dela de 25 %) n'est "
            "presque jamais une occasion : a ce niveau d'ecart, c'est le modele "
            "qui est mal informe. Une option marquee `sans_reference` vient "
            "d'une prevision de repli, sans reference de competition."
        ),
    }



@app.get("/api/coupons")
def list_coupons() -> dict[str, Any]:
    """Coupons enregistres, du plus recent au plus ancien, deja tranches.

    Chacun porte son verdict : un coupon qu'on relit sans savoir ce qu'il a
    donne n'apprend rien, et le calculer a la lecture evite d'avoir a le figer
    -- un match joue apres l'enregistrement se compte alors tout seul.
    """
    rendus = []
    for coupon in store.coupons():
        rendus.append(dict(coupon, verification=verify.verifier_coupon(coupon)))
    return {"total": len(rendus), "coupons": rendus}


@app.post("/api/coupons")
def create_coupon(
    corps: dict[str, Any] = Body(
        ...,
        description='{"libelle": "...", "filtres": {...}, "conseils": {...}}',
    )
) -> dict[str, Any]:
    """Enregistre un coupon tel qu'il vient d'etre compose.

    Le contenu est ecrit TEL QUEL, avec ses filtres. Un coupon est un
    engagement pris a une date : le relire modifie, ou sans savoir ce qu'on
    avait demande, lui oterait toute valeur -- c'est le meme principe qui fait
    qu'une prevision enregistree n'est jamais reecrite.
    """
    conseils = corps.get("conseils")
    if not isinstance(conseils, dict) or not conseils.get("matchs"):
        raise HTTPException(
            status_code=422,
            detail="Coupon vide : aucun conseil a enregistrer.",
        )
    return store.save_coupon(
        conseils,
        libelle=str(corps.get("libelle") or ""),
        filtres=corps.get("filtres") if isinstance(corps.get("filtres"), dict) else {},
    )


@app.get("/api/coupons/{coupon_id}")
def read_coupon(coupon_id: int) -> dict[str, Any]:
    """Un coupon precis, avec ce que ses conseils ont donne."""
    coupon = store.coupon(coupon_id)
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon introuvable.")
    return dict(coupon, verification=verify.verifier_coupon(coupon))


@app.delete("/api/coupons/{coupon_id}")
def remove_coupon(coupon_id: int) -> dict[str, Any]:
    """Supprime un coupon.

    Un coupon se supprime, contrairement a une prevision : il n'engage que son
    auteur, et une liste de brouillons qu'on ne peut pas nettoyer finit par
    cacher ceux qui comptent.
    """
    if not store.delete_coupon(coupon_id):
        raise HTTPException(status_code=404, detail="Coupon introuvable.")
    return {"supprime": coupon_id}


@app.post("/api/predictions/verifier")
def check_predictions(tz: str | None = Query(default=None)) -> dict[str, Any]:
    """Tranche les previsions dont le match est termine.

    C'est le seul appel de l'API qui interroge la source : verifier demande le
    resultat reel, qu'aucune base ne peut inventer. Il ne porte que sur les
    fiches sans resultat, et une fiche tranchee ne l'est jamais deux fois.
    """
    try:
        _, tz_name = api_client.resolve_settings("flashscore", tz)
        report = verify.verify_store(tz_name)
    except api_client.ApiError as exc:
        raise _fail(exc) from exc

    settled = [line for line in report if line.get("statut") == "verifie"]
    return {
        "examinees": len(report),
        "tranchees": len(settled),
        "matchs": [
            {
                "match": line["match"],
                "score": line["score"],
                "realisees": sum(1 for o in line["offres"] if o["realise"] is True),
                "tranchees": sum(1 for o in line["offres"] if o["realise"] is not None),
            }
            for line in settled
        ],
        "bilan": store.tally(),
        "a_verifier": verify.due_count(tz_name),
    }
