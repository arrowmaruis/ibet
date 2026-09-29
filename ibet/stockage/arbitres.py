"""Base des arbitres : qui siffle, combien il sanctionne, et sur quel echantillon.

Le modele des cartons (`modeles/cartons.py`) lit le profil de l'arbitre designe
dans les feuilles de match Flashscore (`store.feuilles`). Ces feuilles ne
remontent pas loin pour la plupart des arbitres : neuf matchs connus en
mediane, trop peu pour distinguer un arbitre severe d'un arbitre coulant.

Ce module apporte l'HISTORIQUE : les tableaux d'arbitres de worldfootball.net,
une ligne par arbitre, par competition et par SAISON -- matchs, jaunes,
second jaune, rouges, penalties. Il sert d'a priori au profil tire des
feuilles (`Discipline.arbitre(..., historique=...)`), qui l'affine ensuite.

**Par saison, et pas en cumul.** Un profil cumule sur deux saisons contient les
matchs qu'on voudrait prevoir : le mesurer sur eux serait se juger avec la
reponse sous les yeux. Garder la saison permet de n'employer, pour un match,
que les saisons qui le PRECEDENT.

**La saison en cours n'y est pas** : la source ne publie ses tableaux qu'une
fois la saison avancee (404 sur 2026-2027 en septembre 2026). Les arbitres de
la saison en cours viennent des feuilles de match Flashscore
(`python -m ibet rattraper-feuilles`), que le modele lit deja.

**Des noms a rapprocher.** La source ecrit « Jesus Gil Manzano / Spain »,
Flashscore « Manzano J. / Esp » ou « Gil Manzano J. ». `historique_de` retrouve
le premier a partir du second : nom de famille contenu dans le nom complet,
meme initiale, pays compatible. Un cas ambigu est ecarte plutot que devine --
un historique attribue au mauvais arbitre ferait plus de mal que pas
d'historique du tout.

Usage :
    python -m ibet arbitres            # releve et enregistre (pages en cache 30 j)
    python -m ibet arbitres --sans-cache
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from ibet import chemins
from ibet.sources import api_client, cache

DB_PATH = chemins.BASE

#: Ancienne base separee, avant que les arbitres ne rejoignent `ibet.db`.
ANCIENNE_BASE = chemins.DONNEES / "arbitres.db"

WORLDFOOTBALL = "https://www.worldfootball.net/referees/%s/"

#: Trente jours : un tableau d'arbitres bouge d'une journee de championnat a
#: l'autre, pas d'une heure a l'autre. Le cache evite de redemander la meme page
#: a chaque construction.
TABLEAU_TTL = 30 * 24 * 3600

#: Competitions couvertes, par (cle interne, slug de la source, libelle).
#:
#: Les slugs ont ete releves un par un -- « bundesliga » n'a pas de prefixe de
#: pays quand « eng-premier-league » en a -- et verifies contre la source en
#: septembre 2026. Les championnats absents (Belgique, Bresil, MLS, Scandinavie)
#: n'ont pas de tableau d'arbitres sur worldfootball.
COMPETITIONS = (
    ("ldc", "champions-league-%s", "Ligue des Champions"),
    ("europa", "europa-league-%s", "Ligue Europa"),
    ("conference", "conference-league-%s", "Ligue Conference"),
    ("angleterre", "eng-premier-league-%s", "Premier League"),
    ("angleterre-2", "eng-championship-%s", "Championship"),
    ("angleterre-3", "eng-league-one-%s", "League One"),
    ("espagne", "esp-primera-division-%s", "LaLiga"),
    ("espagne-2", "esp-segunda-division-%s", "LaLiga2"),
    ("italie", "ita-serie-a-%s", "Serie A"),
    ("italie-2", "ita-serie-b-%s", "Serie B"),
    ("allemagne", "bundesliga-%s", "Bundesliga"),
    ("allemagne-2", "2-bundesliga-%s", "2. Bundesliga"),
    ("france", "fra-ligue-1-%s", "Ligue 1"),
    ("france-2", "fra-ligue-2-%s", "Ligue 2"),
    ("pays-bas", "ned-eredivisie-%s", "Eredivisie"),
    ("pays-bas-2", "ned-eerste-divisie-%s", "Eerste Divisie"),
    ("portugal", "por-primeira-liga-%s", "Liga Portugal"),
    ("turquie", "tur-sueperlig-%s", "Super Lig"),
    ("ecosse", "sco-premiership-%s", "Premiership"),
    ("autriche", "aut-bundesliga-%s", "Bundesliga (Autriche)"),
    ("suisse", "sui-super-league-%s", "Super League"),
    ("grece", "gre-super-league-%s", "Super League (Grece)"),
    ("danemark", "den-superliga-%s", "Superliga"),
    ("pologne", "pol-ekstraklasa-%s", "Ekstraklasa"),
    ("russie", "rus-premier-liga-%s", "Premier Liga"),
)

#: Saisons demandees, la plus recente d'abord. Trois saisons donnent a un
#: arbitre d'elite une cinquantaine de matchs ; au-dela, son style a pu changer.
SAISONS = ("2025-2026", "2024-2025", "2023-2024")

SCHEMA = """
CREATE TABLE IF NOT EXISTS arbitres_saisons (
    cle            TEXT NOT NULL,
    nom            TEXT NOT NULL,
    pays           TEXT NOT NULL DEFAULT '',
    competition    TEXT NOT NULL,
    saison         TEXT NOT NULL,
    matchs         REAL NOT NULL,
    jaunes         REAL NOT NULL,
    jaune_rouge    REAL NOT NULL DEFAULT 0,
    rouges         REAL NOT NULL DEFAULT 0,
    penalties      REAL NOT NULL DEFAULT 0,
    jaunes_moyens  REAL,
    releve_le      TEXT NOT NULL,
    PRIMARY KEY (cle, competition, saison)
);

CREATE INDEX IF NOT EXISTS idx_arbitres_saisons_nom ON arbitres_saisons(nom);
"""


def connect() -> sqlite3.Connection:
    connexion = sqlite3.connect(DB_PATH)
    connexion.row_factory = sqlite3.Row
    return connexion


def init() -> None:
    """Cree les tables. Idempotent."""
    with connect() as connexion:
        connexion.executescript(SCHEMA)


def _sans_accents(texte: str) -> str:
    texte = unicodedata.normalize("NFKD", texte or "")
    texte = "".join(c for c in texte if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z ]+", " ", texte).split())


def cle_arbitre(nom: str, pays: str = "") -> str:
    """Cle stable d'un arbitre : nom sans accents, minuscules, plus le pays.

    Les accents sont retires parce que la source les ecrit et que les autres
    n'en mettent pas toujours -- « Slavko Vincic » et « Slavko Vinčič » sont le
    meme homme, et les compter deux fois diviserait son echantillon.

    Le pays fait partie de la cle : deux arbitres peuvent porter le meme nom, et
    fondre leurs statistiques en donnerait un troisieme qui n'existe pas.
    """
    return "%s|%s" % (_sans_accents(nom), (pays or "").strip().lower())


#: Une ligne de tableau HTML et ses cellules.
_LIGNE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELLULE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_BALISE = re.compile(r"<[^>]+>")


def _nombre(texte: str) -> float:
    """Un entier de cellule, ou 0. Une cellule vide n'est pas une erreur."""
    try:
        return float((texte or "").strip() or 0)
    except ValueError:
        return 0.0


def lire_tableau(slug: str, use_cache: bool = True) -> list[dict[str, Any]]:
    """Une page d'arbitres, lue telle que la source la rend.

    Colonnes de la source, dans cet ordre : rang, drapeau, nom, pays, matchs,
    jaunes, jaune-rouge, rouges, penalties. Le rang n'est ecrit que sur la
    premiere ligne d'un groupe a egalite -- on ne s'en sert pas.

    Rend une liste vide si la page n'existe pas (404 sur une competition sans
    tableau, ou une saison pas encore publiee). Ne leve pas : construire une
    base ne doit pas echouer parce qu'une page sur soixante-quinze manque.
    """
    marque = "v1|arbitres|%s" % slug
    if use_cache:
        garde = cache.get(marque, TABLEAU_TTL)
        if garde is not None:
            return garde

    try:
        reponse = api_client._http_get(
            WORLDFOOTBALL % slug, api_client._BROWSER_HEADERS, {}
        )
    except api_client.ApiError:
        return []
    if reponse.status_code != 200:
        return []

    # Le tri se fait sur la FORME de la ligne, pas sur les balises qui
    # l'entourent. Deux tentatives precedentes ont echoue : accepter toute ligne
    # de huit cellules faisait entrer le classement du championnat (« Arsenal FC »
    # avec « Arsenal » pour pays, et la moyenne generale tombait a 1.82 carton
    # quand le reel est de 4) ; se limiter au tableau portant « Yellow-Red » dans
    # ses en-tetes perdait quatre competitions sur onze, la regex non gourmande
    # tronquant les tableaux imbriques.
    #
    # Une ligne d'arbitre a EXACTEMENT neuf cellules -- rang, drapeau, nom, pays,
    # matchs, jaunes, jaune-rouge, rouges, penalties -- et ses cinq dernieres
    # sont des nombres. Une ligne de classement en a davantage.
    lignes: list[dict[str, Any]] = []
    for brut in _LIGNE.findall(reponse.text):
        cellules = [_BALISE.sub("", c).strip() for c in _CELLULE.findall(brut)]
        if len(cellules) != 9 or not cellules[2]:
            continue
        chiffres = [c.strip() for c in cellules[4:9]]
        if not all(c == "" or c.isdigit() for c in chiffres):
            continue
        if any(caractere.isdigit() for caractere in cellules[3]):
            continue
        lignes.append(
            {
                "nom": cellules[2],
                "pays": cellules[3],
                "matchs": _nombre(cellules[4]),
                "jaunes": _nombre(cellules[5]),
                "jaune_rouge": _nombre(cellules[6]),
                "rouges": _nombre(cellules[7]),
                "penalties": _nombre(cellules[8]),
            }
        )
    if lignes:
        cache.set(marque, lignes)
    return lignes


def construire(
    saisons: Iterable[str] = SAISONS,
    competitions: Iterable[tuple[str, str, str]] = COMPETITIONS,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Toutes les pages demandees, une ligne par arbitre, competition et saison.

    Chaque ligne porte la moyenne de jaunes par match de SA competition et de SA
    saison (`jaunes_moyens`) : un arbitre a 4.5 jaunes par match est severe en
    Premier League et ordinaire en Serie A, et c'est l'ecart a cette moyenne
    -- pas le chiffre brut -- qui dit son style.
    """
    lignes: list[dict[str, Any]] = []
    manquantes: list[str] = []
    for cle_comp, gabarit, libelle in competitions:
        for saison in saisons:
            slug = gabarit % saison
            tableau = lire_tableau(slug, use_cache)
            if not tableau:
                manquantes.append(slug)
                continue
            matchs = sum(l["matchs"] for l in tableau)
            moyenne = sum(l["jaunes"] for l in tableau) / matchs if matchs else None
            for l in tableau:
                lignes.append(dict(
                    l, cle=cle_arbitre(l["nom"], l["pays"]), competition=cle_comp,
                    libelle=libelle, saison=saison, jaunes_moyens=moyenne,
                ))
    return {
        "lignes": lignes,
        "tableaux_lus": len({(l["competition"], l["saison"]) for l in lignes}),
        "tableaux_absents": manquantes,
        "releve_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def enregistrer(base: dict[str, Any]) -> int:
    """Ecrit les lignes. Une ligne deja connue (arbitre, competition, saison)
    est REMPLACEE : une saison en cours se complete d'une releve a l'autre."""
    init()
    with connect() as connexion:
        connexion.executemany(
            "INSERT INTO arbitres_saisons (cle, nom, pays, competition, saison, matchs,"
            " jaunes, jaune_rouge, rouges, penalties, jaunes_moyens, releve_le)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(cle, competition, saison) DO UPDATE SET"
            " nom = excluded.nom, pays = excluded.pays, matchs = excluded.matchs,"
            " jaunes = excluded.jaunes, jaune_rouge = excluded.jaune_rouge,"
            " rouges = excluded.rouges, penalties = excluded.penalties,"
            " jaunes_moyens = excluded.jaunes_moyens, releve_le = excluded.releve_le",
            [
                (l["cle"], l["nom"], l["pays"], l["competition"], l["saison"],
                 l["matchs"], l["jaunes"], l["jaune_rouge"], l["rouges"],
                 l["penalties"], l["jaunes_moyens"], base["releve_le"])
                for l in base["lignes"]
            ],
        )
    _INDEX.clear()
    return len(base["lignes"])


def lignes(cle: str | None = None) -> list[dict[str, Any]]:
    """Les lignes par saison, d'un arbitre ou de tous."""
    init()
    with connect() as connexion:
        if cle:
            rangs = connexion.execute(
                "SELECT * FROM arbitres_saisons WHERE cle = ?", (cle,)
            ).fetchall()
        else:
            rangs = connexion.execute("SELECT * FROM arbitres_saisons").fetchall()
    return [dict(r) for r in rangs]


def tous(minimum_matchs: float = 0.0) -> list[dict[str, Any]]:
    """Profils cumules, toutes saisons, les arbitres les plus sollicites d'abord.

    `minimum_matchs` ecarte ceux dont l'echantillon ne dit rien : sous une
    dizaine de matchs, un ratio de cartons tient surtout du hasard.
    """
    profils: dict[str, dict[str, Any]] = {}
    for l in lignes():
        p = profils.setdefault(l["cle"], {
            "cle": l["cle"], "nom": l["nom"], "pays": l["pays"], "matchs": 0.0,
            "jaunes": 0.0, "jaune_rouge": 0.0, "rouges": 0.0, "penalties": 0.0,
            "attendus": 0.0, "saisons": set(), "competitions": set(),
        })
        for champ in ("matchs", "jaunes", "jaune_rouge", "rouges", "penalties"):
            p[champ] += l[champ]
        p["attendus"] += l["matchs"] * (l["jaunes_moyens"] or 0.0)
        p["saisons"].add(l["saison"])
        p["competitions"].add(l["competition"])
    rendu = []
    for p in profils.values():
        if p["matchs"] < minimum_matchs or p["matchs"] <= 0:
            continue
        p["saisons"] = sorted(p["saisons"], reverse=True)
        p["competitions"] = sorted(p["competitions"])
        p["jaunes_par_match"] = round(p["jaunes"] / p["matchs"], 3)
        # Jaunes donnes / jaunes qu'aurait donnes un arbitre moyen de ses
        # competitions : 1.2 = 20 % de plus que la moyenne.
        p["rapport"] = round(p["jaunes"] / p["attendus"], 3) if p["attendus"] else None
        p["penalties_par_match"] = round(p["penalties"] / p["matchs"], 3)
        p["rouges_par_match"] = round((p["rouges"] + p["jaune_rouge"]) / p["matchs"], 3)
        rendu.append(p)
    rendu.sort(key=lambda p: p["matchs"], reverse=True)
    return rendu


# ---------------------------------------------------------------------------
# Rapprochement avec les noms Flashscore
# ---------------------------------------------------------------------------

#: Pays de la source -> code pays des feuilles Flashscore.
#:
#: Le pays doit CONCORDER des que la feuille en donne un. Sans cette regle, un
#: « Pinheiro J. » bresilien heritait du profil du Portugais Joao Pinheiro, un
#: « Ortiz M. » mexicain de celui de l'Espagnol Miguel Ortiz Arias : la source
#: ne couvre que des arbitres europeens, et un code hors de cette table (Bra,
#: Mex, Chi...) designe donc un autre homme. Les codes marques d'un asterisque
#: dans le commentaire n'ont pas encore ete vus dans les feuilles : ce sont ceux
#: de la FIFA, a corriger si Flashscore en emploie d'autres.
CODES_PAYS = {
    "albania": "alb",  # *
    "armenia": "arm",  # *
    "australia": "aus", "austria": "aut", "azerbaijan": "aze", "belgium": "bel",
    "bosnia herzegovina": "bih", "bulgaria": "bul", "croatia": "cro",
    "cyprus": "cyp",  # *
    "czech republic": "cze",
    "denmark": "den", "estonia": "est", "finland": "fin", "georgia": "geo",  # *
    "england": "eng", "france": "fra", "germany": "ger", "greece": "gre",
    "hungary": "hun",
    "iceland": "isl", "israel": "isr", "kosovo": "kos",  # *
    "ireland": "irl", "italy": "ita", "kazakhstan": "kaz", "latvia": "lat",
    "lithuania": "ltu", "malta": "mlt", "montenegro": "mne", "netherlands": "ned",
    "north macedonia": "mkd", "northern ireland": "nir",  # *
    "norway": "nor", "poland": "pol", "portugal": "por", "romania": "rou",
    "russia": "rus", "scotland": "sco", "serbia": "srb", "slovakia": "svk",
    "slovenia": "slo", "spain": "esp", "sweden": "swe", "switzerland": "sui",
    "turkey": "tur", "usa": "usa", "ukraine": "ukr", "wales": "wal",
}

#: Titres qui precedent parfois le prenom dans la source (« Dr. Matthias
#: Jollenbeck ») et qu'il ne faut pas prendre pour une initiale.
_TITRES = {"dr", "prof"}


def _decouper_flashscore(nom: str) -> tuple[tuple[str, ...], str]:
    """« Gonzalez Esteban J. A. » -> (("gonzalez", "esteban"), "j")."""
    mots = (nom or "").replace(".", " ").split()
    nom_famille = [m for m in mots if len(m) > 1]
    initiales = [m for m in mots if len(m) == 1]
    return tuple(_sans_accents(" ".join(nom_famille)).split()), (
        _sans_accents(initiales[0]) if initiales else ""
    )


class _Index:
    """Lignes par arbitre, et les cles qui permettent de les retrouver."""

    def __init__(self) -> None:
        self.par_cle: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.pays: dict[str, str] = {}
        # (initiale, suite de mots du nom de famille) -> cles candidates
        self.acces: dict[tuple[str, tuple[str, ...]], set[str]] = defaultdict(set)
        self.premier_nom: dict[str, str] = {}
        for l in lignes():
            self.par_cle[l["cle"]].append(l)
            if l["cle"] in self.pays:
                continue
            self.pays[l["cle"]] = CODES_PAYS.get(_sans_accents(l["pays"]), "")
            mots = [m for m in _sans_accents(l["nom"]).split() if m not in _TITRES]
            if len(mots) < 2:
                continue
            initiale = mots[0][0]
            # Toute suite contigue de mots APRES le premier prenom : Flashscore
            # garde tantot le nom complet (« Gil Manzano »), tantot le dernier
            # (« Manzano »), tantot le premier (« Munuera » pour Munuera Montero).
            reste = mots[1:]
            for debut in range(len(reste)):
                for fin in range(debut + 1, len(reste) + 1):
                    self.acces[(initiale, tuple(reste[debut:fin]))].add(l["cle"])
            # Le PREMIER nom de famille, a part : c'est celui que Flashscore
            # garde quand il n'en garde qu'un (« Munuera J. » pour Jose Munuera
            # Montero), ce qui departage deux arbitres qui partagent un nom
            # (Juan Martinez Munuera).
            self.premier_nom[l["cle"]] = reste[0]

    def retrouver(self, nom: str, pays: str = "") -> str | None:
        nom_famille, initiale = _decouper_flashscore(nom)
        if not nom_famille or not initiale:
            return None
        candidats = self.acces.get((initiale, nom_famille), set())
        code = (pays or "").strip().lower()
        if code:
            candidats = {c for c in candidats if self.pays.get(c) == code}
        if len(candidats) > 1:
            candidats = {c for c in candidats if self.premier_nom.get(c) == nom_famille[0]}
        return next(iter(candidats)) if len(candidats) == 1 else None


_INDEX: dict[str, _Index] = {}


def _index() -> _Index:
    if "index" not in _INDEX:
        _INDEX["index"] = _Index()
    return _INDEX["index"]


def historique_de(nom: str, pays: str = "") -> list[dict[str, Any]]:
    """Lignes par saison de l'arbitre Flashscore `nom` / `pays`, ou [].

    Chaque ligne : saison, competition, matchs, jaunes, `attendus` (jaunes
    qu'aurait donnes un arbitre moyen de cette competition et de cette saison).
    Une liste vide quand l'arbitre n'est pas retrouve, ou pas sans ambiguite.
    """
    try:
        index = _index()
    except sqlite3.Error:
        return []
    cle = index.retrouver(nom, pays)
    if not cle:
        return []
    return [
        {"saison": l["saison"], "competition": l["competition"], "matchs": l["matchs"],
         "jaunes": l["jaunes"], "attendus": l["matchs"] * (l["jaunes_moyens"] or 0.0),
         "source": "worldfootball", "nom_source": l["nom"]}
        for l in index.par_cle[cle] if l["jaunes_moyens"]
    ]


def migrer_ancienne_base() -> int:
    """Reprend les observations de fautes de l'ancienne `arbitres.db`.

    Les profils cumules de l'ancienne base ne sont pas repris : ils sont
    reconstruits par saison depuis les pages en cache. Rend le nombre
    d'observations reprises ; 0 si l'ancienne base n'existe plus.
    """
    if not ANCIENNE_BASE.exists():
        return 0
    init_matchs()
    ancienne = sqlite3.connect(ANCIENNE_BASE)
    try:
        rangs = ancienne.execute("SELECT * FROM matchs_arbitres").fetchall()
    except sqlite3.Error:
        rangs = []
    finally:
        ancienne.close()
    with connect() as connexion:
        connexion.executemany(
            "INSERT OR IGNORE INTO matchs_arbitres (match_id, cle, arbitre, competition,"
            " joue_le, fautes, jaunes, rouges, releve_le) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rangs,
        )
    return len(rangs)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m ibet arbitres",
        description="Releve les tableaux d'arbitres de worldfootball.net (%d competitions,"
        " saisons %s) et les enregistre dans donnees/ibet.db."
        % (len(COMPETITIONS), ", ".join(SAISONS)),
    )
    parser.add_argument("--sans-cache", action="store_true",
                        help="Redemande les pages meme si elles sont en cache (30 jours)")
    args = parser.parse_args(argv)

    base = construire(use_cache=not args.sans_cache)
    n = enregistrer(base)
    reprises = migrer_ancienne_base()
    profils = tous()
    print("%d lignes (arbitre x competition x saison) depuis %d pages ; %d absentes"
          % (n, base["tableaux_lus"], len(base["tableaux_absents"])))
    for slug in base["tableaux_absents"]:
        print("  absente : %s" % slug)
    print("%d arbitres, dont %d vus au moins 20 fois"
          % (len(profils), sum(p["matchs"] >= 20 for p in profils)))
    if reprises:
        print("%d observations de fautes reprises de l'ancienne arbitres.db" % reprises)
    return 0


# ---------------------------------------------------------------------------
# Enrichissement incremental : les fautes, match par match
# ---------------------------------------------------------------------------
#
# Les cartons disent si un arbitre sanctionne ; ils ne disent pas s'il LAISSE
# JOUER. Deux arbitres a quatre cartons par match peuvent siffler l'un dix-huit
# fautes, l'autre trente : le premier laisse courir, le second coupe tout, et
# cela change le match bien plus que la couleur des cartons.
#
# Aucune source ne publie les fautes PAR ARBITRE. Flashscore les publie par
# MATCH, et donne aussi l'arbitre de ce match (`feuille_de_match`). Le lien
# existe donc, une rencontre a la fois.
#
# D'ou le choix de l'incremental plutot que du massif. Reconstituer les fautes
# de 241 arbitres sur quarante matchs chacun demanderait des milliers de
# requetes ; n'accumuler que sur les matchs que le projet TOUCHE DEJA n'en
# demande aucune de plus quand leurs statistiques sont en cache. La base se
# remplit au rythme de l'usage, ce qui est lent -- et c'est acceptable, parce que
# les cartons sont deja la pour attendre.
#
# Une observation est enregistree PAR MATCH et non cumulee a la volee :
# l'identifiant du match est la cle primaire, donc repasser deux fois sur la
# meme rencontre ne la compte pas deux fois. Un total cumule, lui, aurait
# double en silence au second passage.

SCHEMA_MATCHS = """
CREATE TABLE IF NOT EXISTS matchs_arbitres (
    match_id          TEXT PRIMARY KEY,
    cle               TEXT NOT NULL,
    arbitre           TEXT NOT NULL,
    competition       TEXT NOT NULL DEFAULT '',
    joue_le           TEXT NOT NULL DEFAULT '',
    fautes            REAL,
    jaunes            REAL,
    rouges            REAL,
    releve_le         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_matchs_arbitres_cle ON matchs_arbitres(cle);
"""


def init_matchs() -> None:
    with connect() as connexion:
        connexion.executescript(SCHEMA_MATCHS)


def _nombre_stat(valeur: Any) -> float | None:
    """Une statistique Flashscore en nombre, ou None si absente.

    Les fautes arrivent parfois en chaine (« 14 »), parfois absentes selon la
    competition. Rendre None plutot que zero est la difference entre « pas de
    faute sifflee » et « on ne sait pas », et confondre les deux fausserait la
    moyenne vers le bas.
    """
    if valeur is None:
        return None
    nombre = api_client.stat_number(valeur)
    return float(nombre) if nombre is not None else None


def observer(match: dict[str, Any], use_cache: bool = True) -> dict[str, Any] | None:
    """Releve l'arbitre et les fautes d'UN match. None si l'un des deux manque.

    Les deux sont exiges. Un match dont on connait l'arbitre mais pas les fautes
    n'apprend rien sur son style de sifflet ; l'inverse n'apprend rien du tout.

    Le match doit etre TERMINE : les fautes n'existent pas avant, et l'arbitre
    d'un match a venir n'est pas encore celui qui l'a arbitre -- une designation
    peut changer.
    """
    identifiant = match.get("match_id") or ""
    if not identifiant or match.get("statut") != api_client.FINISHED:
        return None

    try:
        feuille = api_client.feuille_de_match(identifiant, use_cache=use_cache)
    except api_client.ApiError:
        return None
    arbitre = (feuille.get("arbitre") or "").strip()
    if not arbitre:
        return None

    try:
        stats = api_client.get_stats(match, use_cache=use_cache)
    except api_client.ApiError:
        return None

    domicile = stats.get("domicile") or {}
    exterieur = stats.get("exterieur") or {}
    fautes_dom = _nombre_stat(domicile.get("fautes"))
    fautes_ext = _nombre_stat(exterieur.get("fautes"))
    if fautes_dom is None or fautes_ext is None:
        return None

    jaunes_dom = _nombre_stat(domicile.get("cartons_jaunes")) or 0.0
    jaunes_ext = _nombre_stat(exterieur.get("cartons_jaunes")) or 0.0
    rouges_dom = _nombre_stat(domicile.get("cartons_rouges")) or 0.0
    rouges_ext = _nombre_stat(exterieur.get("cartons_rouges")) or 0.0

    return {
        "match_id": identifiant,
        "cle": cle_arbitre(arbitre, feuille.get("arbitre_pays", "")),
        "arbitre": arbitre,
        "competition": "%s (%s)" % (
            match.get("championnat", ""), match.get("pays", "")
        ),
        "joue_le": match.get("date", ""),
        # Le TOTAL des deux equipes : c'est l'arbitre qui siffle, pas une equipe
        # qui commet. Separer les deux cotes n'aurait de sens que pour juger les
        # equipes, ce qui n'est pas la question ici.
        "fautes": fautes_dom + fautes_ext,
        "jaunes": jaunes_dom + jaunes_ext,
        "rouges": rouges_dom + rouges_ext,
    }


def enregistrer_observation(observation: dict[str, Any]) -> bool:
    """Ecrit l'observation d'un match. Rend False si elle y etait deja.

    `INSERT OR IGNORE` sur l'identifiant du match : repasser sur une rencontre
    deja relevee ne la compte pas deux fois. C'est ce qui rend l'accumulation
    rejouable autant de fois qu'on veut sans fausser la moyenne.
    """
    init_matchs()
    horodatage = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as connexion:
        curseur = connexion.execute(
            "INSERT OR IGNORE INTO matchs_arbitres"
            " (match_id, cle, arbitre, competition, joue_le, fautes, jaunes,"
            "  rouges, releve_le) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                observation["match_id"], observation["cle"], observation["arbitre"],
                observation["competition"], observation["joue_le"],
                observation["fautes"], observation["jaunes"], observation["rouges"],
                horodatage,
            ),
        )
    return curseur.rowcount > 0


def accumuler(
    matchs: Iterable[dict[str, Any]], use_cache: bool = True
) -> dict[str, Any]:
    """Passe sur une serie de matchs et retient ce qui est exploitable.

    Les causes d'ecart sont comptees separement plutot que confondues : un match
    sans arbitre publie est une limite de la source, un match sans fautes une
    competition que Flashscore ne detaille pas, et un match deja releve n'est pas
    un echec du tout. Les melanger rendrait le resultat illisible -- c'est la
    meme regle que les exclusions du banc d'essai.
    """
    causes: Counter = Counter()
    nouveaux = 0
    for match in matchs:
        if match.get("statut") != api_client.FINISHED:
            causes["pas encore joue"] += 1
            continue
        observation = observer(match, use_cache)
        if not observation:
            causes["arbitre ou fautes indisponibles"] += 1
            continue
        if enregistrer_observation(observation):
            nouveaux += 1
        else:
            causes["deja releve"] += 1
    return {"nouveaux": nouveaux, "causes": dict(causes)}


def fautes_par_arbitre(minimum_matchs: int = 1) -> dict[str, dict[str, Any]]:
    """Fautes sifflees par match, par arbitre, depuis les observations.

    `minimum_matchs` ecarte les arbitres dont l'echantillon ne dit rien. Sur deux
    matchs, dix-huit fautes ou trente relevent du hasard des rencontres : un
    derby se siffle autrement qu'un match de milieu de tableau.
    """
    init_matchs()
    with connect() as connexion:
        lignes = connexion.execute(
            "SELECT cle, arbitre, COUNT(*) AS matchs, AVG(fautes) AS fautes,"
            " AVG(jaunes) AS jaunes, AVG(rouges) AS rouges"
            " FROM matchs_arbitres GROUP BY cle HAVING COUNT(*) >= ?",
            (minimum_matchs,),
        ).fetchall()
    return {
        ligne["cle"]: {
            "arbitre": ligne["arbitre"],
            "matchs_observes": ligne["matchs"],
            "fautes_par_match": round(ligne["fautes"] or 0, 2),
            "jaunes_par_match": round(ligne["jaunes"] or 0, 2),
            "rouges_par_match": round(ligne["rouges"] or 0, 3),
            # Combien de fautes il faut pour qu'un carton sorte. Un arbitre qui
            # siffle beaucoup et sanctionne peu a un rapport eleve : il coupe le
            # jeu sans le durcir. C'est une autre facon de laisser jouer -- ou de
            # ne pas le faire -- que le seul compte de cartons ne montre pas.
            "fautes_par_carton": round(
                (ligne["fautes"] or 0) / (ligne["jaunes"] or 1), 2
            )
            if (ligne["jaunes"] or 0) > 0
            else None,
        }
        for ligne in lignes
    }

def accumuler_depuis_la_base(
    limite: int = 400, use_cache: bool = True
) -> dict[str, Any]:
    """Accumule sur les matchs des fiches DEJA emises et tranchees.

    C'est le sens de l'incremental : ces matchs ont deja ete interroges pour
    produire la fiche et la verifier, leurs statistiques sont donc en cache et la
    lecture ne coute rien de plus. Seule la feuille de match peut manquer -- une
    requete par rencontre, gardee trente jours, un match termine ne changeant
    plus d'arbitre.

    Les jours sont regroupes : le flux d'une journee se demande une fois et sert
    a tous les matchs de cette date, comme partout ailleurs dans le projet.
    """
    from ibet.stockage import store

    with store.connect() as connexion:
        lignes = connexion.execute(
            "SELECT match_id, coup_denvoi_local FROM predictions"
            " WHERE resultat_reel IS NOT NULL"
            " ORDER BY coup_denvoi_local DESC LIMIT ?",
            (limite,),
        ).fetchall()

    par_jour: dict[str, set[str]] = {}
    for ligne in lignes:
        jour = (ligne["coup_denvoi_local"] or "")[:10]
        if jour and ligne["match_id"]:
            par_jour.setdefault(jour, set()).add(ligne["match_id"])

    matchs: list[dict[str, Any]] = []
    jours_absents: list[str] = []
    for jour, voulus in sorted(par_jour.items()):
        try:
            flux = api_client.get_matches(
                jour, provider="flashscore", use_cache=use_cache
            )
        except api_client.ApiError:
            jours_absents.append(jour)
            continue
        matchs.extend(x for x in flux if x.get("match_id") in voulus)

    resultat = accumuler(matchs, use_cache)
    resultat.update(
        {
            "fiches_examinees": len(lignes),
            "matchs_retrouves": len(matchs),
            "jours_absents": jours_absents,
        }
    )
    return resultat


if __name__ == "__main__":
    raise SystemExit(main())
