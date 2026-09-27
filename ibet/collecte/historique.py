"""Verse l'historique long des grands championnats (football-data.co.uk).

Flashscore ne publie que quelques saisons de statistiques, et le moteur ne lit
que les derniers matchs de chaque equipe. football-data.co.uk tient, depuis le
debut des annees 2000, un CSV par championnat et par saison : score, tirs,
tirs cadres, corners, cartons, arbitre (Angleterre) et les cotes du marche --
1X2 et plus / moins de 2,5 buts, a l'ouverture et a la cloture. Gratuit, sans
cle, publie pour cet usage.

Deux usages, mesures par `ibet/evaluation/mesure_historique.py` :
  - la profondeur : ce qu'une equipe fait sur plusieurs saisons ;
  - le marche : ce que les cotes disent du match qui vient.

Le script est INCREMENTAL : les saisons terminees deja versees sont sautees ;
la saison en cours est relue a chaque passage.

Usage :
    python -m ibet historique                  # 2012-13 a aujourd'hui
    python -m ibet historique --depuis 2018
    python -m ibet historique --liste
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from datetime import date, datetime

from ibet.sources import api_client
from ibet.stockage import store

URL = "https://www.football-data.co.uk/mmz4281/%s/%s.csv"

# Cle de CHAMPIONNATS_STYLES -> code de la source.
CODES = {
    "angleterre": "E0",
    "espagne": "SP1",
    "italie": "I1",
    "allemagne": "D1",
    "france": "F1",
    "portugal": "P1",
    "pays-bas": "N1",
}

# Cotes : la moyenne du marche d'abord, puis l'ancien agregat de BetBrain,
# puis un operateur, selon ce que la saison publie.
_COTES = {
    "cote_dom": ("AvgH", "BbAvH", "B365H"),
    "cote_nul": ("AvgD", "BbAvD", "B365D"),
    "cote_ext": ("AvgA", "BbAvA", "B365A"),
    "cote_plus25": ("Avg>2.5", "BbAv>2.5", "B365>2.5"),
    "cote_moins25": ("Avg<2.5", "BbAv<2.5", "B365<2.5"),
    "cloture_dom": ("AvgCH", "PSCH", "B365CH"),
    "cloture_nul": ("AvgCD", "PSCD", "B365CD"),
    "cloture_ext": ("AvgCA", "PSCA", "B365CA"),
}
_STATS = {
    "buts_dom": "FTHG", "buts_ext": "FTAG", "tirs_dom": "HS", "tirs_ext": "AS",
    "cadres_dom": "HST", "cadres_ext": "AST", "corners_dom": "HC", "corners_ext": "AC",
    "jaunes_dom": "HY", "jaunes_ext": "AY",
}


def saison_courante(jour: date | None = None) -> int:
    """Annee de debut de la saison en cours."""
    jour = jour or date.today()
    return jour.year if jour.month >= 7 else jour.year - 1


def _code_saison(debut: int) -> str:
    return "%02d%02d" % (debut % 100, (debut + 1) % 100)


def _entier(v: str) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _reel(v: str) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x > 1.0 else None


def _date(v: str) -> str:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(v.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def lire_saison(cle: str, debut: int) -> list[dict]:
    """Matchs d'une saison, tels que la source les publie. [] si absente."""
    try:
        brut = api_client._http_get(URL % (_code_saison(debut), CODES[cle]), {}, {}).content
    except api_client.ApiError:
        return []
    texte = brut.decode("utf-8-sig", errors="replace")
    matchs = []
    for ligne in csv.DictReader(io.StringIO(texte)):
        jour = _date(ligne.get("Date") or "")
        dom, ext = (ligne.get("HomeTeam") or "").strip(), (ligne.get("AwayTeam") or "").strip()
        if not jour or not dom or not ext:
            continue
        m = {"championnat": api_client.CHAMPIONNATS_STYLES[cle][2],
             "saison": "%d-%d" % (debut, debut + 1), "date": jour,
             "domicile": dom, "exterieur": ext,
             "arbitre": (ligne.get("Referee") or "").strip()}
        for champ, col in _STATS.items():
            m[champ] = _entier(ligne.get(col))
        for champ, cols in _COTES.items():
            m[champ] = next((x for x in (_reel(ligne.get(c)) for c in cols) if x), None)
        matchs.append(m)
    return matchs


def verser(matchs: list[dict]) -> int:
    if not matchs:
        return 0
    colonnes = list(matchs[0])
    requete = "INSERT OR REPLACE INTO historique (%s) VALUES (%s)" % (
        ", ".join(colonnes), ", ".join("?" * len(colonnes)))
    with store.connect() as cx:
        cx.executemany(requete, [tuple(m[c] for c in colonnes) for m in matchs])
    return len(matchs)


def historique(championnat: str = "") -> list[dict]:
    """Matchs verses, des plus anciens aux plus recents."""
    requete = "SELECT * FROM historique"
    parametres: tuple = ()
    if championnat:
        requete += " WHERE championnat = ?"
        parametres = (championnat,)
    with store.connect() as cx:
        return [dict(r) for r in cx.execute(requete + " ORDER BY date, championnat, domicile",
                                            parametres)]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--depuis", type=int, default=2012, help="premiere saison (annee de debut)")
    parser.add_argument("--championnats", nargs="*", default=list(CODES), choices=list(CODES))
    parser.add_argument("--liste", action="store_true")
    args = parser.parse_args(argv)
    store.init()

    with store.connect() as cx:
        deja = {(r[0], r[1]): r[2] for r in cx.execute(
            "SELECT championnat, saison, COUNT(*) FROM historique GROUP BY 1, 2")}
    if args.liste:
        for cle in CODES:
            nom = api_client.CHAMPIONNATS_STYLES[cle][2]
            saisons = sorted((s, n) for (c, s), n in deja.items() if c == nom)
            print("  %-15s %s" % (nom, "  ".join("%s:%d" % sn for sn in saisons) or "rien"))
        return 0

    courante = saison_courante()
    total = 0
    for cle in args.championnats:
        nom = api_client.CHAMPIONNATS_STYLES[cle][2]
        for debut in range(args.depuis, courante + 1):
            saison = "%d-%d" % (debut, debut + 1)
            if debut < courante and deja.get((nom, saison), 0) >= 250:
                continue
            n = verser(lire_saison(cle, debut))
            total += n
            print("  %-15s %s : %d matchs" % (nom, saison, n), flush=True)
    print("Termine : %d matchs verses." % total)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
