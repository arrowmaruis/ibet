"""Base des arbitres : qui siffle, combien il sanctionne, et sur quel echantillon.

Le critere 12 mesure la discipline de l'arbitre designe, et il reste a poids
zero pour une raison qui n'a jamais ete son bien-fonde : **le projet n'avait
aucune source d'arbitres**. Flashscore, qui fournit tout le reste, rend un champ
`arbitre` vide sur son edition francaise ; api-football le donnerait mais
demande une cle payante qui n'est pas configuree. Le critere reconstituait donc
un profil dans les matchs des deux equipes, sur un echantillon petit ET biaise
-- ce sont les matchs de ces equipes-la, pas ceux de l'arbitre.

Ce module remplace cette reconstitution par une vraie base : les tableaux
d'arbitres de worldfootball.net, rendus cote serveur, une ligne par arbitre et
par competition avec son nombre de matchs, ses cartons et ses penalties.

**Ce que « style » veut dire ici.** Pas une etiquette (« severe », « permissif »)
mais des comptages : cartons par match, cartons rouges par match, penalties par
match, chacun rapporte a la moyenne de la competition ou l'arbitre officie. Un
adjectif ne se mesure pas et ne se verifie pas ; un rapport de 1.35 carton par
match contre 0.95 pour la competition, oui -- et c'est exactement la forme que
le critere 12 attend (`rapport`, centre sur 1).

**Ce que la base ne dit pas.** Elle donne le profil d'un arbitre, pas sa
designation : savoir que Siebert donne 5.2 cartons par match ne dit pas qu'il
arbitrera tel match. La designation reste hors de portee -- elle est publiee
tardivement, parfois apres coup. La base rend donc le critere 12 MESURABLE le
jour ou une source de designation existera, et utile des maintenant pour lire un
match une fois l'arbitre connu.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

from ibet import chemins
from ibet.sources import api_client, cache

DB_PATH = chemins.BASE_ARBITRES

WORLDFOOTBALL = "https://www.worldfootball.net/referees/%s/"

#: Trente jours : un tableau d'arbitres bouge d'une journee de championnat a
#: l'autre, pas d'une heure a l'autre. Le cache evite de redemander la meme page
#: a chaque construction, et la construction complete en demande une quinzaine.
TABLEAU_TTL = 30 * 24 * 3600

#: Competitions couvertes, par (cle interne, slug de la source, libelle).
#:
#: Les coupes d'Europe d'abord : ce sont elles que le projet vise, et un arbitre
#: qui y officie est par construction parmi les meilleurs de son pays. Les grands
#: championnats ensuite, parce que la plupart des arbitres UEFA y siffient aussi
#: -- c'est la que leur echantillon est large, donc leur profil fiable.
#:
#: Les slugs ont ete releves un par un : « bundesliga » n'a pas de prefixe de
#: pays quand « eng-premier-league » en a, et « conference-league » ne s'appelle
#: pas « uefa-europa-conference-league ». Les deviner rendait 404.
COMPETITIONS = (
    ("ldc", "champions-league-%s", "Ligue des Champions"),
    ("europa", "europa-league-%s", "Ligue Europa"),
    ("conference", "conference-league-%s", "Ligue Conference"),
    ("angleterre", "eng-premier-league-%s", "Premier League"),
    ("espagne", "esp-primera-division-%s", "LaLiga"),
    ("italie", "ita-serie-a-%s", "Serie A"),
    ("allemagne", "bundesliga-%s", "Bundesliga"),
    ("france", "fra-ligue-1-%s", "Ligue 1"),
    ("pays-bas", "ned-eredivisie-%s", "Eredivisie"),
    ("portugal", "por-primeira-liga-%s", "Liga Portugal"),
    ("turquie", "tur-sueperlig-%s", "Super Lig"),
)

#: Saisons demandees. Deux valent mieux qu'une : un arbitre qui n'a siffle que
#: trois matchs cette saison en a peut-etre vingt-cinq l'an dernier, et c'est
#: l'echantillon cumule qui rend son profil lisible.
SAISONS = ("2025-2026", "2024-2025")

SCHEMA = """
CREATE TABLE IF NOT EXISTS arbitres (
    id          INTEGER PRIMARY KEY,
    cle         TEXT NOT NULL UNIQUE,
    nom         TEXT NOT NULL,
    pays        TEXT NOT NULL DEFAULT '',
    releve_le   TEXT NOT NULL,
    payload     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_arbitres_nom ON arbitres(nom);
"""


def connect() -> sqlite3.Connection:
    connexion = sqlite3.connect(DB_PATH)
    connexion.row_factory = sqlite3.Row
    return connexion


def init() -> None:
    """Cree la base. Idempotent."""
    with connect() as connexion:
        connexion.executescript(SCHEMA)


def cle_arbitre(nom: str, pays: str = "") -> str:
    """Cle stable d'un arbitre : nom sans accents, minuscules, plus le pays.

    Les accents sont retires parce que la source les ecrit et que les autres
    n'en mettent pas toujours -- « Slavko Vincic » et « Slavko Vinčič » sont le
    meme homme, et les compter deux fois diviserait son echantillon.

    Le pays fait partie de la cle : deux arbitres peuvent porter le meme nom, et
    fondre leurs statistiques en donnerait un troisieme qui n'existe pas.
    """
    texte = unicodedata.normalize("NFKD", nom or "")
    texte = "".join(c for c in texte if not unicodedata.combining(c)).lower()
    texte = re.sub(r"[^a-z ]+", " ", texte)
    return "%s|%s" % (" ".join(texte.split()), (pays or "").strip().lower())


#: Un tableau entier, pour n'en garder QUE celui des arbitres.
#:
#: Une page de la source en porte plusieurs -- le classement du championnat y
#: figure aussi, et ses lignes ont autant de cellules. Les accepter toutes
#: faisait entrer « Arsenal FC » dans la base des arbitres, avec « Arsenal »
#: pour pays et 0.60 carton par match : la moyenne generale tombait a 1.82
#: carton quand le reel tourne autour de 4, sans qu'aucune erreur ne soit levee.
#: On identifie donc le bon tableau par ses EN-TETES.
_TABLEAU = re.compile(r"<table[^>]*>(.*?)</table>", re.S)

#: Marque du tableau des arbitres : cette colonne n'existe nulle part ailleurs.
_ENTETE_ARBITRES = "Yellow-Red"

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
    tableau, ou un slug qui a change). Ne leve pas : construire une base ne doit
    pas echouer parce qu'une competition sur onze manque.
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
    # sont des nombres. Une ligne de classement en a davantage (victoires, nuls,
    # defaites, buts, difference, points). C'est le seul critere qui tienne sans
    # dependre de la structure de la page.
    lignes: list[dict[str, Any]] = []
    for brut in _LIGNE.findall(reponse.text):
        cellules = [_BALISE.sub("", c).strip() for c in _CELLULE.findall(brut)]
        if len(cellules) != 9 or not cellules[2]:
            continue
        chiffres = [c.strip() for c in cellules[4:9]]
        if not all(c == "" or c.isdigit() for c in chiffres):
            continue
        # Le pays ne contient jamais de chiffre ; un nom d'equipe court non plus,
        # mais la longueur de ligne l'a deja ecarte.
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
                "penalties": _nombre(cellules[8]) if len(cellules) > 8 else 0.0,
            }
        )
    if lignes:
        cache.set(marque, lignes)
    return lignes


def _profil_vide(nom: str, pays: str) -> dict[str, Any]:
    return {
        "nom": nom,
        "pays": pays,
        "matchs": 0.0,
        "jaunes": 0.0,
        "jaune_rouge": 0.0,
        "rouges": 0.0,
        "penalties": 0.0,
        "competitions": {},
    }


def construire(
    saisons: Iterable[str] = SAISONS,
    competitions: Iterable[tuple[str, str, str]] = COMPETITIONS,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Agrege toutes les competitions demandees en une base d'arbitres.

    Un arbitre apparait dans plusieurs tableaux -- sa Ligue des Champions et son
    championnat national --, et ses lignes sont CUMULEES : c'est l'echantillon
    total qui rend son profil lisible, et un profil etabli sur trente matchs vaut
    infiniment mieux que trois profils de dix.

    Le detail par competition est conserve a cote du cumul. Il n'est pas
    decoratif : un arbitre peut etre severe dans son championnat et mesure en
    Coupe d'Europe, et le cumul seul le cacherait.
    """
    profils: dict[str, dict[str, Any]] = {}
    manquantes: list[str] = []
    lues = 0

    for cle_comp, gabarit, libelle in competitions:
        for saison in saisons:
            slug = gabarit % saison
            lignes = lire_tableau(slug, use_cache)
            if not lignes:
                manquantes.append(slug)
                continue
            lues += 1
            for ligne in lignes:
                cle = cle_arbitre(ligne["nom"], ligne["pays"])
                profil = profils.setdefault(
                    cle, _profil_vide(ligne["nom"], ligne["pays"])
                )
                for champ in ("matchs", "jaunes", "jaune_rouge", "rouges", "penalties"):
                    profil[champ] += ligne[champ]
                detail = profil["competitions"].setdefault(
                    cle_comp, {"libelle": libelle, "matchs": 0.0, "jaunes": 0.0,
                               "rouges": 0.0, "saisons": []}
                )
                detail["matchs"] += ligne["matchs"]
                detail["jaunes"] += ligne["jaunes"]
                detail["rouges"] += ligne["rouges"] + ligne["jaune_rouge"]
                if saison not in detail["saisons"]:
                    detail["saisons"].append(saison)

    for profil in profils.values():
        profil.update(_indices(profil))

    return {
        "arbitres": profils,
        "tableaux_lus": lues,
        "tableaux_absents": manquantes,
        "releve_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _indices(profil: dict[str, Any]) -> dict[str, Any]:
    """Les ratios par match : c'est eux, et non les totaux, qui comparent.

    Un arbitre a 52 cartons n'est pas plus severe qu'un arbitre a 19 s'il a
    arbitre dix matchs contre trois. Les totaux mesurent une carriere, les
    ratios un style -- et c'est le style qu'on veut lire.

    Un rouge et un second jaune sont comptes ensemble : les deux sortent un
    joueur, et la distinction n'interesse pas un parieur.
    """
    matchs = profil["matchs"]
    if matchs <= 0:
        return {"cartons_par_match": None, "rouges_par_match": None,
                "penalties_par_match": None}
    return {
        "cartons_par_match": round(profil["jaunes"] / matchs, 3),
        "rouges_par_match": round(
            (profil["rouges"] + profil["jaune_rouge"]) / matchs, 3
        ),
        "penalties_par_match": round(profil["penalties"] / matchs, 3),
    }


def enregistrer(base: dict[str, Any]) -> int:
    """Ecrit la base. Un arbitre deja connu est REMPLACE par son profil a jour.

    Contrairement a une prevision, un profil d'arbitre n'est pas un engagement
    pris a une date : c'est un etat courant, et le garder perime n'aurait aucun
    interet. Le relevé est horodate pour qu'on sache de quand il date.
    """
    init()
    horodatage = base.get("releve_le") or datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    with connect() as connexion:
        for cle, profil in base["arbitres"].items():
            connexion.execute(
                "INSERT INTO arbitres (cle, nom, pays, releve_le, payload)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(cle) DO UPDATE SET"
                " nom = excluded.nom, pays = excluded.pays,"
                " releve_le = excluded.releve_le, payload = excluded.payload",
                (
                    cle, profil["nom"], profil["pays"], horodatage,
                    json.dumps(profil, ensure_ascii=False),
                ),
            )
    return len(base["arbitres"])


def tous(minimum_matchs: float = 0.0) -> list[dict[str, Any]]:
    """Les arbitres connus, les plus sollicites d'abord.

    `minimum_matchs` ecarte ceux dont l'echantillon ne dit rien. Sous une
    dizaine de matchs, un ratio de cartons tient surtout du hasard des
    rencontres : trois derbys donnent un arbitre severe qui ne l'est pas.
    """
    init()
    with connect() as connexion:
        lignes = connexion.execute(
            "SELECT cle, nom, pays, releve_le, payload FROM arbitres"
        ).fetchall()
    profils = []
    for ligne in lignes:
        profil = json.loads(ligne["payload"])
        profil["cle"] = ligne["cle"]
        profil["releve_le"] = ligne["releve_le"]
        if profil.get("matchs", 0) >= minimum_matchs:
            profils.append(profil)
    profils.sort(key=lambda p: p.get("matchs", 0), reverse=True)
    return profils


def trouver(nom: str, pays: str = "") -> dict[str, Any] | None:
    """Un arbitre par son nom, accents et casse indifferents.

    Sans pays, on accepte une correspondance sur le seul nom -- c'est le cas
    courant, une source de designation ne donnant que « M. Oliver ». Le pays,
    quand il est connu, leve l'ambiguite entre deux homonymes.
    """
    init()
    voulu = cle_arbitre(nom, pays)
    with connect() as connexion:
        if pays:
            ligne = connexion.execute(
                "SELECT payload FROM arbitres WHERE cle = ?", (voulu,)
            ).fetchone()
            return json.loads(ligne["payload"]) if ligne else None
        prefixe = voulu.split("|")[0]
        lignes = connexion.execute(
            "SELECT payload, cle FROM arbitres WHERE cle LIKE ?", (prefixe + "|%",)
        ).fetchall()
    # Plusieurs homonymes de pays differents : on ne choisit pas a la place de
    # l'appelant, on rend le plus experimente et il pourra preciser le pays.
    profils = [json.loads(l["payload"]) for l in lignes]
    profils.sort(key=lambda p: p.get("matchs", 0), reverse=True)
    return profils[0] if profils else None


def reperes() -> dict[str, Any]:
    """Moyennes de l'ensemble : de quoi dire si un arbitre s'en ecarte.

    Un ratio seul ne se lit pas. 4.5 cartons par match est severe en Premier
    League et ordinaire en Serie A ; c'est l'ECART a la moyenne qui informe, et
    c'est cette forme-la -- un rapport centre sur 1 -- que le critere 12 attend.
    """
    profils = [p for p in tous() if (p.get("matchs") or 0) > 0]
    if not profils:
        return {}
    total_matchs = sum(p["matchs"] for p in profils)
    return {
        "arbitres": len(profils),
        "matchs_cumules": total_matchs,
        "cartons_par_match": round(
            sum(p["jaunes"] for p in profils) / total_matchs, 3
        ),
        "rouges_par_match": round(
            sum(p["rouges"] + p["jaune_rouge"] for p in profils) / total_matchs, 3
        ),
        "penalties_par_match": round(
            sum(p["penalties"] for p in profils) / total_matchs, 3
        ),
    }


def rapport_au_repere(profil: dict[str, Any]) -> dict[str, Any]:
    """Les ratios d'un arbitre rapportes a la moyenne, centres sur 1.

    C'est la forme que le critere 12 emploie deja : un arbitre moyen vaut 1, un
    arbitre severe 1.2. Rendre ce rapport plutot que le ratio brut evite a
    l'appelant de refaire la normalisation -- et de la refaire autrement.
    """
    base = reperes()
    if not base or not profil.get("matchs"):
        return {}
    rendu = {}
    for champ in ("cartons_par_match", "rouges_par_match", "penalties_par_match"):
        moyenne = base.get(champ) or 0
        valeur = profil.get(champ)
        if moyenne and valeur is not None:
            rendu[champ] = round(valeur / moyenne, 3)
    return rendu

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
