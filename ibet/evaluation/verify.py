"""Confronte les previsions enregistrees aux resultats reels.

Une prevision n'a de valeur que si elle est verifiee apres coup, sans
reecriture. Ce module va chercher le score et les statistiques du match, puis
tranche chaque proposition : realisee ou non. Les probabilites annoncees ne sont
jamais retouchees -- seul le champ `verifie` est renseigne.

Deux sources, la meme logique de tranchage :

    python -m ibet verifier          # les fiches de la base (`store`), le cas courant
    python -m ibet verifier --json   # le fichier historique `predictions_ouvertes.json`

Seules les fiches sans resultat sont examinees, et seuls les matchs **termines**
sont tranches : un match en cours n'a pas de resultat, et un match tranche n'en
changera plus.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ibet import chemins
from ibet.sources import api_client, horloge
from ibet.stockage import store

RECORD = chemins.HISTORIQUE_JSON

# Un match dure 90 minutes, plus la mi-temps et les arrets de jeu. Avant ce
# delai, aller demander son resultat ne peut rien rapporter -- et une prevision
# emise pour apres-demain n'a aucune raison de faire interroger la source.
MATCH_DURATION_MINUTES = 135

# Flashscore ne publie que sept jours autour d'aujourd'hui. Passe ce delai, le
# resultat d'un match n'est plus interrogeable, et la fiche qui l'attendait ne
# se tranchera JAMAIS : elle ne comptera dans aucune mesure, ni en reussite ni
# en echec. Le projet en a perdu onze d'un coup -- 264 propositions -- faute
# d'avoir lance la verification pendant deux semaines.
#
# C'est une perte silencieuse : rien ne signalait l'echeance, et une fiche
# expiree ressemble a une fiche en attente. D'ou l'avertissement ci-dessous.
JOURS_FENETRE = 7

# Marge avant l'echeance. A deux jours, il reste le temps de lancer la
# verification ; a zero, il est deja trop tard.
JOURS_ALERTE = 2


def _tz_name(tz_name: str | None = None) -> str:
    return tz_name or os.getenv("TIMEZONE") or "Europe/Paris"


def is_due(record: dict[str, Any], tz_name: str | None = None) -> bool:
    """Le match de cette fiche devrait-il avoir livre son resultat ?"""
    try:
        started = datetime.strptime(
            record.get("coup_denvoi_local") or "", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=ZoneInfo(_tz_name(tz_name)))
    except ValueError:
        # Coup d'envoi illisible : on examine la fiche plutot que de la laisser
        # en attente pour toujours.
        return True
    now = horloge.maintenant(ZoneInfo(_tz_name(tz_name)))
    return (now - started).total_seconds() > MATCH_DURATION_MINUTES * 60


def jours_restants(record: dict[str, Any], tz_name: str | None = None) -> float | None:
    """Jours avant que le resultat de ce match cesse d'etre interrogeable.

    Negatif = l'echeance est passee, la fiche ne se tranchera plus. None si le
    coup d'envoi est illisible.
    """
    try:
        debut = datetime.strptime(
            record.get("coup_denvoi_local") or "", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=ZoneInfo(_tz_name(tz_name)))
    except ValueError:
        return None
    maintenant = horloge.maintenant(ZoneInfo(_tz_name(tz_name)))
    return JOURS_FENETRE - (maintenant - debut).total_seconds() / 86400


def peremption(tz_name: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Fiches en attente, rangees par urgence.

    `expirees` ont leur match hors de la fenetre de la source. Elles ne sont
    plus perdues pour autant : l'archive de `store` les tranche si elle les
    connait, et `rattrapage.py` va les chercher sinon. `urgentes` entreront
    dans la zone hors fenetre dans moins de JOURS_ALERTE jours.
    """
    expirees, urgentes = [], []
    for record in store.pending():
        if record.get("resultat_reel"):
            continue
        reste = jours_restants(record, tz_name)
        if reste is None:
            continue
        if reste < 0:
            expirees.append(record)
        elif reste <= JOURS_ALERTE:
            urgentes.append(record)
    return {"expirees": expirees, "urgentes": urgentes}


def due_count(tz_name: str | None = None) -> int:
    """Fiches sans resultat dont le match devrait etre termine."""
    return sum(1 for record in store.pending() if is_due(record, tz_name))

# Libelle de grandeur -> champ de statistiques (None = les buts, lus au score).
FIELDS = {
    "Buts": None,
    "Corners": "corners",
    "Tirs cadres": "tirs_cadres",
    "Cartons jaunes": "cartons_jaunes",
}

# "Plus de 3.5 corners au total" / "Arsenal : moins de 1.5 buts"
_THRESHOLD = re.compile(r"(plus|moins) de (\d+(?:\.\d+)?)", re.IGNORECASE)

# "Entre 2 et 4 buts au total" -- bornes incluses, ce sont des comptages.
_RANGE = re.compile(r"^Entre (\d+) et (\d+) .+ au total$")

# "Arsenal gagne par 2 buts ou plus" -- ecart minimal, pas total marque.
_MARGIN = re.compile(r"^(.+) gagne par (\d+) \D+ ou plus$")

# "Nombre total de buts pair" / "... impair"
_PARITY = re.compile(r"^Nombre total de .+ (pair|impair)$")

# "Plus de corners pour Arsenal" / "Autant ou plus de corners pour Arsenal".
# Le nom de la grandeur n'a pas a etre lu : l'appelant passe deja les valeurs de
# la bonne grandeur. Seul le sujet compte.
_DUEL = re.compile(r"^(Autant ou plus|Plus) de .+ pour (.+)$")

_CLEAN_SHEET = " n'encaisse aucun but"


def actual_values(match: dict[str, Any]) -> dict[str, tuple[float, float]] | None:
    """Valeurs reelles (domicile, exterieur) par grandeur, ou None si absentes.

    Les statistiques sont lues dans l'archive quand elle les a : une fiche
    statistique ne bouge plus une fois le match fini, et la source ne la publie
    que sept jours. Les relire en base evite la requete, et surtout rend la
    verification possible bien apres la fermeture de cette fenetre.
    """
    if match.get("score_domicile") is None:
        return None
    out = {"Buts": (float(match["score_domicile"]), float(match["score_exterieur"]))}

    garde = store.resultat(match.get("match_id") or "") or {}
    stats = garde.get("stats") or {}
    if not stats:
        try:
            stats = api_client.get_stats(match)
        except api_client.ApiError:
            return out
        # Archivees au passage : la prochaine verification ne les redemandera
        # pas, et elles resteront lisibles quand la source les aura oubliees.
        store.archiver_resultat(match, stats)

    for label, field in FIELDS.items():
        if field is None:
            continue
        home = api_client.stat_number((stats.get("domicile") or {}).get(field))
        away = api_client.stat_number((stats.get("exterieur") or {}).get(field))
        if home is not None and away is not None:
            out[label] = (home, away)
    return out


def check_offer(
    label: str, teams: tuple[str, str], values: tuple[float, float]
) -> bool | None:
    """Une proposition s'est-elle realisee ? None si elle n'est pas verifiable ici."""
    home_name, away_name = teams
    home_value, away_value = values

    def side(subject: str) -> tuple[float, float] | None:
        """(valeur du sujet, valeur de l'adversaire), ou None si inconnu."""
        if subject == home_name:
            return home_value, away_value
        if subject == away_name:
            return away_value, home_value
        return None

    # Combines : "A et B" se tranche en tranchant A et B. On ne coupe que si les
    # DEUX moities se tranchent -- sans quoi un nom d'equipe contenant " et "
    # ferait passer une proposition simple pour un combine.
    if " et " in label:
        left, _, right = label.partition(" et ")
        first = check_offer(left, teams, values)
        second = check_offer(right, teams, values)
        if first is not None and second is not None:
            return first and second

    parity = _PARITY.match(label)
    if parity:
        return ((home_value + away_value) % 2 == 0) == (parity.group(1) == "pair")

    span = _RANGE.match(label)
    if span:
        low, high = int(span.group(1)), int(span.group(2))
        return low <= home_value + away_value <= high

    margin = _MARGIN.match(label)
    if margin:
        pair = side(margin.group(1))
        return None if pair is None else pair[0] - pair[1] >= int(margin.group(2))

    duel = _DUEL.match(label)
    if duel:
        pair = side(duel.group(2))
        if pair is None:
            return None
        return pair[0] >= pair[1] if duel.group(1) == "Autant ou plus" else pair[0] > pair[1]

    if label.endswith(_CLEAN_SHEET):
        pair = side(label[: -len(_CLEAN_SHEET)])
        # "n'encaisse aucun but" porte sur ce que l'ADVERSAIRE a marque.
        return None if pair is None else pair[1] == 0

    # Propositions d'issue : elles ne portent pas de seuil chiffre, mais se
    # tranchent tout aussi bien. Les laisser en "non verifiable" retirait du
    # bilan les paris les plus lisibles -- dont les doubles chances.
    if label in ("Pas de match nul", "Victoire de l'une ou l'autre (pas de nul)"):
        # Le second libelle vient de fiches emises par une version anterieure :
        # une prevision deja enregistree ne se reecrit pas, c'est donc au
        # verificateur de continuer a la comprendre.
        return home_value != away_value
    if label == "Match nul":
        return home_value == away_value
    if label.startswith("Victoire "):
        pair = side(label[len("Victoire "):])
        # Sujet non reconnu : on rend None. Deviner ferait pire que ne rien
        # dire -- l'ancien code retombait sur "victoire exterieure" et comptait
        # comme ratee une proposition qui s'etait realisee.
        return None if pair is None else pair[0] > pair[1]
    if label.endswith(" ou nul"):
        who = label[: -len(" ou nul")]
        if who == home_name:
            return home_value >= away_value
        if who == away_name:
            return away_value >= home_value
        return None
    if label == "Les deux equipes marquent":
        return home_value > 0 and away_value > 0
    if label == "Une equipe au moins ne marque pas":
        return home_value == 0 or away_value == 0

    match = _THRESHOLD.search(label)
    if not match:
        return None
    over = match.group(1).lower() == "plus"
    line = float(match.group(2))

    # "Equipe : plus de X" porte sur cette equipe seule ; sinon sur le total.
    subject, _, _ = label.partition(" : ")
    if subject == home_name:
        observed = home_value
    elif subject == away_name:
        observed = away_value
    else:
        observed = home_value + away_value
    return (observed > line) if over else (observed < line)


#: Familles de propositions, reconnues au libelle. L'ordre compte : « A et B »
#: est un combine avant d'etre autre chose, et « Entre 2 et 4 buts » contient
#: aussi " et " sans en etre un.
_FAMILLES = (
    ("fourchette", lambda p: bool(re.match(r"^Entre \d+ et \d+ .+ au total$", p))),
    ("combine", lambda p: " et " in p),
    ("parite", lambda p: bool(re.match(r"^Nombre total de .+ (pair|impair)$", p))),
    ("ecart", lambda p: bool(re.match(r"^.+ gagne par \d+ \D+ ou plus$", p))),
    ("duel", lambda p: bool(re.match(r"^(Autant ou plus|Plus) de .+ pour ", p))),
    ("cage inviolee", lambda p: p.endswith(_CLEAN_SHEET)),
    (
        "double chance",
        lambda p: p.endswith(" ou nul")
        or p in ("Pas de match nul", "Victoire de l'une ou l'autre (pas de nul)"),
    ),
    ("issue", lambda p: p == "Match nul" or p.startswith("Victoire ")),
    (
        "les deux marquent",
        lambda p: p
        in ("Les deux equipes marquent", "Une equipe au moins ne marque pas"),
    ),
    ("equipe", lambda p: " : " in p),
    ("total", lambda p: "au total" in p),
)


def famille_de(pari: str) -> str:
    """Famille d'une proposition, d'apres son libelle seul.

    Les fiches n'enregistrent pas la famille -- `offer_candidates` la connait a
    l'emission mais la table des offres ne garde que le libelle. La relire ici
    plutot que de changer le schema evite de reecrire des fiches deja emises :
    une prevision est un engagement pris a une date, et on ne la retouche pas.
    """
    for nom, reconnait in _FAMILLES:
        if reconnait(pari):
            return nom
    return "autre"


def _mesure_groupee(
    observations: list[tuple[str, float, bool]]
) -> dict[str, Any] | None:
    """Annonce, observe, ecart, et erreur type GROUPEE PAR MATCH.

    Le groupement n'est pas un raffinement. Une fiche porte une douzaine de
    propositions tirees du MEME lambda et tranchees par le MEME resultat : si le
    match finit 4-0, elles se realisent ou echouent ensemble. Les compter comme
    des tirages independants divise l'erreur type par la racine d'un effectif
    que l'echantillon n'a pas -- ici par presque deux, ce qui suffit a faire
    passer un ecart ordinaire pour un resultat.
    """
    n = len(observations)
    if not n:
        return None
    annonce = sum(p for _, p, _ in observations) / n
    observe = sum(1 for _, _, hit in observations if hit) / n
    ecart = annonce - observe

    par_match: dict[str, float] = {}
    for cle, p, hit in observations:
        par_match[cle] = par_match.get(cle, 0.0) + (p - (1.0 if hit else 0.0))
    matchs = len(par_match)
    # Sous cinq matchs, la variance entre groupes ne mesure rien : deux groupes
    # produisent une erreur type absurdement petite et une etoile qui ne veut
    # rien dire. On rend la ligne sans erreur type plutot qu'avec une fausse.
    if matchs >= 5:
        centre = [total - ecart * n / matchs for total in par_match.values()]
        erreur = (sum(c * c for c in centre) * matchs / (matchs - 1)) ** 0.5 / n
    else:
        erreur = None

    return {
        "propositions": n,
        "matchs": matchs,
        "annonce": annonce,
        "observe": observe,
        # Positif = le modele promet plus qu'il ne tient.
        "ecart": ecart,
        "erreur_type": erreur,
        "significatif": bool(erreur) and abs(ecart) > 2 * erreur,
    }


def bilan_par_option(limite: int = 5000) -> dict[str, Any]:
    """Ce que les propositions EMISES ont donne, ventilees trois facons.

    Distinct du banc d'essai, et le seul a porter sur ce qui a ete reellement
    engage : `backtest` rejoue des matchs et juge des propositions qu'aucune
    fiche n'a affichees, tandis qu'ici chaque ligne a ete ecrite avant le coup
    d'envoi et verifiee apres. C'est l'epreuve la plus honnete du projet, et
    elle n'etait accessible par aucune commande.

    Trois ventilations, parce qu'elles ne repondent pas a la meme question :

      - **par grandeur** : le modele se trompe-t-il plus sur les corners que sur
        les buts ? Les parametres sont propres a chaque grandeur, un ecart y est
        donc directement actionnable ;
      - **par famille** : les totaux et les lignes par equipe ne passent pas par
        le meme calcul, et un biais sur les uns n'implique rien sur les autres ;
      - **par tranche annoncee** : c'est la calibration proprement dite -- une
        proposition annoncee a 86 % doit se realiser 86 fois sur 100.

    Le compte de MATCHS accompagne partout celui des propositions : c'est lui
    qui dit ce que la mesure vaut.
    """
    with store.connect() as connection:
        lignes = connection.execute(
            """
            SELECT p.match_id, o.grandeur, o.pari, o.probabilite, o.verifie
              FROM offres o
              JOIN predictions p ON p.id = o.prediction_id
             WHERE o.verifie IS NOT NULL
             LIMIT ?
            """,
            (limite,),
        ).fetchall()

    if not lignes:
        return {"propositions": 0, "matchs": 0}

    observations = [
        (ligne["match_id"], ligne["probabilite"], bool(ligne["verifie"]))
        for ligne in lignes
    ]

    def ventiler(cle) -> list[dict[str, Any]]:
        groupes: dict[str, list[tuple[str, float, bool]]] = {}
        for ligne, obs in zip(lignes, observations):
            groupes.setdefault(cle(ligne), []).append(obs)
        rendu = []
        for nom, obs in groupes.items():
            mesure = _mesure_groupee(obs)
            if mesure:
                rendu.append(dict(mesure, nom=nom))
        rendu.sort(key=lambda ligne: -ligne["propositions"])
        return rendu

    def tranche(ligne) -> str:
        bas = int(ligne["probabilite"] * 10) * 10
        return "%d-%d %%" % (bas, bas + 10)

    return {
        "propositions": len(lignes),
        "matchs": len({ligne["match_id"] for ligne in lignes}),
        "ensemble": _mesure_groupee(observations),
        "par_grandeur": ventiler(lambda ligne: ligne["grandeur"]),
        "par_famille": ventiler(lambda ligne: famille_de(ligne["pari"])),
        "par_tranche": ventiler(tranche),
    }


_SENS_PARI = re.compile(r"\b(plus|moins) de \d", re.IGNORECASE)


def sens_de(pari: str) -> str | None:
    """« plus », « moins », ou None (issue, duel, combine). Le seuil exige un
    chiffre : « Victoire de l'une ou l'autre » ne doit pas passer pour un total."""
    libelle = pari.lower()
    if " et " in libelle or " pour " in libelle:
        return None
    trouve = _SENS_PARI.search(libelle)
    return trouve.group(1).lower() if trouve else None


def bilan_par_sens(limite: int = 20000) -> list[dict[str, Any]]:
    """Annonce / observe des « plus de » et des « moins de », par grandeur.

    Meme mesure que `bilan_par_option` (erreur type groupee par match). Les
    deux sens echouent differemment : un match ouvert fait tomber tous ses
    « moins » d'un coup, et c'est ce que la ventilation par famille cache.
    """
    with store.connect() as connection:
        lignes = connection.execute(
            "SELECT p.match_id, o.grandeur, o.pari, o.probabilite, o.verifie"
            " FROM offres o JOIN predictions p ON p.id = o.prediction_id"
            " WHERE o.verifie IS NOT NULL LIMIT ?",
            (limite,),
        ).fetchall()
    groupes: dict[tuple[str, str], list[tuple[str, float, bool]]] = {}
    for ligne in lignes:
        sens = sens_de(ligne["pari"])
        if not sens:
            continue
        obs = (ligne["match_id"], ligne["probabilite"], bool(ligne["verifie"]))
        groupes.setdefault(("Toutes", sens), []).append(obs)
        groupes.setdefault((ligne["grandeur"], sens), []).append(obs)
    rendu = []
    for (grandeur, sens), obs in groupes.items():
        mesure = _mesure_groupee(obs)
        if mesure:
            rendu.append(dict(mesure, grandeur=grandeur, sens=sens))
    rendu.sort(key=lambda m: (m["grandeur"] != "Toutes", m["grandeur"], m["sens"]))
    return rendu


def _valeurs_du_resultat(
    resultat: dict[str, Any] | None, grandeur: str
) -> tuple[float, float] | None:
    """(domicile, exterieur) d'une grandeur, depuis le resultat enregistre.

    Les buts sont sous "score", les autres sous leur libelle -- asymetrie qui
    vient du verificateur et qu'on ne reecrit pas : une fiche deja enregistree
    ne se reecrit pas.
    """
    brut = (resultat or {}).get("score" if grandeur == "Buts" else grandeur)
    if not isinstance(brut, str):
        return None
    gauche, separateur, droite = brut.partition(" - ")
    if not separateur:
        return None
    try:
        return float(gauche), float(droite)
    except ValueError:
        return None


def _grandeur_du_pari(pari: str) -> str:
    """Grandeur qu'une proposition engage, lue de son libelle."""
    minuscule = pari.lower()
    if "corner" in minuscule:
        return "Corners"
    if "tirs cadres" in minuscule or "tirs cadrés" in minuscule:
        return "Tirs cadres"
    if "carton" in minuscule:
        return "Cartons jaunes"
    return "Buts"


def verifier_coupon(coupon: dict[str, Any]) -> dict[str, Any]:
    """Ce que les conseils d'un coupon ont donne, une fois les matchs joues.

    C'est l'epreuve du coupon, et elle est du meme ordre que celle des fiches :
    chaque conseil a ete ecrit AVANT le coup d'envoi, et il est tranche par le
    meme code que les propositions d'une fiche (`check_offer`). Ce qui est
    compte ici est donc exactement ce qui serait compte ailleurs.

    Deux nombres, et le second seul juge :

      - le **taux de reussite** : combien de conseils se sont realises ;
      - l'**ecart entre annonce et observe**. Un coupon de conseils annonces a
        88 % qui en realise 75 % n'est pas un bon coupon, meme avec trois quarts
        de reussites -- il a promis plus qu'il n'a tenu. Et l'inverse vaut
        aussi : 70 % annonces pour 70 % realises est un coupon honnete.

    Les conseils dont le match n'est pas joue restent EN ATTENTE, jamais
    comptes comme rates : une prevision qui attend n'est pas une prevision
    fausse.
    """
    lignes: list[dict[str, Any]] = []
    for bloc_match in (coupon.get("conseils") or {}).get("matchs") or []:
        fiche = store.get(bloc_match.get("match_id", "")) or {}
        resultat = fiche.get("resultat_reel")
        equipes = tuple(
            (bloc_match.get("match") or "").split(" - ", 1) + [""]
        )[:2]
        for conseil in bloc_match.get("options") or []:
            pari = conseil.get("pari", "")
            valeurs = _valeurs_du_resultat(resultat, _grandeur_du_pari(pari))
            verdict = (
                check_offer(pari, equipes, valeurs) if valeurs else None
            )
            cote, source = _cote_du_conseil(conseil)
            lignes.append(
                {
                    "match": bloc_match.get("match", ""),
                    "match_id": bloc_match.get("match_id", ""),
                    "pari": pari,
                    "probabilite": conseil.get("probabilite"),
                    "cote": cote,
                    "cote_source": source,
                    "score": (resultat or {}).get("score", ""),
                    # None = le match n'est pas tranche, ou la proposition n'est
                    # pas verifiable ici. Ce n'est pas un echec.
                    "realise": verdict,
                }
            )

    tranches = [l for l in lignes if l["realise"] is not None]
    reussis = [l for l in tranches if l["realise"]]
    resume: dict[str, Any] = {
        "conseils": len(lignes),
        "tranches": len(tranches),
        "reussis": len(reussis),
        "en_attente": len(lignes) - len(tranches),
    }
    if tranches:
        annonce = sum(
            float(l["probabilite"] or 0) for l in tranches
        ) / len(tranches)
        observe = len(reussis) / len(tranches)
        resume.update(
            {
                "annonce_moyen": annonce,
                "observe": observe,
                # Positif = le coupon a promis plus qu'il n'a tenu.
                "ecart": annonce - observe,
            }
        )
    resume.update(rendement_du_coupon(lignes))
    return {"lignes": lignes, "resume": resume}


def _cote_du_conseil(conseil: dict[str, Any]) -> tuple[float | None, str]:
    """La cote d'un conseil : celle qu'il porte, sinon estimee (marge mediane)."""
    from ibet.modeles import offres

    for cle in ("cote", "cote_estimee"):
        if conseil.get(cle):
            return float(conseil[cle]), ("relevee" if cle == "cote" else "estimee")
    p = conseil.get("probabilite")
    return (offres.cote_estimee(float(p)), "estimee") if p else (None, "inconnue")


def rendement_du_coupon(lignes: list[dict[str, Any]]) -> dict[str, Any]:
    """Ce que le coupon aurait rapporte, pour une mise de 1.

    Le taux de reussite ne juge pas un coupon : « 13 sur 13 » a 1.02 l'option
    rapporte presque rien, « 2 sur 3 » a 1.35 peut rapporter davantage. Deux
    lectures :

      - **combine** : la PREMIERE option de chaque match, toutes ensemble -- le
        combine que le composeur propose. Gagne si toutes passent ;
      - **simples** : 1 mise sur chaque option tranchee, separement.

    Les cotes sont celles du coupon quand il en porte, sinon estimees d'apres la
    marge mediane des bookmakers (`offres.cote_estimee`) -- `cotes` le dit.
    """
    premieres: dict[str, dict[str, Any]] = {}
    for ligne in lignes:
        premieres.setdefault(ligne["match_id"] or ligne["match"], ligne)
    combine = list(premieres.values())
    sources = {l["cote_source"] for l in lignes if l.get("cote") is not None}
    rendu: dict[str, Any] = {"cotes": "relevees" if sources == {"relevee"} else "estimees"}

    if combine and all(l.get("cote") for l in combine):
        cote = 1.0
        for l in combine:
            cote *= l["cote"]
        if any(l["realise"] is False for l in combine):
            statut, gain = "perdu", -1.0
        elif all(l["realise"] is True for l in combine):
            statut, gain = "gagne", cote - 1.0
        else:
            statut, gain = "en attente", None
        rendu["combine"] = {"selections": len(combine), "cote": round(cote, 2),
                            "statut": statut, "gain": None if gain is None else round(gain, 2)}

    tranchees = [l for l in lignes if l["realise"] is not None and l.get("cote")]
    if tranchees:
        gain = sum((l["cote"] - 1.0) if l["realise"] else -1.0 for l in tranchees)
        rendu["simples"] = {"mises": len(tranchees), "gain": round(gain, 2),
                            "rendement": round(gain / len(tranchees), 4)}
    return rendu


def bilan_des_coupons(coupons: list[dict[str, Any]]) -> dict[str, Any]:
    """Rendement cumule des coupons tranches : combines et simples, mise 1."""
    combines = mises = 0
    gain_combines = gain_simples = 0.0
    gagnes = 0
    for coupon in coupons:
        resume = verifier_coupon(coupon)["resume"]
        c = resume.get("combine")
        if c and c["gain"] is not None:
            combines += 1
            gain_combines += c["gain"]
            gagnes += c["statut"] == "gagne"
        s = resume.get("simples")
        if s:
            mises += s["mises"]
            gain_simples += s["gain"]
    return {
        "combines": {"joues": combines, "gagnes": gagnes, "gain": round(gain_combines, 2),
                     "rendement": round(gain_combines / combines, 4) if combines else None},
        "simples": {"mises": mises, "gain": round(gain_simples, 2),
                    "rendement": round(gain_simples / mises, 4) if mises else None},
        "cotes": "estimees d'apres la marge mediane des bookmakers, sauf cote relevee",
    }


def verify_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tranche les fiches passees, **en place**. Retourne le rapport.

    Les matchs d'une meme journee sont recuperes en une seule requete : verifier
    douze fiches du meme jour n'en demande qu'une, pas douze. Le cache est
    volontairement ignore -- c'est precisement le statut a jour que l'on vient
    chercher.
    """
    report: list[dict[str, Any]] = []
    days: dict[str, list[dict[str, Any]]] = {}
    # Une journee que la source refuse n'est plus un abandon : l'archive prend
    # le relais. On garde l'erreur de cote pour ne la rapporter que si elle ne
    # sait pas non plus, et une seule fois par journee.
    pannes: dict[str, str] = {}

    for record in records:
        try:
            _verifier_une(record, days, pannes, report)
        except Exception as exc:  # noqa: BLE001 - la cause est rapportee
            # Meme regle que `enrich_with_stats` et `backtest.run` : une fiche
            # qui echoue est signalee, elle n'emporte pas les autres. Ce chemin
            # conditionne TOUTE la mesure du projet -- sans fiche tranchee, ni
            # le bilan des propositions ni celui du marche n'ont de matiere --,
            # et le faire dependre de la fiche la plus fragile du lot serait le
            # rendre inutilisable au premier enregistrement inattendu.
            report.append(
                {
                    "match": record.get("match", "(fiche sans libelle)"),
                    "statut": "erreur",
                    "detail": "%s : %s" % (type(exc).__name__, exc),
                }
            )
    return report


def _verifier_une(
    record: dict[str, Any],
    days: dict[str, list[dict[str, Any]]],
    pannes: dict[str, str],
    report: list[dict[str, Any]],
) -> None:
    """Tranche une fiche, en place. `days` sert de cache de journee partage."""
    # Une fiche mal formee ne doit pas emporter les autres. Le coup d'envoi
    # peut etre vide -- le schema l'autorise (`DEFAULT ''`) -- et un
    # `.split()[0]` sur une chaine vide levait une IndexError qui
    # interrompait TOUT le lot : plus une seule fiche tranchee, donc plus
    # rien a mesurer, et aucun message disant pourquoi.
    day = (record.get("coup_denvoi_local") or "").split(" ")[0]
    if not day:
        report.append(
            {
                "match": record.get("match", "(fiche sans libelle)"),
                "statut": "coup d'envoi illisible",
                "detail": "impossible de savoir quel jour interroger.",
            }
        )
        return
    if day not in days:
        try:
            days[day] = api_client.get_matches(
                day, provider="flashscore", use_cache=False
            )
            # Tout ce qui est termine ce jour-la est archive, pas seulement les
            # matchs qui portent une fiche : ca ne coute aucune requete de plus,
            # et une prevision peut etre emise plus tard sur un match deja joue.
            store.archiver_journee(days[day], api_client.FINISHED)
        except api_client.ApiError as exc:
            # Surtout ne pas abandonner ici : le cas le plus frequent est une
            # date sortie de la fenetre de sept jours, et c'est precisement
            # celui que l'archive est faite pour couvrir.
            days[day] = []
            pannes[day] = str(exc)
    matches = days[day]
    # L'identifiant d'abord, l'URL en repli, et JAMAIS de rapprochement sur
    # deux valeurs vides. L'ancienne condition retombait sur la comparaison
    # d'URL des que l'identifiant ne correspondait pas, et lisait
    # `record["url"]` sans garde : une fiche sans cette cle levait une
    # KeyError qui emportait le lot entier.
    identifier = record.get("match_id") or ""
    url = record.get("url") or ""
    match = next(
        (
            m
            for m in matches
            if (identifier and m.get("match_id") == identifier)
            or (url and m.get("url") == url)
        ),
        None,
    )
    if match is None or match["statut"] != api_client.FINISHED:
        # La source ne publie que sept jours autour d'aujourd'hui : passe ce
        # delai elle ne rend plus le match, et la fiche resterait « en attente »
        # pour toujours. L'archive, elle, garde -- c'est tout son objet.
        # Les fiches d'avant la table `resultats` ne portent pas de `match_id`
        # en clair : il se deduit de leur URL, et c'est exactement ce que fait
        # `store.match_id_of`. Chercher sur `identifier` seul revenait a
        # interroger l'archive avec une chaine vide -- elle ne repondait jamais.
        reference = identifier or store.match_id_of(record)
        garde = store.resultat(reference) if reference else None
        if (
            garde
            and garde.get("statut") == api_client.FINISHED
            and garde.get("score_domicile") is not None
        ):
            match = {
                "match_id": reference,
                "provider": "flashscore",
                "statut": api_client.FINISHED,
                "score_domicile": garde["score_domicile"],
                "score_exterieur": garde["score_exterieur"],
            }
        elif pannes.get(day):
            report.append(
                {"match": record["match"], "statut": "source indisponible",
                 "detail": pannes[day]}
            )
            return
        else:
            report.append({"match": record["match"], "statut": "en attente"})
            return

    values = actual_values(match)
    if not values:
        report.append({"match": record["match"], "statut": "sans resultat"})
        return

    teams = tuple(record["match"].split(" - ", 1))
    record["resultat_reel"] = {
        "score": "%s - %s" % (match["score_domicile"], match["score_exterieur"]),
        **{label: "%g - %g" % v for label, v in values.items() if label != "Buts"},
    }

    lines = {"match": record["match"], "statut": "verifie",
             "score": record["resultat_reel"]["score"], "grandeurs": [], "offres": []}
    for metric in record["grandeurs"]:
        observed = values.get(metric["grandeur"])
        if observed:
            lines["grandeurs"].append({
                "grandeur": metric["grandeur"],
                "prevu": metric["attendu_total"],
                "reel": observed[0] + observed[1],
                "prevu_equipes": (metric["attendu_domicile"], metric["attendu_exterieur"]),
                "reel_equipes": observed,
            })
        # `.get` : une fiche peut ne porter aucune proposition sur cette
        # grandeur (aucune ne depassait le seuil d'interet a l'emission).
        for offer in metric.get("offres") or []:
            verdict = (
                check_offer(offer["pari"], teams, observed) if observed else None
            )
            offer["verifie"] = verdict
            lines["offres"].append(
                {"pari": offer["pari"], "probabilite": offer["probabilite"],
                 "realise": verdict}
            )
    report.append(lines)


def verify_store(tz_name: str | None = None, all_pending: bool = False) -> list[dict[str, Any]]:
    """Tranche les fiches de la base dont le match est termine.

    Seules les fiches **mures** sont examinees : interroger la source pour un
    match qui n'a pas commence ne peut rien apprendre, et le projet tient a ne
    pas la solliciter pour rien. `all_pending` force l'examen de toutes.
    """
    records = [
        record
        for record in store.pending()
        if all_pending or is_due(record, tz_name)
    ]
    report = verify_records(records)
    for record in records:
        # Seules les fiches effectivement tranchees sont reecrites : une fiche
        # dont le match n'est pas fini doit rester intacte, resultat compris.
        if record.get("resultat_reel"):
            store.save(record)
    return report


def verify(path: Path = RECORD) -> list[dict[str, Any]]:
    """Tranche le fichier historique et le met a jour sur place."""
    records = json.loads(path.read_text(encoding="utf-8"))
    report = verify_records(records)
    path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def render(report: list[dict[str, Any]]) -> str:
    """Bilan lisible d'un passage de verification.

    Les fiches tranchees d'abord, avec le score et le sort de chaque
    proposition ; celles qui attendent encore sont juste denombrees -- les
    lister toutes noierait le peu qui vient de changer.
    """
    settled = [line for line in report if line.get("statut") == "verifie"]
    # « En attente » et « bloquee » ne sont pas la meme chose, et les confondre
    # coute cher : une fiche qui attend se tranchera d'elle-meme au prochain
    # passage, une fiche bloquee ne se tranchera JAMAIS. Or c'est la
    # verification qui alimente toute la mesure du projet -- sans fiche
    # tranchee, ni le bilan des propositions ni celui du marche n'ont de
    # matiere. Une fiche bloquee doit donc se voir, et non se fondre dans un
    # compte d'attente qui a l'air normal.
    waiting = [line for line in report if line.get("statut") == "en attente"]
    stuck = [
        line
        for line in report
        if line.get("statut") not in ("verifie", "en attente")
    ]
    out: list[str] = []

    for line in settled:
        offers = line["offres"]
        won = sum(1 for offer in offers if offer["realise"] is True)
        judged = sum(1 for offer in offers if offer["realise"] is not None)
        out.append("")
        out.append("%s  %s" % (line["match"], line["score"]))

        for quantity in line["grandeurs"]:
            out.append(
                "  %-14s prevu %5.2f   reel %3g"
                % (quantity["grandeur"], quantity["prevu"], quantity["reel"])
            )
        for offer in offers:
            mark = {True: "oui", False: "non", None: " ? "}[offer["realise"]]
            out.append(
                "  [%s] %-46s %5.1f%%"
                % (mark, offer["pari"][:46], offer["probabilite"] * 100)
            )
        out.append("  -> %d proposition(s) sur %d realisee(s)" % (won, judged))

    if waiting:
        out.append("")
        out.append(
            "En attente : %d fiche(s) dont le match n'est pas tranche." % len(waiting)
        )

    if stuck:
        out.append("")
        out.append(
            "Bloquees : %d fiche(s) que la verification ne peut pas trancher."
            % len(stuck)
        )
        for line in stuck:
            out.append(
                "  %-34s %s%s"
                % (
                    line.get("match", "(sans libelle)")[:34],
                    line.get("statut", "?"),
                    " -- %s" % line["detail"] if line.get("detail") else "",
                )
            )
        out.append(
            "  Ces fiches ne se trancheront pas d'elles-memes : tant qu'elles "
            "restent la, elles ne comptent dans aucune mesure."
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Confronte les previsions enregistrees aux resultats reels."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Verifie le fichier historique au lieu de la base",
    )
    parser.add_argument(
        "--toutes",
        action="store_true",
        help="Examine toutes les fiches en attente, y compris les matchs a venir",
    )
    args = parser.parse_args(argv)

    try:
        report = verify(RECORD) if args.json else verify_store(all_pending=args.toutes)
    except api_client.ApiError as exc:
        print("Erreur : %s" % exc, file=sys.stderr)
        return 1
    except FileNotFoundError:
        print("Aucun fichier %s a verifier." % RECORD.name, file=sys.stderr)
        return 1

    settled = sum(1 for line in report if line.get("statut") == "verifie")
    if not report:
        print("Aucun match a verifier pour l'instant.")
        if not args.json:
            print("Bilan cumule :", store.tally())
        return 0
    print("%d fiche(s) examinee(s), %d tranchee(s)." % (len(report), settled))
    if report:
        print(render(report))
    if not args.json:
        etat = peremption()
        if etat["expirees"]:
            print()
            print(
                "HORS FENETRE : %d fiche(s) dont le match n'est plus publie "
                "par la source." % len(etat["expirees"])
            )
            for record in etat["expirees"][:8]:
                print(
                    "  %-34s %s"
                    % (record.get("match", "")[:34],
                       record.get("coup_denvoi_local", ""))
                )
            print(
                "  L'archive des resultats ne les connait pas non plus. "
                "`python -m ibet rattraper` va les chercher dans l'historique "
                "des equipes, qui remonte bien au-dela des %d jours."
                % JOURS_FENETRE
            )
        if etat["urgentes"]:
            print()
            print(
                "A TRANCHER SOUS %d JOUR(S) : %d fiche(s), sans quoi elles "
                "seront perdues." % (JOURS_ALERTE, len(etat["urgentes"]))
            )
            for record in etat["urgentes"][:8]:
                print(
                    "  %-34s %s"
                    % (record.get("match", "")[:34],
                       record.get("coup_denvoi_local", ""))
                )
        print()
        print("Bilan cumule :", store.tally())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
