"""Stockage des previsions dans une base SQLite locale.

Une prevision coute cher a produire : une requete d'historique par equipe, une
dizaine d'autres pour reconstituer la reference du championnat, et le calcul
par-dessus. La relire ne doit rien couter -- et surtout pas retourner
interroger la source, qui n'est pas une API publiee et dont les CGU demandent
un volume de requetes raisonnable. Une fois emise, une prevision est donc
ecrite ici et n'est plus jamais recalculee.

SQLite plutot qu'un serveur de base de donnees : un fichier, aucune
installation, aucun processus a lancer, et le module est dans la bibliotheque
standard. Le volume attendu se compte en milliers de lignes.

Quatre tables :

  predictions  la prevision complete, telle qu'elle a ete emise (`payload`),
               plus les colonnes qui servent a lister et a filtrer sans avoir
               a ouvrir le JSON.
  offres       une ligne par proposition, pour que le bilan (« combien de
               propositions se sont realisees ») soit une requete et non un
               parcours de tous les fichiers.
  cotes        un releve par appel, HORODATE et jamais ecrase. C'est ce qui
               distingue le critere 13 des treize autres : un mouvement de
               ligne ne se lit pas dans une cote, seulement dans deux cotes
               prises a deux moments. Garder la derniere effacerait la seule
               chose que ce critere sait voir.
  resultats    le score de chaque match termine vu par le programme, fiche ou
               pas. La source ne publie que sept jours : sans cette table, une
               fiche non verifiee dans ce delai ne se tranche jamais.

Le `payload` est conserve **tel quel**, sans reecriture : une prevision est un
engagement pris a une date, avec un reglage donne. La relire modifiee lui
oterait toute valeur -- c'est le meme principe qui fait que `verify.py` tranche
sans jamais retoucher les probabilites annoncees.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DB_PATH = Path(__file__).parent / "ibet.db"

# Le libelle que toutes les sources prennent une fois normalisees par
# `api_client` (FINISHED). Repete ici plutot qu'importe : `store` ne doit
# dependre d'aucun module qui, lui, parle au reseau.
STATUT_TERMINE = "Termine"

# Fichier historique, anterieur a la base. Importe une fois, puis conserve tel
# quel : il reste la trace d'origine et `verify.py` continue de le lire.
LEGACY_RECORD = Path(__file__).parent / "predictions_ouvertes.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id                   INTEGER PRIMARY KEY,
    match_id             TEXT NOT NULL UNIQUE,
    emis_le              TEXT NOT NULL,
    libelle              TEXT NOT NULL,
    competition          TEXT NOT NULL DEFAULT '',
    coup_denvoi_local    TEXT NOT NULL DEFAULT '',
    statut_a_l_emission  TEXT NOT NULL DEFAULT '',
    url                  TEXT NOT NULL DEFAULT '',
    reglage              TEXT NOT NULL DEFAULT '{}',
    payload              TEXT NOT NULL,
    resultat_reel        TEXT
);

CREATE TABLE IF NOT EXISTS offres (
    id             INTEGER PRIMARY KEY,
    prediction_id  INTEGER NOT NULL REFERENCES predictions(id) ON DELETE CASCADE,
    grandeur       TEXT NOT NULL,
    pari           TEXT NOT NULL,
    probabilite    REAL NOT NULL,
    verifie        INTEGER
);

CREATE TABLE IF NOT EXISTS cotes (
    id          INTEGER PRIMARY KEY,
    match_id    TEXT NOT NULL,
    releve_le   TEXT NOT NULL,
    operateur   TEXT NOT NULL DEFAULT '',
    marche      TEXT NOT NULL DEFAULT '1x2',
    valeurs     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coupons (
    id         INTEGER PRIMARY KEY,
    cree_le    TEXT NOT NULL,
    libelle    TEXT NOT NULL DEFAULT '',
    filtres    TEXT NOT NULL DEFAULT '{}',
    payload    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resultats (
    match_id          TEXT PRIMARY KEY,
    releve_le         TEXT NOT NULL,
    libelle           TEXT NOT NULL DEFAULT '',
    coup_denvoi_local TEXT NOT NULL DEFAULT '',
    statut            TEXT NOT NULL DEFAULT '',
    score_domicile    INTEGER,
    score_exterieur   INTEGER,
    stats             TEXT NOT NULL DEFAULT '{}'
);

-- Feuille de match d'un resultat archive : arbitre, entraineurs, temps de jeu
-- et cartons par joueur (`api_client.feuille_de_match`). Table a part plutot
-- que dans `resultats.stats` : un nouvel archivage du meme match reecrit ce
-- blob et effacerait la feuille. `date` et `competition` la rendent lisible
-- sans le cache, et donc rejouable avec coupure temporelle.
CREATE TABLE IF NOT EXISTS feuilles (
    match_id     TEXT PRIMARY KEY,
    releve_le    TEXT NOT NULL,
    date         TEXT NOT NULL DEFAULT '',
    competition  TEXT NOT NULL DEFAULT '',
    domicile     TEXT NOT NULL DEFAULT '',
    exterieur    TEXT NOT NULL DEFAULT '',
    arbitre      TEXT NOT NULL DEFAULT '',
    feuille      TEXT NOT NULL DEFAULT '{}'
);

-- Statistiques par joueur des matchs des championnats suivis
-- (`api_client.stats_joueurs`) : la matiere des profils de style
-- (`modeles/styles.py`). Une ligne par joueur et par match ; `stats` ne porte
-- que les grandeurs non nulles. `matchs_joueurs` dit quels matchs ont ete
-- releves -- y compris ceux que le fournisseur ne couvre pas (`couvert` = 0),
-- pour ne pas les redemander a chaque passage.
CREATE TABLE IF NOT EXISTS matchs_joueurs (
    match_id      TEXT PRIMARY KEY,
    releve_le     TEXT NOT NULL,
    date          TEXT NOT NULL DEFAULT '',
    championnat   TEXT NOT NULL DEFAULT '',
    saison        TEXT NOT NULL DEFAULT '',
    domicile      TEXT NOT NULL DEFAULT '',
    exterieur     TEXT NOT NULL DEFAULT '',
    couvert       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stats_joueurs (
    match_id      TEXT NOT NULL,
    joueur_id     TEXT NOT NULL,
    cote          TEXT NOT NULL,
    equipe        TEXT NOT NULL DEFAULT '',
    nom           TEXT NOT NULL DEFAULT '',
    poste         TEXT NOT NULL DEFAULT '?',
    poste_libelle TEXT NOT NULL DEFAULT '',
    titulaire     INTEGER NOT NULL DEFAULT 0,
    minutes       REAL NOT NULL DEFAULT 0,
    stats         TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (match_id, joueur_id)
);

CREATE INDEX IF NOT EXISTS idx_stats_joueurs_joueur ON stats_joueurs(joueur_id);
CREATE INDEX IF NOT EXISTS idx_stats_joueurs_equipe ON stats_joueurs(equipe);
CREATE INDEX IF NOT EXISTS idx_matchs_joueurs_date ON matchs_joueurs(championnat, date);
CREATE INDEX IF NOT EXISTS idx_feuilles_arbitre ON feuilles(arbitre, date);
CREATE INDEX IF NOT EXISTS idx_offres_prediction ON offres(prediction_id);
CREATE INDEX IF NOT EXISTS idx_predictions_coup_denvoi ON predictions(coup_denvoi_local);
CREATE INDEX IF NOT EXISTS idx_cotes_match ON cotes(match_id, releve_le);
"""


def connect() -> sqlite3.Connection:
    """Connexion prete a l'emploi : lignes nommees et cles etrangeres actives.

    `foreign_keys` n'est pas actif par defaut dans SQLite : sans ce PRAGMA, la
    suppression en cascade des offres ne se ferait pas.
    """
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init() -> None:
    """Cree les tables si besoin. Sans effet si elles existent deja."""
    with connect() as connection:
        connection.executescript(SCHEMA)


def match_id_of(prediction: dict[str, Any]) -> str:
    """Identifiant du match, lu dans la prevision ou extrait de son URL.

    Les previsions du fichier historique ne portent pas le champ : leur URL
    (`https://www.flashscore.fr/match/zsqkxoXj/`) le contient, et c'est le meme
    identifiant que celui rendu par `api_client`.
    """
    direct = str(prediction.get("match_id") or "").strip()
    if direct:
        return direct
    url = str(prediction.get("url") or "").rstrip("/")
    return url.rsplit("/", 1)[-1] if url else ""


def _offers_of(prediction: dict[str, Any]) -> Iterable[tuple[str, str, float, bool | None]]:
    for quantity in prediction.get("grandeurs") or []:
        label = str(quantity.get("grandeur") or quantity.get("libelle") or "")
        for offer in quantity.get("offres") or []:
            probability = offer.get("probabilite")
            if probability is None:
                continue
            yield label, str(offer.get("pari") or ""), float(probability), offer.get("verifie")


def save(prediction: dict[str, Any]) -> str:
    """Enregistre une prevision. Retourne l'identifiant du match.

    Une prevision par match : reemettre pour le meme match remplace la
    precedente. Conserver les deux obligerait a choisir laquelle afficher, et
    la seule reponse honnete serait « la derniere » -- autant ne garder qu'elle.
    """
    identifier = match_id_of(prediction)
    if not identifier:
        raise ValueError("Prevision sans identifiant de match ni URL exploitable.")

    with connect() as connection:
        connection.execute("DELETE FROM predictions WHERE match_id = ?", (identifier,))
        cursor = connection.execute(
            """
            INSERT INTO predictions (
                match_id, emis_le, libelle, competition, coup_denvoi_local,
                statut_a_l_emission, url, reglage, payload, resultat_reel
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identifier,
                str(prediction.get("emis_le") or ""),
                str(prediction.get("match") or ""),
                str(prediction.get("competition") or ""),
                str(prediction.get("coup_denvoi_local") or ""),
                str(prediction.get("statut_a_l_emission") or ""),
                str(prediction.get("url") or ""),
                json.dumps(prediction.get("reglage") or {}, ensure_ascii=False),
                json.dumps(prediction, ensure_ascii=False),
                (
                    json.dumps(prediction["resultat_reel"], ensure_ascii=False)
                    if prediction.get("resultat_reel")
                    else None
                ),
            ),
        )
        connection.executemany(
            "INSERT INTO offres (prediction_id, grandeur, pari, probabilite, verifie)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (cursor.lastrowid, quantity, bet, probability, checked)
                for quantity, bet, probability, checked in _offers_of(prediction)
            ],
        )
    return identifier


# ---------------------------------------------------------------------------
# Archive des resultats
# ---------------------------------------------------------------------------
#
# La source ne publie que sept jours autour d'aujourd'hui. Passe ce delai, le
# resultat d'un match n'est plus interrogeable, et une fiche qui l'attendait ne
# se tranchera JAMAIS : elle ne comptera dans aucune mesure, ni en reussite ni
# en echec. Le projet en a perdu onze d'un coup -- 264 propositions -- faute
# d'avoir lance la verification pendant deux semaines.
#
# La parade n'est pas de verifier plus souvent, c'est de ne plus dependre du
# moment ou on le fait : des qu'un match termine passe sous les yeux du
# programme, son resultat est archive ici. La verification lit ensuite
# l'archive, et la source seulement si l'archive ne sait pas.
#
# L'archive est volontairement independante des fiches : on garde le resultat
# meme d'un match sur lequel aucune prevision n'a ete emise. Il ne coute rien,
# et une prevision peut etre emise plus tard sur un match deja joue -- le
# backtest en vit.


def _ecrire_resultat(
    connection: sqlite3.Connection,
    identifiant: str,
    match: dict[str, Any],
    stats: dict[str, Any] | None,
) -> None:
    """Ecrit une ligne d'archive sur une connexion deja ouverte."""
    detail = json.dumps(stats or {}, ensure_ascii=False)
    if not stats:
        ancien = connection.execute(
            "SELECT stats FROM resultats WHERE match_id = ?", (identifiant,)
        ).fetchone()
        # On ne remplace pas des statistiques deja connues par un objet vide.
        if ancien:
            detail = ancien["stats"]
    connection.execute(
        """
        INSERT INTO resultats
            (match_id, releve_le, libelle, coup_denvoi_local, statut,
             score_domicile, score_exterieur, stats)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(match_id) DO UPDATE SET
            releve_le = excluded.releve_le,
            statut = excluded.statut,
            score_domicile = excluded.score_domicile,
            score_exterieur = excluded.score_exterieur,
            stats = excluded.stats
        """,
        (
            identifiant,
            datetime.now().astimezone().isoformat(timespec="seconds"),
            "%s - %s" % (match.get("domicile", ""), match.get("exterieur", "")),
            match.get("coup_denvoi_local", "") or match.get("heure", ""),
            match.get("statut", ""),
            match.get("score_domicile"),
            match.get("score_exterieur"),
            detail,
        ),
    )


def archiver_resultat(
    match: dict[str, Any],
    stats: dict[str, Any] | None = None,
    connexion: sqlite3.Connection | None = None,
) -> bool:
    """Garde le resultat d'un match termine. Rend True s'il a ete ecrit.

    Un match sans score n'est pas archive : il n'y a rien a garder, et ecrire
    une ligne vide empecherait la vraie de s'ecrire plus tard.

    Les statistiques sont facultatives et ne s'ajoutent qu'en complement : un
    second passage qui les apporte enrichit la ligne sans effacer le score.

    `connexion` sert aux ecritures en lot. Une connexion SQLite par match tient
    tant qu'on en archive trois ; a l'echelle d'une journee -- deux cents
    matchs -- ou d'une reprise du cache -- des dizaines de milliers -- c'est
    une transaction et un fsync chacun, et l'operation passe de la seconde a
    plusieurs minutes.
    """
    identifiant = (match.get("match_id") or "").strip()
    if not identifiant or match.get("score_domicile") is None:
        return False
    # Un match EN COURS porte un score, et ce score n'est pas un resultat :
    # trancher une fiche sur un 0-0 de la vingtieme minute serait pire que la
    # laisser en attente -- une erreur silencieuse contre un trou visible. Le
    # garde est ici, au plus bas niveau, parce que c'est precisement la faute
    # qu'une reprise de cache a commise.
    statut = (match.get("statut") or STATUT_TERMINE).strip()
    if statut != STATUT_TERMINE:
        return False

    if connexion is not None:
        _ecrire_resultat(connexion, identifiant, match, stats)
        return True
    with connect() as connection:
        _ecrire_resultat(connection, identifiant, match, stats)
    return True


def archiver_journee(
    matchs: Iterable[dict[str, Any]], statut_termine: str = STATUT_TERMINE
) -> int:
    """Archive tous les matchs termines d'une liste. Rend le nombre ecrit.

    Appelee partout ou le programme recupere deja une journee : afficher des
    matchs, emettre une fiche, verifier. Le resultat est donc capture au passage,
    sans requete supplementaire -- c'est ce qui rend la parade gratuite, et donc
    tenable. Une seule transaction pour toute la journee : cette fonction est
    sur le chemin d'une requete HTTP, elle doit couter quelques millisecondes.
    """
    termines = [m for m in matchs if m.get("statut") == statut_termine]
    if not termines:
        return 0
    ecrits = 0
    with connect() as connection:
        for match in termines:
            ecrits += 1 if archiver_resultat(match, connexion=connection) else 0
    return ecrits


def resultat(match_id: str) -> dict[str, Any] | None:
    """Resultat archive d'un match, ou None."""
    with connect() as connection:
        ligne = connection.execute(
            "SELECT * FROM resultats WHERE match_id = ?", (match_id,)
        ).fetchone()
    if not ligne:
        return None
    return {
        "match_id": ligne["match_id"],
        "releve_le": ligne["releve_le"],
        "libelle": ligne["libelle"],
        "coup_denvoi_local": ligne["coup_denvoi_local"],
        "statut": ligne["statut"],
        "score_domicile": ligne["score_domicile"],
        "score_exterieur": ligne["score_exterieur"],
        "stats": json.loads(ligne["stats"] or "{}"),
    }


def completer_stats(match_id: str, stats: dict[str, Any]) -> bool:
    """Ajoute les statistiques detaillees a un resultat deja archive."""
    if not stats:
        return False
    with connect() as connection:
        curseur = connection.execute(
            "UPDATE resultats SET stats = ? WHERE match_id = ?",
            (json.dumps(stats, ensure_ascii=False), match_id),
        )
    return curseur.rowcount > 0


def archiver_feuille(
    match_id: str,
    feuille: dict[str, Any],
    date: str = "",
    competition: str = "",
    domicile: str = "",
    exterieur: str = "",
    connexion: sqlite3.Connection | None = None,
) -> bool:
    """Garde la feuille de match d'un resultat. Une feuille vide n'est pas ecrite :
    elle empecherait la vraie de s'ecrire au prochain passage."""
    if not match_id or not feuille:
        return False
    ligne = (
        match_id,
        datetime.now().astimezone().isoformat(timespec="seconds"),
        date or "",
        competition or "",
        domicile or "",
        exterieur or "",
        (feuille.get("arbitre") or "").strip(),
        json.dumps(feuille, ensure_ascii=False),
    )
    requete = """
        INSERT OR REPLACE INTO feuilles
            (match_id, releve_le, date, competition, domicile, exterieur,
             arbitre, feuille)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    if connexion is not None:
        connexion.execute(requete, ligne)
        return True
    with connect() as connection:
        connection.execute(requete, ligne)
    return True


def feuilles_connues() -> set[str]:
    """Identifiants des matchs dont la feuille est deja archivee."""
    with connect() as connection:
        return {r[0] for r in connection.execute("SELECT match_id FROM feuilles")}


def archiver_stats_joueurs(
    match: dict[str, Any],
    joueurs: dict[str, Any],
    championnat: str,
    saison: str,
    connexion: sqlite3.Connection,
) -> int:
    """Garde les statistiques par joueur d'un match. Rend le nombre de joueurs.

    Un match que le fournisseur ne couvre pas (`joueurs` vide) est tout de meme
    note, `couvert` a zero : le redemander a chaque passage ne le ferait pas
    apparaitre.
    """
    match_id = match.get("match_id") or ""
    if not match_id:
        return 0
    equipes = {"domicile": match.get("domicile") or "", "exterieur": match.get("exterieur") or ""}
    n = 0
    for cote in ("domicile", "exterieur"):
        for j in (joueurs or {}).get(cote) or []:
            stats = j.get("stats") or {}
            connexion.execute(
                """INSERT OR REPLACE INTO stats_joueurs
                   (match_id, joueur_id, cote, equipe, nom, poste, poste_libelle,
                    titulaire, minutes, stats)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (match_id, j.get("id") or "", cote, equipes[cote], j.get("nom") or "",
                 j.get("poste") or "?", j.get("poste_libelle") or "",
                 1 if j.get("titulaire") else 0, float(stats.get("minutes") or 0),
                 json.dumps(stats, ensure_ascii=False)),
            )
            n += 1
    connexion.execute(
        """INSERT OR REPLACE INTO matchs_joueurs
           (match_id, releve_le, date, championnat, saison, domicile, exterieur, couvert)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (match_id, datetime.now().astimezone().isoformat(timespec="seconds"),
         match.get("date") or "", championnat, saison, equipes["domicile"],
         equipes["exterieur"], 1 if n else 0),
    )
    return n


def matchs_joueurs_connus() -> set[str]:
    with connect() as connection:
        return {r[0] for r in connection.execute("SELECT match_id FROM matchs_joueurs")}


def stats_joueurs_archivees(championnat: str = "") -> list[dict[str, Any]]:
    """Matchs couverts avec leurs joueurs et les stats d'equipe archivees,
    des plus anciens aux plus recents. Un element par match :

        {"match_id", "date", "championnat", "saison", "domicile", "exterieur",
         "stats": <resultats.stats>, "joueurs": {"domicile": [...], "exterieur": [...]}}
    """
    requete = """
        SELECT m.match_id, m.date, m.championnat, m.saison, m.domicile, m.exterieur,
               r.stats AS stats_match
        FROM matchs_joueurs m LEFT JOIN resultats r ON r.match_id = m.match_id
        WHERE m.couvert = 1
    """
    parametres: tuple[Any, ...] = ()
    if championnat:
        requete += " AND m.championnat = ?"
        parametres = (championnat,)
    requete += " ORDER BY m.date, m.match_id"
    with connect() as connection:
        matchs = {
            l["match_id"]: {
                "match_id": l["match_id"], "date": l["date"],
                "championnat": l["championnat"], "saison": l["saison"],
                "domicile": l["domicile"], "exterieur": l["exterieur"],
                "stats": json.loads(l["stats_match"] or "{}"),
                "joueurs": {"domicile": [], "exterieur": []},
            }
            for l in connection.execute(requete, parametres)
        }
        for l in connection.execute(
            "SELECT match_id, joueur_id, cote, nom, poste, poste_libelle, titulaire, "
            "minutes, stats FROM stats_joueurs"
        ):
            m = matchs.get(l["match_id"])
            if m is None:
                continue
            m["joueurs"][l["cote"]].append({
                "id": l["joueur_id"], "nom": l["nom"], "poste": l["poste"],
                "poste_libelle": l["poste_libelle"], "titulaire": bool(l["titulaire"]),
                "minutes": l["minutes"], "stats": json.loads(l["stats"] or "{}"),
            })
    return list(matchs.values())


def feuilles(avant: str = "") -> list[dict[str, Any]]:
    """Feuilles archivees jointes a leurs statistiques, des plus anciennes aux
    plus recentes. `avant` (AAAA-MM-JJ) coupe strictement : une prevision ne
    doit jamais lire le match qu'elle prevoit, ni ceux qui le suivent."""
    requete = """
        SELECT f.match_id, f.date, f.competition, f.domicile, f.exterieur,
               f.arbitre, f.feuille, r.stats
        FROM feuilles f LEFT JOIN resultats r ON r.match_id = f.match_id
    """
    parametres: tuple[Any, ...] = ()
    if avant:
        requete += " WHERE f.date != '' AND f.date < ?"
        parametres = (avant,)
    requete += " ORDER BY f.date, f.match_id"
    with connect() as connection:
        lignes = connection.execute(requete, parametres).fetchall()
    return [
        {
            "match_id": l["match_id"],
            "date": l["date"],
            "competition": l["competition"],
            "domicile": l["domicile"],
            "exterieur": l["exterieur"],
            "arbitre": l["arbitre"],
            "feuille": json.loads(l["feuille"] or "{}"),
            "stats": json.loads(l["stats"] or "{}"),
        }
        for l in lignes
    ]


def resultats_archives() -> int:
    with connect() as connection:
        return connection.execute("SELECT COUNT(*) FROM resultats").fetchone()[0]


def lignes_dun_historique(sides: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Remet a l'endroit les matchs d'un historique d'equipes.

    L'historique dit "ce que MON equipe a marque et encaisse, chez elle ou a
    l'exterieur". L'archive veut le match tel qu'il s'est joue : qui recevait,
    et le score dans cet ordre.

    C'est la forme rendue par la recuperation d'historique d'`api_client`, et
    c'est aussi ce qui dort dans le cache : une seule conversion pour les deux.
    """
    lignes: dict[str, dict[str, Any]] = {}
    for bord in sides:
        if not isinstance(bord, dict):
            continue
        equipe = (bord.get("equipe") or "").strip()
        for entree in bord.get("matchs") or []:
            if not isinstance(entree, dict):
                continue
            pour = entree.get("buts_pour")
            contre = entree.get("buts_contre")
            adverse = (entree.get("adversaire") or "").strip()
            identifiant = (entree.get("match_id") or "").strip()
            if not identifiant or not equipe or not adverse:
                continue
            if pour is None or contre is None:
                continue
            chez_soi = entree.get("lieu") == "domicile"
            lignes[identifiant] = {
                "match_id": identifiant,
                "domicile": equipe if chez_soi else adverse,
                "exterieur": adverse if chez_soi else equipe,
                "score_domicile": pour if chez_soi else contre,
                "score_exterieur": contre if chez_soi else pour,
                "coup_denvoi_local": entree.get("kickoff_utc", ""),
                "statut": STATUT_TERMINE,
            }
    return lignes


def archiver_historique(sides: Iterable[dict[str, Any]]) -> int:
    """Archive tous les matchs d'un historique d'equipes. Rend le nombre ecrit.

    Un historique porte une dizaine de matchs par equipe, remontant des mois :
    c'est la seule voie qui franchisse la fenetre de sept jours de la source.
    """
    lignes = lignes_dun_historique(sides)
    if not lignes:
        return 0
    ecrits = 0
    with connect() as connection:
        for ligne in lignes.values():
            ecrits += 1 if archiver_resultat(ligne, connexion=connection) else 0
    return ecrits


def _resultats_du_cache(dossier: Path) -> dict[str, dict[str, Any]]:
    """Tous les matchs termines retrouvables dans le cache des requetes.

    Deux gisements, et ils ne se recouvrent pas :

      `|hist|`        l'historique des equipes suivies. Vu depuis une equipe,
                      donc a remettre a l'endroit -- "j'ai marque 2 a
                      l'exterieur" devient "0 - 2". C'est de loin le plus
                      profond : il remonte bien au-dela des sept jours.
      `|flashscore|`  les journees consultees, deja dans la bonne forme.

    Contrairement aux notes de force, les amicaux sont gardes : l'archive est
    un releve de ce qui s'est passe, pas une entree du modele. En ecarter un
    ici, ce serait rendre une fiche intranchable plus tard.
    """
    trouves: dict[str, dict[str, Any]] = {}
    for fichier in dossier.glob("*.json"):
        try:
            paquet = json.loads(fichier.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError):
            continue
        cle = str(paquet.get("key", ""))
        valeur = paquet.get("value")

        if "|hist|" in cle:
            for identifiant, ligne in lignes_dun_historique(valeur or []).items():
                trouves.setdefault(identifiant, ligne)

        elif "|flashscore|" in cle:
            for match in valeur or []:
                if not isinstance(match, dict):
                    continue
                identifiant = (match.get("match_id") or "").strip()
                if not identifiant or match.get("score_domicile") is None:
                    continue
                if match.get("statut") != STATUT_TERMINE:
                    # Une journee mise en cache pendant qu'elle se jouait garde
                    # des scores partiels. Ils ne valent rien comme resultat.
                    continue
                # La journee prime sur l'historique : elle nomme les equipes
                # comme la source les ecrit, et porte le vrai statut.
                trouves[identifiant] = match
    return trouves


def _stats_du_cache(dossier: Path) -> dict[str, dict[str, Any]]:
    """Fiches statistiques en cache, indexees par identifiant de match."""
    trouvees: dict[str, dict[str, Any]] = {}
    for fichier in dossier.glob("*.json"):
        try:
            paquet = json.loads(fichier.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError):
            continue
        cle = str(paquet.get("key", ""))
        if "|stats|" not in cle:
            continue
        valeur = paquet.get("value")
        if not isinstance(valeur, dict) or not valeur.get("domicile"):
            continue
        trouvees[cle.rsplit("|", 1)[-1]] = valeur
    return trouvees


def reprendre_le_cache(dossier: Path | None = None) -> dict[str, int]:
    """Remplit l'archive avec ce que le cache des requetes contient deja.

    Ce cache s'est rempli au fil des mois par des requetes qui ne seront pas
    refaites : il contient des milliers de matchs termines et leurs fiches
    statistiques, bien au-dela de la fenetre de sept jours de la source. Les
    verser dans l'archive ne coute aucune requete, et donne d'un coup un
    historique que la capture au fil de l'eau mettrait une saison a batir.

    Idempotent : relancer ne double rien, `archiver_resultat` reecrit la ligne
    du meme match. Rend le detail de ce qui a ete ecrit.
    """
    import cache as _cache  # local : garde `store` importable sans le cache

    racine = dossier or _cache.CACHE_DIR
    if not racine.exists():
        return {"matchs": 0, "stats": 0}

    matchs = _resultats_du_cache(racine)
    stats = _stats_du_cache(racine)
    ecrits = 0
    enrichis = 0
    with connect() as connection:
        for identifiant, match in matchs.items():
            detail = stats.get(identifiant)
            if archiver_resultat(match, detail, connexion=connection):
                ecrits += 1
                enrichis += 1 if detail else 0
    return {"matchs": ecrits, "stats": enrichis}


def purger_non_termines() -> int:
    """Retire les lignes qui ne sont pas des resultats definitifs.

    Sert a rattraper une archive ecrite avant le garde-fou d'`archiver_resultat`
    -- une reprise de cache y avait verse des matchs en cours. Sans effet une
    fois l'archive saine.
    """
    with connect() as connection:
        curseur = connection.execute(
            "DELETE FROM resultats WHERE statut <> ?", (STATUT_TERMINE,)
        )
    return curseur.rowcount


def save_odds(
    match_id: str,
    valeurs: dict[str, float],
    operateur: str = "",
    marche: str = "1x2",
    releve_le: str = "",
) -> dict[str, Any]:
    """Enregistre un releve de cotes. Ne remplace jamais le precedent.

    Chaque appel ajoute une ligne : c'est un releve a un instant, pas un etat
    courant. Le mouvement de ligne -- le seul signal que le critere 13 sache
    lire -- n'existe que dans la difference entre deux releves.
    """
    identifiant = (match_id or "").strip()
    if not identifiant:
        raise ValueError("Releve de cotes sans identifiant de match.")
    propres = {
        cle: float(valeur)
        for cle, valeur in (valeurs or {}).items()
        if valeur is not None and float(valeur) > 1.0
    }
    if not propres:
        raise ValueError(
            "Aucune cote exploitable : une cote decimale est superieure a 1."
        )
    horodatage = releve_le or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as connection:
        connection.execute(
            "INSERT INTO cotes (match_id, releve_le, operateur, marche, valeurs)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                identifiant, horodatage, operateur, marche,
                json.dumps(propres, ensure_ascii=False),
            ),
        )
    return {"match_id": identifiant, "releve_le": horodatage, "cotes": propres}


def odds_history(match_id: str, marche: str = "1x2") -> list[dict[str, Any]]:
    """Releves de cotes d'un match, du plus ancien au plus recent.

    L'ordre chronologique n'est pas cosmetique : c'est lui qui fait du premier
    et du dernier element les deux bornes du mouvement de ligne.
    """
    with connect() as connection:
        rows = connection.execute(
            "SELECT releve_le, operateur, marche, valeurs FROM cotes"
            " WHERE match_id = ? AND marche = ? ORDER BY releve_le, id",
            (match_id, marche),
        ).fetchall()
    return [
        {
            "releve_le": row["releve_le"],
            "operateur": row["operateur"],
            "marche": row["marche"],
            "cotes": json.loads(row["valeurs"]),
        }
        for row in rows
    ]


def latest_odds(match_id: str, marche: str = "1x2") -> dict[str, float] | None:
    """Dernier releve connu, ou None si le match n'en a aucun."""
    historique = odds_history(match_id, marche)
    return historique[-1]["cotes"] if historique else None


def cotes_courantes(match_id: str, marche: str = "1x2") -> dict[str, float]:
    """Derniere cote connue de CHAQUE cle, fusionnee sur tous les releves.

    `latest_odds` rend le dernier releve entier, ce qui convient au 1X2 : ses
    trois issues sont relevees ensemble, en une ligne. Cela ne convient PAS a un
    marche indexe par libelle de proposition, ou chaque cote est saisie
    separement : trois saisies font trois lignes, et ne lire que la derniere
    perdait les deux premieres -- sans erreur, sans message, la valorisation
    portant simplement sur une option au lieu de trois.

    Chaque cle garde donc sa propre histoire, et l'on prend la plus recente de
    chacune. Les releves restent intacts : le mouvement de ligne se lit toujours
    dans `odds_history`.
    """
    courantes: dict[str, float] = {}
    for releve in odds_history(match_id, marche):
        for cle, valeur in (releve.get("cotes") or {}).items():
            courantes[cle] = float(valeur)
    return courantes


def save_coupon(
    conseils: dict[str, Any], libelle: str = "", filtres: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Enregistre un coupon. N'ecrase jamais le precedent.

    Un coupon est un engagement pris a une date, comme une prevision : deux
    coupons composes le meme jour avec des filtres differents sont deux
    engagements distincts, et le second n'annule pas le premier. Le contenu est
    donc ecrit tel quel, avec ses filtres -- relire un coupon sans savoir ce
    qu'on avait demande ne dirait rien de ce qu'on avait decide.
    """
    horodatage = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as connection:
        curseur = connection.execute(
            "INSERT INTO coupons (cree_le, libelle, filtres, payload)"
            " VALUES (?, ?, ?, ?)",
            (
                horodatage,
                (libelle or "").strip(),
                json.dumps(filtres or {}, ensure_ascii=False),
                json.dumps(conseils, ensure_ascii=False),
            ),
        )
        identifiant = curseur.lastrowid
    return {"id": identifiant, "cree_le": horodatage, "libelle": libelle}


def coupons(limite: int = 100) -> list[dict[str, Any]]:
    """Coupons enregistres, du plus recent au plus ancien."""
    with connect() as connection:
        lignes = connection.execute(
            "SELECT id, cree_le, libelle, filtres, payload FROM coupons"
            " ORDER BY id DESC LIMIT ?",
            (limite,),
        ).fetchall()
    return [
        {
            "id": ligne["id"],
            "cree_le": ligne["cree_le"],
            "libelle": ligne["libelle"],
            "filtres": json.loads(ligne["filtres"]),
            "conseils": json.loads(ligne["payload"]),
        }
        for ligne in lignes
    ]


def coupon(identifiant: int) -> dict[str, Any] | None:
    """Un coupon precis, ou None."""
    with connect() as connection:
        ligne = connection.execute(
            "SELECT id, cree_le, libelle, filtres, payload FROM coupons WHERE id = ?",
            (identifiant,),
        ).fetchone()
    if not ligne:
        return None
    return {
        "id": ligne["id"],
        "cree_le": ligne["cree_le"],
        "libelle": ligne["libelle"],
        "filtres": json.loads(ligne["filtres"]),
        "conseils": json.loads(ligne["payload"]),
    }


def delete_coupon(identifiant: int) -> bool:
    """Supprime un coupon. Rend False s'il n'existait pas.

    Un coupon se supprime, contrairement a une prevision : il n'engage que son
    auteur, et une liste de brouillons qu'on ne peut pas nettoyer finit par
    cacher ceux qui comptent.
    """
    with connect() as connection:
        curseur = connection.execute(
            "DELETE FROM coupons WHERE id = ?", (identifiant,)
        )
    return curseur.rowcount > 0


def _summary(row: sqlite3.Row, offers: dict[int, dict[str, int]]) -> dict[str, Any]:
    counts = offers.get(row["id"], {"total": 0, "tranchees": 0, "reussies": 0})
    return {
        "match_id": row["match_id"],
        "emis_le": row["emis_le"],
        "match": row["libelle"],
        "competition": row["competition"],
        "coup_denvoi_local": row["coup_denvoi_local"],
        "statut_a_l_emission": row["statut_a_l_emission"],
        "url": row["url"],
        "reglage": json.loads(row["reglage"]),
        "resultat_reel": json.loads(row["resultat_reel"]) if row["resultat_reel"] else None,
        "offres": counts,
    }


def _offer_counts(connection: sqlite3.Connection) -> dict[int, dict[str, int]]:
    """Compte des propositions par prevision : total, tranchees, reussies."""
    rows = connection.execute(
        """
        SELECT prediction_id,
               COUNT(*)                                   AS total,
               SUM(verifie IS NOT NULL)                   AS tranchees,
               SUM(COALESCE(verifie, 0))                  AS reussies
          FROM offres
         GROUP BY prediction_id
        """
    ).fetchall()
    return {
        row["prediction_id"]: {
            "total": row["total"],
            "tranchees": row["tranchees"] or 0,
            "reussies": row["reussies"] or 0,
        }
        for row in rows
    }


def all_predictions(limit: int = 200) -> list[dict[str, Any]]:
    """Previsions, du coup d'envoi le plus recent au plus ancien."""
    with connect() as connection:
        counts = _offer_counts(connection)
        rows = connection.execute(
            "SELECT * FROM predictions ORDER BY coup_denvoi_local DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_summary(row, counts) for row in rows]


def get(match_id: str) -> dict[str, Any] | None:
    """Prevision complete d'un match, telle qu'elle a ete emise."""
    with connect() as connection:
        row = connection.execute(
            "SELECT payload, resultat_reel FROM predictions WHERE match_id = ?",
            (match_id,),
        ).fetchone()
    if row is None:
        return None
    payload: dict[str, Any] = json.loads(row["payload"])
    # Le resultat reel est ecrit apres coup : la colonne fait foi sur le payload,
    # qui date de l'emission.
    payload["resultat_reel"] = json.loads(row["resultat_reel"]) if row["resultat_reel"] else None
    return payload


def pending() -> list[dict[str, Any]]:
    """Fiches completes dont le match n'a pas encore ete tranche.

    C'est l'entree de `verify` : tant qu'une fiche n'a pas de resultat reel,
    elle attend la fin de son match. Une fois tranchee, elle n'est plus jamais
    reexaminee -- un resultat ne change pas.
    """
    with connect() as connection:
        rows = connection.execute(
            "SELECT payload FROM predictions WHERE resultat_reel IS NULL"
            " ORDER BY coup_denvoi_local"
        ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def tally() -> dict[str, Any]:
    """Bilan global : combien de propositions tranchees, combien realisees.

    Le taux n'est calcule que sur les propositions **tranchees**. Rapporter les
    reussites au total ferait passer une prevision en attente pour un echec.
    """
    with connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*)                  AS total,
                   SUM(verifie IS NOT NULL)  AS tranchees,
                   SUM(COALESCE(verifie, 0)) AS reussies
              FROM offres
            """
        ).fetchone()
        predictions = connection.execute(
            "SELECT COUNT(*) AS n, SUM(resultat_reel IS NOT NULL) AS verifiees FROM predictions"
        ).fetchone()

    settled = row["tranchees"] or 0
    won = row["reussies"] or 0
    return {
        "previsions": predictions["n"] or 0,
        "previsions_verifiees": predictions["verifiees"] or 0,
        "offres": row["total"] or 0,
        "offres_tranchees": settled,
        "offres_reussies": won,
        "taux_reussite": (won / settled) if settled else None,
    }


def import_legacy(path: Path = LEGACY_RECORD) -> int:
    """Importe le fichier de previsions anterieur a la base. Idempotent.

    Reimporter n'ajoute rien : `save` remplace la prevision du meme match.
    """
    if not path.is_file():
        return 0
    entries = json.loads(path.read_text(encoding="utf-8"))
    imported = 0
    for entry in entries:
        try:
            save(entry)
            imported += 1
        except ValueError:
            continue
    return imported


if __name__ == "__main__":  # importe a la main : python store.py
    init()
    count = import_legacy()
    reprise = reprendre_le_cache()
    print("Base : %s" % DB_PATH)
    print("Previsions importees depuis le fichier historique : %d" % count)
    print(
        "Resultats repris du cache : %d matchs, dont %d avec statistiques"
        % (reprise["matchs"], reprise["stats"])
    )
    print("Resultats archives au total : %d" % resultats_archives())
    print("Bilan :", tally())
