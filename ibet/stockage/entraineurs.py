"""Base des entraineurs : systeme, style de jeu, pressing, et les evenements qui suivent.

Un entraineur n'est pas une equipe. L'historique d'une equipe melange ses
entraineurs successifs, et un entraineur emporte d'un club a l'autre des
habitudes qui se lisent dans les statistiques : le systeme qu'il aligne, la part
de jeu long, la hauteur de son pressing -- et, par ricochet, les EVENEMENTS que
ses matchs produisent. Une equipe qui presse haut provoque des fautes et des
cartons ; une equipe qui centre beaucoup obtient des corners.

Ce module construit cette base a partir de ce que le projet archive deja : les
feuilles de match (entraineur et systeme de chaque equipe, `store.feuilles`) et
les statistiques d'equipe de chaque resultat (`resultats.stats`). Aucune
requete.

**Des indices, pas des adjectifs.** Comme la base des arbitres, chaque mesure
est rapportee a la moyenne de la competition, du MEME cote (domicile ou
exterieur) : 1.00 est la moyenne, 1.30 trente pour cent de plus. Un entraineur
vu peu de fois est tire vers 1 par `A_PRIORI` matchs fictifs a la moyenne. Les
etiquettes de style (« pressing haut », « jeu direct »...) sont DEDUITES des
indices, par des seuils explicites (`ETIQUETTES`), pour rester verifiables.

**Le pressing** se mesure par un PPDA approche : passes tentees par l'adversaire
divisees par nos actions defensives (tacles tentes + fautes). Le vrai PPDA ne
compte que les passes adverses dans leurs 60 % de terrain et ajoute les
interceptions ; la source ne donne ni les zones, ni les interceptions par
equipe. Plus il est BAS, plus l'equipe presse. Les hors-jeux provoques
completent la lecture : une ligne defensive haute en provoque beaucoup.

**Ce que la base ne dit pas.** Un grand club a le ballon parce qu'il est
meilleur, pas seulement parce que son entraineur le veut : possession, xG et
tirs melangent le style et le niveau de l'effectif. Les indices ne sont pas
corriges de la force de l'adversaire.

**Ce que la mesure a donne.** Stabilite, en coupant les matchs de chaque
entraineur en deux moities (159 entraineurs a 16 matchs ou plus, correlation
des indices d'une moitie a l'autre) : style tres stable (possession 0.79,
passes 0.87, jeu long 0.86), pressing et hors-jeux provoques 0.59 / 0.69,
fautes 0.59, xG produits 0.68 ; corners obtenus 0.47 ; totaux du match faibles
(corners 0.10, buts 0.11, cartons 0.25).

Prevision, en walk-forward sur 3 275 matchs, contre l'historique de l'equipe
(corners, fautes, cartons, tirs, tirs cadres, buts) :

  - historique de l'equipe restreint a l'entraineur actuel : PIRE partout
    (t = +3 a +11), meme sur les huit premiers matchs d'un nouvel entraineur ;
  - carriere de l'entraineur ailleurs quand il arrive : ~170 matchs, rien de
    significatif ;
  - style de l'entraineur adverse : aucun gain hors echantillon ;
  - rotation du onze (connue avec la composition) : rien de solide.

La base sert donc a LIRE un match (bloc `entraineurs` des fiches, voir
`resume`), et ne deplace aucune probabilite.

Usage :
    python -m ibet entraineurs --construire        # (re)construit la base
    python -m ibet entraineurs Guardiola           # fiche d'un entraineur
    python -m ibet entraineurs --liste             # tous, par nombre de matchs
    python -m ibet entraineurs --etiquette "pressing haut"
    python -m ibet entraineurs --export            # donnees/exports/entraineurs.json et .csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from ibet import chemins
from ibet.stockage import store

DB_PATH = chemins.BASE_ENTRAINEURS

SCHEMA = """
CREATE TABLE IF NOT EXISTS entraineurs (
    id               TEXT PRIMARY KEY,
    nom              TEXT NOT NULL,
    matchs           INTEGER NOT NULL,
    equipe_actuelle  TEXT NOT NULL DEFAULT '',
    dernier_match    TEXT NOT NULL DEFAULT '',
    systeme_principal TEXT NOT NULL DEFAULT '',
    etiquettes       TEXT NOT NULL DEFAULT '[]',
    fiche            TEXT NOT NULL,
    construit_le     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS entraineurs_nom ON entraineurs(nom);

-- Une ligne par equipe et par match : ce que l'equipe de l'entraineur a fait.
-- C'est la matiere premiere des fiches, gardee pour qu'un indice se verifie.
CREATE TABLE IF NOT EXISTS matchs_entraineurs (
    match_id       TEXT NOT NULL,
    cote           TEXT NOT NULL,
    entraineur_id  TEXT NOT NULL,
    equipe         TEXT NOT NULL,
    adversaire     TEXT NOT NULL,
    date           TEXT NOT NULL,
    competition    TEXT NOT NULL,
    systeme        TEXT NOT NULL DEFAULT '',
    mesures        TEXT NOT NULL,
    PRIMARY KEY (match_id, cote)
);
CREATE INDEX IF NOT EXISTS matchs_entraineurs_id ON matchs_entraineurs(entraineur_id);
"""

#: Matchs fictifs a la moyenne de la competition ajoutes a chaque indice : un
#: entraineur vu trois fois n'a pas de style mesurable, et son indice reste
#: pres de 1 tant que les matchs ne s'accumulent pas.
A_PRIORI = 5.0

#: Matchs fictifs a la moyenne globale pour la reference d'une competition.
A_PRIORI_COMPETITION = 20.0

#: Sous ce nombre de matchs, aucune etiquette : trois matchs ne font pas un style.
MATCHS_ETIQUETTE = 10

#: Competitions ecartees : un amical ne dit rien d'un style.
_AMICAL = re.compile(r"amical|friendly", re.I)

# ---------------------------------------------------------------------------
# Mesures d'un match
# ---------------------------------------------------------------------------

#: Grandeurs comptees pour l'equipe ET pour son adversaire : ce qu'elle produit,
#: ce qu'elle concede.
EVENEMENTS = ("buts", "xg", "tirs_total", "tirs_cadres", "corners",
              "cartons_jaunes", "cartons_rouges", "fautes", "hors_jeux",
              "grosses_occasions", "touches_surface")

#: Libelles des evenements, pour l'affichage.
LIBELLES = {
    "buts": "buts", "xg": "xG", "tirs_total": "tirs", "tirs_cadres": "tirs cadres",
    "corners": "corners", "cartons_jaunes": "cartons jaunes",
    "cartons_rouges": "cartons rouges", "fautes": "fautes",
    "hors_jeux": "hors-jeux", "grosses_occasions": "grosses occasions",
    "touches_surface": "touches dans la surface",
}


def _nombre(valeur: Any) -> float | None:
    if isinstance(valeur, (int, float)):
        return float(valeur)
    if isinstance(valeur, str):
        texte = valeur.strip().rstrip("%").replace(",", ".")
        try:
            return float(texte)
        except ValueError:
            return None
    return None


def _fraction(valeur: Any) -> tuple[float, float] | None:
    """« 70% (186/265) » -> (186, 265) : reussies, tentees."""
    if not isinstance(valeur, str):
        return None
    trouve = re.search(r"\((\d+)\s*/\s*(\d+)\)", valeur)
    if not trouve:
        return None
    return float(trouve.group(1)), float(trouve.group(2))


def mesures_du_match(
    moi: dict[str, Any], lui: dict[str, Any], buts_pour: int | None, buts_contre: int | None
) -> dict[str, float]:
    """Ce qu'une equipe a fait dans un match, a partir des deux blocs de stats.

    Rend un dictionnaire plat : `pour_<evenement>`, `contre_<evenement>`, et les
    grandeurs de style (possession, passes, part de jeu long, PPDA...). Une
    grandeur absente de la source est simplement absente : elle ne compte ni
    pour ni contre l'entraineur.
    """
    m: dict[str, float] = {}
    for cle in EVENEMENTS:
        if cle == "buts":
            pour, contre = buts_pour, buts_contre
        else:
            pour, contre = _nombre(moi.get(cle)), _nombre(lui.get(cle))
        if pour is not None:
            m["pour_" + cle] = float(pour)
        if contre is not None:
            m["contre_" + cle] = float(contre)

    possession = _nombre(moi.get("possession"))
    if possession is not None:
        m["possession"] = possession

    passes, passes_adverses = _fraction(moi.get("passes")), _fraction(lui.get("passes"))
    if passes:
        m["passes_tentees"] = passes[1]
        m["passes_reussies"] = passes[0]
    longues = _fraction(moi.get("passes_longues"))
    if longues and passes:
        m["passes_longues"] = longues[1]
    centres = _fraction(moi.get("centres"))
    if centres:
        m["centres"] = centres[1]
    dernier_tiers = _fraction(moi.get("passes_dernier_tiers"))
    if dernier_tiers:
        m["passes_dernier_tiers"] = dernier_tiers[1]

    # PPDA approche : passes adverses par action defensive.
    tacles = _fraction(moi.get("tacles"))
    fautes = _nombre(moi.get("fautes"))
    if passes_adverses and tacles and fautes is not None:
        actions = tacles[1] + fautes
        if actions > 0:
            m["ppda_passes_adverses"] = passes_adverses[1]
            m["ppda_actions"] = actions
    return m


# ---------------------------------------------------------------------------
# Style : grandeurs agregees en rapports de sommes
# ---------------------------------------------------------------------------

#: Grandeurs de style : nom -> (numerateur, denominateur). Un denominateur None
#: veut dire « par match ». Les rapports se calculent sur les SOMMES, pas en
#: moyennant des rapports par match : un match a 3 passes longues sur 5 ne doit
#: pas peser autant qu'un match a 60 sur 500.
STYLE = {
    "possession": ("possession", None),
    "passes": ("passes_tentees", None),
    "precision_passes": ("passes_reussies", "passes_tentees"),
    "part_jeu_long": ("passes_longues", "passes_tentees"),
    "centres": ("centres", None),
    "passes_dernier_tiers": ("passes_dernier_tiers", None),
    "ppda": ("ppda_passes_adverses", "ppda_actions"),
    "hors_jeux_provoques": ("contre_hors_jeux", None),
    "rotation": ("rotation", None),
    "remplacements": ("remplacements", None),
    "premier_changement": ("premier_changement", None),
    "points": ("points", None),
}

LIBELLES_STYLE = {
    "possession": "possession (%)",
    "passes": "passes tentees par match",
    "precision_passes": "precision des passes",
    "part_jeu_long": "part de passes longues",
    "centres": "centres par match",
    "passes_dernier_tiers": "passes dans le dernier tiers par match",
    "ppda": "PPDA approche (bas = pressing)",
    "hors_jeux_provoques": "hors-jeux provoques par match",
    "rotation": "titulaires changes d'un match a l'autre",
    "remplacements": "remplacements par match",
    "premier_changement": "minute du premier changement",
    "points": "points par match",
}


def _cle_competition(nom: str) -> str:
    return (nom or "").split(" - ", 1)[0].strip()


def _grandeurs(mesures: dict[str, float]) -> dict[str, tuple[float, float]]:
    """(numerateur, denominateur) de chaque grandeur suivie, pour un match."""
    rendu: dict[str, tuple[float, float]] = {}
    for nom, (num, den) in STYLE.items():
        if num in mesures and (den is None or mesures.get(den)):
            rendu["style:" + nom] = (mesures[num], 1.0 if den is None else mesures[den])
    for cle in EVENEMENTS:
        for sens in ("pour", "contre"):
            if sens + "_" + cle in mesures:
                rendu["%s:%s" % (sens, cle)] = (mesures[sens + "_" + cle], 1.0)
    for cle in ("buts", "corners", "cartons_jaunes", "fautes"):
        if "pour_" + cle in mesures and "contre_" + cle in mesures:
            rendu["total:" + cle] = (mesures["pour_" + cle] + mesures["contre_" + cle], 1.0)
    return rendu


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def lignes_des_feuilles() -> list[dict[str, Any]]:
    """Une ligne par equipe et par match, avec entraineur, systeme et mesures."""
    with store.connect() as connexion:
        rangs = connexion.execute(
            """
            SELECT f.match_id, f.date, f.competition, f.domicile, f.exterieur,
                   f.feuille, r.stats, r.score_domicile, r.score_exterieur
              FROM feuilles f JOIN resultats r ON r.match_id = f.match_id
            """
        ).fetchall()
    lignes = []
    for rang in rangs:
        if _AMICAL.search(rang["competition"] or ""):
            continue
        feuille = json.loads(rang["feuille"] or "{}")
        stats = json.loads(rang["stats"] or "{}")
        for cote, autre, equipe, adversaire, pour, contre in (
            ("domicile", "exterieur", rang["domicile"], rang["exterieur"],
             rang["score_domicile"], rang["score_exterieur"]),
            ("exterieur", "domicile", rang["exterieur"], rang["domicile"],
             rang["score_exterieur"], rang["score_domicile"]),
        ):
            bloc = feuille.get(cote) or {}
            coach = bloc.get("entraineur") or {}
            if not coach.get("id"):
                continue
            joueurs = bloc.get("joueurs") or []
            lignes.append({
                "match_id": rang["match_id"], "cote": cote,
                "titulaires": [j.get("id") for j in joueurs if j.get("titulaire") and j.get("id")],
                # Minute d'entree d'un remplacant : 90 moins ses minutes. Le
                # temps additionnel n'est pas connu ; l'ordre, lui, est juste.
                "entrees": [max(0, 90 - int(j.get("minutes") or 0)) for j in joueurs
                            if not j.get("titulaire") and (j.get("minutes") or 0) > 0],
                "entraineur_id": coach["id"], "nom": coach.get("nom", ""),
                "equipe": equipe, "adversaire": adversaire,
                "date": rang["date"], "competition": _cle_competition(rang["competition"]),
                "systeme": bloc.get("systeme") or "",
                "mesures": mesures_du_match(
                    stats.get(cote) or {}, stats.get(autre) or {}, pour, contre
                ),
            })
    ajouter_compositions(lignes)
    return lignes


#: Au-dela de cet ecart entre deux matchs, la rotation n'est pas mesuree : un
#: onze qui change apres une treve dit l'effectif, pas un choix de gestion.
ROTATION_JOURS = 21


def ajouter_compositions(lignes: list[dict[str, Any]]) -> None:
    """Rotation, remplacements et points, ajoutes aux mesures de chaque ligne.

    La rotation compte les titulaires qui n'etaient pas titulaires au match
    PRECEDENT de la meme equipe (0 = meme onze, 11 = onze entierement change).
    """
    precedent: dict[str, dict[str, Any]] = {}
    for ligne in sorted(lignes, key=lambda l: (l["date"], l["match_id"])):
        m = ligne["mesures"]
        if "pour_buts" in m and "contre_buts" in m:
            ecart = m["pour_buts"] - m["contre_buts"]
            m["points"] = 3.0 if ecart > 0 else 1.0 if ecart == 0 else 0.0
        entrees = ligne.get("entrees") or []
        if len(ligne.get("titulaires") or []) == 11:
            m["remplacements"] = float(len(entrees))
            if entrees:
                m["premier_changement"] = float(min(entrees))
            avant = precedent.get(ligne["equipe"])
            if avant and len(avant["titulaires"]) == 11 and _jours(avant["date"], ligne["date"]) <= ROTATION_JOURS:
                m["rotation"] = float(11 - len(set(ligne["titulaires"]) & set(avant["titulaires"])))
            precedent[ligne["equipe"]] = ligne


def _jours(avant: str, apres: str) -> int:
    try:
        return (datetime.fromisoformat(apres[:10]) - datetime.fromisoformat(avant[:10])).days
    except ValueError:
        return 10 ** 6


def references(lignes: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, float]]:
    """Moyenne de chaque grandeur par (competition, cote), tiree vers la moyenne globale.

    Rend {(competition, cote): {grandeur: valeur par unite de denominateur}}, et
    la cle ("", cote) pour la moyenne globale.
    """
    sommes: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(lambda: [0.0, 0.0, 0])
    )
    for ligne in lignes:
        for cle in ((ligne["competition"], ligne["cote"]), ("", ligne["cote"])):
            for nom, (num, den) in _grandeurs(ligne["mesures"]).items():
                somme = sommes[cle][nom]
                somme[0] += num
                somme[1] += den
                somme[2] += 1
    globale = {
        cote: {nom: s[0] / s[1] for nom, s in sommes[("", cote)].items() if s[1] > 0}
        for cote in ("domicile", "exterieur")
    }
    rendu: dict[tuple[str, str], dict[str, float]] = {
        ("", cote): globale[cote] for cote in globale
    }
    for (competition, cote), grandeurs in sommes.items():
        if not competition:
            continue
        rendu[(competition, cote)] = {}
        for nom, (num, den, matchs) in grandeurs.items():
            ref = globale[cote].get(nom)
            if ref is None or den <= 0:
                continue
            # Tirage vers la moyenne globale : A_PRIORI_COMPETITION matchs
            # fictifs, chacun avec le denominateur moyen de la competition.
            den_moyen = den / matchs
            poids = A_PRIORI_COMPETITION * den_moyen
            rendu[(competition, cote)][nom] = (num + poids * ref) / (den + poids)
    return rendu


def _etiquettes(style: dict[str, dict[str, float]], evenements: dict[str, dict[str, float]],
                seuils: dict[str, tuple[float, float]]) -> list[str]:
    """Etiquettes de style, par seuils sur les indices (quintiles des entraineurs etablis)."""
    def indice(bloc: dict[str, dict[str, float]], nom: str) -> float | None:
        return (bloc.get(nom) or {}).get("indice")

    def haut(nom: str, valeur: float | None) -> bool:
        return valeur is not None and nom in seuils and valeur >= seuils[nom][1]

    def bas(nom: str, valeur: float | None) -> bool:
        return valeur is not None and nom in seuils and valeur <= seuils[nom][0]

    rendu = []
    possession = (style.get("possession") or {}).get("valeur")
    if possession is not None and possession >= 55:
        rendu.append("possession")
    if possession is not None and possession <= 45:
        rendu.append("jeu sans ballon")
    ppda = indice(style, "ppda")
    if bas("style:ppda", ppda):
        rendu.append("pressing haut")
    if haut("style:ppda", ppda):
        rendu.append("bloc bas")
    if haut("style:hors_jeux_provoques", indice(style, "hors_jeux_provoques")):
        rendu.append("ligne haute")
    if haut("style:part_jeu_long", indice(style, "part_jeu_long")):
        rendu.append("jeu direct")
    if bas("style:part_jeu_long", indice(style, "part_jeu_long")):
        rendu.append("jeu court")
    if haut("style:centres", indice(style, "centres")):
        rendu.append("jeu par les couloirs")
    if haut("pour:xg", indice(evenements, "pour:xg")):
        rendu.append("offensif")
    if bas("contre:xg", indice(evenements, "contre:xg")):
        rendu.append("solide")
    if haut("contre:xg", indice(evenements, "contre:xg")):
        rendu.append("permeable")
    if haut("pour:fautes", indice(evenements, "pour:fautes")):
        rendu.append("rugueux")
    if haut("pour:cartons_jaunes", indice(evenements, "pour:cartons_jaunes")):
        rendu.append("indiscipline")
    if haut("style:rotation", indice(style, "rotation")):
        rendu.append("rotation forte")
    if bas("style:rotation", indice(style, "rotation")):
        rendu.append("onze stable")
    if bas("style:premier_changement", indice(style, "premier_changement")):
        rendu.append("coaching precoce")
    if haut("total:buts", indice(evenements, "total:buts")):
        rendu.append("matchs ouverts")
    if bas("total:buts", indice(evenements, "total:buts")):
        rendu.append("matchs fermes")
    return rendu


#: Libelle de chaque etiquette, avec la regle qui la donne.
ETIQUETTES = {
    "possession": "possession moyenne >= 55 %",
    "jeu sans ballon": "possession moyenne <= 45 %",
    "pressing haut": "PPDA dans le quintile le plus bas",
    "bloc bas": "PPDA dans le quintile le plus haut",
    "ligne haute": "hors-jeux provoques dans le quintile le plus haut",
    "jeu direct": "part de passes longues dans le quintile le plus haut",
    "jeu court": "part de passes longues dans le quintile le plus bas",
    "jeu par les couloirs": "centres dans le quintile le plus haut",
    "offensif": "xG produits dans le quintile le plus haut",
    "solide": "xG concedes dans le quintile le plus bas",
    "permeable": "xG concedes dans le quintile le plus haut",
    "rugueux": "fautes commises dans le quintile le plus haut",
    "indiscipline": "cartons jaunes recus dans le quintile le plus haut",
    "rotation forte": "titulaires changes dans le quintile le plus haut",
    "onze stable": "titulaires changes dans le quintile le plus bas",
    "coaching precoce": "premier changement dans le quintile le plus tot",
    "matchs ouverts": "buts du match (deux equipes) dans le quintile le plus haut",
    "matchs fermes": "buts du match (deux equipes) dans le quintile le plus bas",
}


def construire(lignes: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """Fiches de tous les entraineurs, a partir des lignes par match."""
    lignes = lignes if lignes is not None else lignes_des_feuilles()
    refs = references(lignes)
    ligne_nom = {l["entraineur_id"]: l["nom"] for l in lignes if l["nom"]}

    par_coach: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ligne in lignes:
        par_coach[ligne["entraineur_id"]].append(ligne)

    # Predecesseur : l'entraineur de l'equipe au match qui precede l'arrivee.
    predecesseurs: dict[tuple[str, str], str] = {}
    dernier_coach: dict[str, str] = {}
    for ligne in sorted(lignes, key=lambda l: (l["date"], l["match_id"])):
        avant = dernier_coach.get(ligne["equipe"])
        if avant and avant != ligne["entraineur_id"]:
            predecesseurs[(ligne["equipe"], ligne["entraineur_id"])] = ligne_nom.get(avant, avant)
        dernier_coach[ligne["equipe"]] = ligne["entraineur_id"]

    fiches: dict[str, dict[str, Any]] = {}
    for coach_id, matchs in par_coach.items():
        matchs.sort(key=lambda l: l["date"])
        # Somme observee et somme attendue (reference de la competition, meme
        # cote) de chaque grandeur, sur les memes matchs.
        observe: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0])
        for ligne in matchs:
            ref = refs.get((ligne["competition"], ligne["cote"])) or refs[("", ligne["cote"])]
            for nom, (num, den) in _grandeurs(ligne["mesures"]).items():
                if nom not in ref:
                    continue
                cumul = observe[nom]
                cumul[0] += num
                cumul[1] += den
                cumul[2] += ref[nom] * den
                cumul[3] += 1

        def bloc(prefixe: str) -> dict[str, dict[str, float]]:
            rendu = {}
            for nom, (num, den, attendu, n) in observe.items():
                if not nom.startswith(prefixe) or den <= 0 or attendu <= 0:
                    continue
                # Indice lisse : A_PRIORI matchs fictifs a la moyenne.
                poids = A_PRIORI * attendu / n
                rendu[nom.split(":", 1)[1] if prefixe == "style:" else nom] = {
                    "valeur": round(num / den, 3),
                    "reference": round(attendu / den, 3),
                    "indice": round((num + poids) / (attendu + poids), 3),
                    "matchs": n,
                }
            return rendu

        style = bloc("style:")
        evenements = {k: v for k, v in bloc("").items() if not k.startswith("style:")}

        systemes = Counter(l["systeme"] for l in matchs if l["systeme"])
        equipes: dict[str, dict[str, Any]] = {}
        for ligne in matchs:
            e = equipes.setdefault(ligne["equipe"], {"equipe": ligne["equipe"], "matchs": 0,
                                                      "du": ligne["date"], "au": ligne["date"]})
            e["matchs"] += 1
            e["au"] = ligne["date"]
        derniere = matchs[-1]
        # Anciennete : premier match de la serie en cours avec l'equipe actuelle.
        arrivee = derniere
        for ligne in reversed(matchs):
            if ligne["equipe"] != derniere["equipe"]:
                break
            arrivee = ligne
        principal, n_principal = systemes.most_common(1)[0] if systemes else ("", 0)
        fiches[coach_id] = {
            "id": coach_id,
            "nom": Counter(l["nom"] for l in matchs if l["nom"]).most_common(1)[0][0],
            "matchs": len(matchs),
            "equipe_actuelle": derniere["equipe"],
            "dernier_match": derniere["date"],
            "en_poste": {
                "depuis": arrivee["date"],
                "matchs": sum(1 for l in matchs if l["equipe"] == derniere["equipe"]
                              and l["date"] >= arrivee["date"]),
                "predecesseur": predecesseurs.get((derniere["equipe"], coach_id), ""),
            },
            "equipes": sorted(equipes.values(), key=lambda e: e["au"], reverse=True),
            "competitions": dict(Counter(l["competition"] for l in matchs).most_common()),
            "systemes": {
                "principal": principal,
                "part_principal": round(n_principal / sum(systemes.values()), 3) if systemes else 0.0,
                "releves": sum(systemes.values()),
                "repartition": dict(systemes.most_common()),
            },
            "style": style,
            "evenements": evenements,
        }

    # Seuils des etiquettes : quintiles des entraineurs etablis.
    etablis = [f for f in fiches.values() if f["matchs"] >= MATCHS_ETIQUETTE]
    valeurs: dict[str, list[float]] = defaultdict(list)
    for fiche in etablis:
        for nom, v in fiche["style"].items():
            valeurs["style:" + nom].append(v["indice"])
        for nom, v in fiche["evenements"].items():
            valeurs[nom].append(v["indice"])
    seuils = {}
    for nom, liste in valeurs.items():
        if len(liste) >= 20:
            liste.sort()
            seuils[nom] = (liste[int(0.2 * (len(liste) - 1))], liste[int(0.8 * (len(liste) - 1))])
    for fiche in fiches.values():
        fiche["etiquettes"] = (
            _etiquettes(fiche["style"], fiche["evenements"], seuils)
            if fiche["matchs"] >= MATCHS_ETIQUETTE else []
        )
    return fiches


# ---------------------------------------------------------------------------
# Stockage
# ---------------------------------------------------------------------------


def connect() -> sqlite3.Connection:
    chemins.DONNEES.mkdir(parents=True, exist_ok=True)
    connexion = sqlite3.connect(DB_PATH)
    connexion.row_factory = sqlite3.Row
    return connexion


def init() -> None:
    with connect() as connexion:
        connexion.executescript(SCHEMA)


def enregistrer(fiches: dict[str, dict[str, Any]], lignes: list[dict[str, Any]]) -> int:
    """Remplace le contenu de la base par ces fiches et ces lignes."""
    init()
    maintenant = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as connexion:
        connexion.execute("DELETE FROM entraineurs")
        connexion.execute("DELETE FROM matchs_entraineurs")
        connexion.executemany(
            "INSERT INTO entraineurs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (f["id"], f["nom"], f["matchs"], f["equipe_actuelle"], f["dernier_match"],
                 f["systemes"]["principal"], json.dumps(f["etiquettes"], ensure_ascii=False),
                 json.dumps(f, ensure_ascii=False), maintenant)
                for f in fiches.values()
            ],
        )
        connexion.executemany(
            "INSERT OR REPLACE INTO matchs_entraineurs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (l["match_id"], l["cote"], l["entraineur_id"], l["equipe"], l["adversaire"],
                 l["date"], l["competition"], l["systeme"], json.dumps(l["mesures"]))
                for l in lignes
            ],
        )
    return len(fiches)


def tous(minimum_matchs: int = 0) -> list[dict[str, Any]]:
    """Toutes les fiches, les plus vues d'abord."""
    init()
    with connect() as connexion:
        rangs = connexion.execute(
            "SELECT fiche FROM entraineurs WHERE matchs >= ? ORDER BY matchs DESC, nom",
            (minimum_matchs,),
        ).fetchall()
    return [json.loads(r["fiche"]) for r in rangs]


def _plat(texte: str) -> str:
    texte = unicodedata.normalize("NFKD", texte or "")
    return "".join(c for c in texte if not unicodedata.combining(c)).lower().strip()


def trouver(nom_ou_id: str) -> list[dict[str, Any]]:
    """Fiches dont l'identifiant, le nom ou l'equipe actuelle correspond."""
    cherche = _plat(nom_ou_id)
    return [
        f for f in tous()
        if f["id"] == nom_ou_id or cherche in _plat(f["nom"]) or cherche in _plat(f["equipe_actuelle"])
    ]


def fiche(coach_id: str) -> dict[str, Any] | None:
    """La fiche d'un entraineur par son identifiant, ou None (base absente comprise)."""
    if not coach_id or not DB_PATH.exists():
        return None
    try:
        with connect() as connexion:
            rang = connexion.execute(
                "SELECT fiche FROM entraineurs WHERE id = ?", (coach_id,)
            ).fetchone()
    except sqlite3.Error:
        return None
    return json.loads(rang["fiche"]) if rang else None


#: Indices repris dans le resume d'une fiche de match : ceux dont la mesure
#: demi-echantillon montre qu'ils sont stables d'un match a l'autre (r >= 0.45).
RESUME_EVENEMENTS = ("pour:corners", "pour:fautes", "pour:tirs_cadres", "pour:xg", "pour:buts")
RESUME_STYLE = ("possession", "part_jeu_long", "ppda", "hors_jeux_provoques", "rotation", "points")


def resume(coach_id: str, nom: str = "", equipe: str = "") -> dict[str, Any] | None:
    """Ce qu'une fiche de match affiche d'un entraineur : lecture, pas prevision.

    Mesure faite (walk-forward, 3 275 matchs) : ni le profil de l'entraineur,
    ni l'historique de l'equipe sous l'entraineur actuel, ni le style de
    l'entraineur adverse, ni la rotation n'ameliorent la prevision des corners,
    fautes, cartons, tirs ou buts au-dela de l'historique de l'equipe. Le resume
    sert donc a LIRE un match, et ne deplace aucune probabilite.
    """
    f = fiche(coach_id)
    if not f:
        return {"id": coach_id, "nom": nom, "connu": False} if (coach_id or nom) else None
    en_poste = f.get("en_poste") or {}
    nouveau = bool(equipe) and equipe != f["equipe_actuelle"]
    return {
        "id": coach_id,
        "nom": f["nom"],
        "connu": True,
        "matchs_archives": f["matchs"],
        "systeme_principal": f["systemes"]["principal"],
        "etiquettes": f["etiquettes"],
        # Un entraineur dont la derniere equipe connue n'est pas celle du match
        # vient d'arriver : l'historique de l'equipe est celui d'un autre.
        "nouveau_dans_l_equipe": nouveau,
        "en_poste": None if nouveau else en_poste,
        "style": {k: {"valeur": v["valeur"], "indice": v["indice"]}
                  for k, v in f["style"].items() if k in RESUME_STYLE},
        "evenements": {k: {"valeur": v["valeur"], "indice": v["indice"]}
                       for k, v in f["evenements"].items() if k in RESUME_EVENEMENTS},
    }


# ---------------------------------------------------------------------------
# Affichage et export
# ---------------------------------------------------------------------------


def _fleche(indice: float) -> str:
    if indice >= 1.15:
        return "++" if indice >= 1.30 else "+"
    if indice <= 0.87:
        return "--" if indice <= 0.77 else "-"
    return "="


def formater(fiche: dict[str, Any]) -> str:
    lignes = [
        "%s  (%d matchs, dernier le %s avec %s)" % (
            fiche["nom"], fiche["matchs"], fiche["dernier_match"], fiche["equipe_actuelle"]),
        "  En poste     : depuis le %s (%d matchs)%s" % (
            fiche["en_poste"]["depuis"], fiche["en_poste"]["matchs"],
            (", succede a " + fiche["en_poste"]["predecesseur"]) if fiche["en_poste"]["predecesseur"] else ""),
        "  Equipes      : " + ", ".join(
            "%s (%d, %s -> %s)" % (e["equipe"], e["matchs"], e["du"], e["au"]) for e in fiche["equipes"]),
        "  Systemes     : " + (", ".join(
            "%s x%d" % (s, n) for s, n in fiche["systemes"]["repartition"].items()) or "non releves"),
        "  Etiquettes   : " + (", ".join(fiche["etiquettes"]) or
                               ("aucune" if fiche["matchs"] >= MATCHS_ETIQUETTE
                                else "moins de %d matchs" % MATCHS_ETIQUETTE)),
        "",
        "  Style                                   valeur   competition  indice",
    ]
    for nom, libelle in LIBELLES_STYLE.items():
        v = fiche["style"].get(nom)
        if v:
            lignes.append("    %-36s %8.2f %10.2f %9.2f %s" % (
                libelle, v["valeur"], v["reference"], v["indice"], _fleche(v["indice"])))
    lignes += ["", "  Evenements par match                    pour     contre   total du match"]
    for cle in EVENEMENTS:
        pour, contre = fiche["evenements"].get("pour:" + cle), fiche["evenements"].get("contre:" + cle)
        total = fiche["evenements"].get("total:" + cle)
        if not pour and not contre:
            continue
        def cellule(v: dict[str, float] | None) -> str:
            return "%5.2f (x%.2f)" % (v["valeur"], v["indice"]) if v else " " * 13
        lignes.append("    %-36s %s %s %s" % (LIBELLES[cle], cellule(pour), cellule(contre), cellule(total)))
    lignes.append("  (xN.NN : indice, 1.00 = moyenne de la competition, meme cote du terrain)")
    return "\n".join(lignes)


def exporter() -> tuple[str, str]:
    """Ecrit donnees/exports/entraineurs.json (complet) et .csv (une ligne par entraineur)."""
    fiches = tous()
    chemins.EXPORTS.mkdir(parents=True, exist_ok=True)
    chemin_json = chemins.EXPORTS / "entraineurs.json"
    chemin_json.write_text(json.dumps(fiches, ensure_ascii=False, indent=1), encoding="utf-8")
    chemin_csv = chemins.EXPORTS / "entraineurs.csv"
    colonnes_style = list(LIBELLES_STYLE)
    colonnes_evt = ["%s:%s" % (s, e) for s in ("pour", "contre") for e in EVENEMENTS] + \
                   ["total:%s" % e for e in ("buts", "corners", "cartons_jaunes", "fautes")]
    with chemin_csv.open("w", newline="", encoding="utf-8-sig") as sortie:
        ecrivain = csv.writer(sortie, delimiter=";")
        ecrivain.writerow(["id", "nom", "matchs", "equipe_actuelle", "dernier_match",
                           "systeme_principal", "part_systeme_principal", "etiquettes"]
                          + ["style_" + c for c in colonnes_style]
                          + ["indice_" + c.replace(":", "_") for c in colonnes_evt])
        for f in fiches:
            ecrivain.writerow(
                [f["id"], f["nom"], f["matchs"], f["equipe_actuelle"], f["dernier_match"],
                 f["systemes"]["principal"], f["systemes"]["part_principal"], ", ".join(f["etiquettes"])]
                + [(f["style"].get(c) or {}).get("valeur", "") for c in colonnes_style]
                + [(f["evenements"].get(c) or {}).get("indice", "") for c in colonnes_evt]
            )
    return str(chemin_json), str(chemin_csv)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("recherche", nargs="?", help="nom, identifiant ou equipe actuelle")
    parser.add_argument("--construire", action="store_true", help="(re)construit la base")
    parser.add_argument("--liste", action="store_true", help="liste les entraineurs")
    parser.add_argument("--min", type=int, default=MATCHS_ETIQUETTE, help="matchs minimum pour --liste")
    parser.add_argument("--etiquette", help="entraineurs portant cette etiquette")
    parser.add_argument("--export", action="store_true", help="exporte en JSON et CSV")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    if args.construire:
        lignes = lignes_des_feuilles()
        fiches = construire(lignes)
        n = enregistrer(fiches, lignes)
        etablis = sum(1 for f in fiches.values() if f["matchs"] >= MATCHS_ETIQUETTE)
        print("%d entraineurs (%d avec au moins %d matchs), %d lignes de match -> %s"
              % (n, etablis, MATCHS_ETIQUETTE, len(lignes), DB_PATH))
    if args.export:
        print("export : %s, %s" % exporter())
    if args.etiquette:
        for f in tous(MATCHS_ETIQUETTE):
            if args.etiquette in f["etiquettes"]:
                print("%-24s %3d matchs  %-24s %s" % (f["nom"], f["matchs"], f["equipe_actuelle"],
                                                       ", ".join(f["etiquettes"])))
    if args.liste:
        for f in tous(args.min):
            print("%-24s %3d matchs  %-24s %-10s %s" % (
                f["nom"], f["matchs"], f["equipe_actuelle"], f["systemes"]["principal"],
                ", ".join(f["etiquettes"])))
    if args.recherche:
        trouves = trouver(args.recherche)
        if not trouves:
            print("Aucun entraineur ne correspond a « %s »." % args.recherche)
        for f in trouves[:5]:
            print(formater(f))
            print()
    if not (args.construire or args.export or args.etiquette or args.liste or args.recherche):
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
