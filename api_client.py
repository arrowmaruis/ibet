"""Recuperation des matchs de football.

Quatre providers interchangeables, tous avec une offre gratuite :

  - flashscore    : aucune inscription, couverture mondiale, +/- 7 jours
  - thesportsdb   : aucune inscription (cle de test publique "123")
  - football-data : gratuit non commercial, 12 grandes competitions
  - api-football  : gratuit 100 req/jour, couverture mondiale

Chaque provider normalise sa reponse vers le meme dictionnaire, ce qui permet
a l'affichage et a l'export d'ignorer completement la source des donnees.

Les trois derniers sont des API REST publiques. Flashscore, lui, est lu via le
flux que le site utilise pour sa propre page d'accueil : ce n'est pas une API
publiee, elle peut changer sans preavis, et les CGU du site interdisent la
reutilisation commerciale des donnees. A reserver a un usage personnel, avec un
volume de requetes raisonnable.
"""

from __future__ import annotations

import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from dotenv import load_dotenv

import cache

# Ce module lit CA_BUNDLE, CACHE_TTL et les cles d'API dans l'environnement :
# il charge donc .env lui-meme, sans dependre de son appelant. Sans cela, tout
# programme autre que main.py (backtest, tests, script ponctuel) partait sans
# bundle de certificats et echouait en SSL. load_dotenv n'ecrase jamais une
# variable deja definie et peut etre appele plusieurs fois.
load_dotenv()

USER_AGENT = "match-fetcher/1.0 (usage personnel)"
TIMEOUT = 15

# Reprise sur erreur de transport : voir _http_get.
HTTP_ATTEMPTS = 3
HTTP_BACKOFF = 0.5

# Version du schema des dictionnaires mis en cache. A incrementer des qu'un champ
# est ajoute, renomme ou calcule autrement : les entrees ecrites par une version
# anterieure deviennent alors inatteignables au lieu d'etre relues telles quelles.
# Sans cela, un champ nouvellement ajoute ressort vide pendant toute la duree du
# TTL (1 h pour les matchs, 30 jours pour les statistiques), silencieusement.
CACHE_SCHEMA = 7

# Statuts normalises
SCHEDULED = "A venir"
LIVE = "En cours"
FINISHED = "Termine"
POSTPONED = "Reporte"
UNKNOWN = "Inconnu"


class ApiError(RuntimeError):
    """Erreur fonctionnelle destinee a etre affichee telle quelle a l'utilisateur."""


class MissingKeyError(ApiError):
    pass


class QuotaError(ApiError):
    pass


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ApiError(
            "Fuseau horaire inconnu : " + repr(name) + ". "
            "Utilisez un identifiant IANA (ex: Europe/Paris). "
            "Sous Windows, installez le paquet 'tzdata'."
        ) from None


def _local_time(utc_iso: str | None, tz_name: str) -> tuple[str, str]:
    """Convertit un timestamp UTC ISO en (date locale YYYY-MM-DD, heure HH:MM)."""
    if not utc_iso:
        return "", ""
    raw = utc_iso.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return "", ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(_tz(tz_name))
    return local.strftime("%Y-%m-%d"), local.strftime("%H:%M")


def _utc_iso(timestamp: int | None) -> str:
    """Horodatage unix -> ISO UTC, chaine vide si la valeur est inexploitable.

    Le flux porte parfois un horodatage aberrant (zero, negatif, ou tres au-dela
    de l'epoque representable). Sous Windows, `fromtimestamp` leve alors OSError
    plutot que de rendre une date fausse : sans garde, une seule vieille
    confrontation faisait echouer toute une evaluation.
    """
    if timestamp is None:
        return ""
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return ""


def _int_or_none(value: Any) -> int | None:
    if value in (None, "", "null"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _match(
    *,
    provider: str,
    match_id: Any,
    kickoff_utc: str | None,
    league: str,
    country: str,
    home: str,
    away: str,
    status: str,
    score_home: Any = None,
    score_away: Any = None,
    tz_name: str = "Europe/Paris",
    url: str = "",
) -> dict[str, Any]:
    local_date, local_hour = _local_time(kickoff_utc, tz_name)
    return {
        "url": url,
        "provider": provider,
        "match_id": str(match_id) if match_id is not None else "",
        "date": local_date,
        "heure": local_hour,
        "kickoff_utc": kickoff_utc or "",
        "championnat": league or "",
        "pays": country or "",
        "domicile": home or "",
        "exterieur": away or "",
        "statut": status,
        "score_domicile": score_home,
        "score_exterieur": score_away,
    }


def _verify() -> str | bool:
    """Bundle de certificats a utiliser (voir CA_BUNDLE et setup_ca.py)."""
    bundle = os.getenv("CA_BUNDLE", "").strip().strip('"')
    if not bundle:
        return True
    if not os.path.isfile(bundle):
        raise ApiError(
            "CA_BUNDLE pointe vers un fichier introuvable : %s\n"
            "Relancez `python setup_ca.py` ou videz la variable dans .env." % bundle
        )
    return bundle


def _http_get(
    url: str, headers: dict[str, str], params: dict[str, Any]
) -> requests.Response:
    """GET brut, avec traduction des erreurs reseau/HTTP en `ApiError` lisibles.

    Les erreurs de transport (TLS, connexion, delai) sont reessayees : sous un
    antivirus qui inspecte le HTTPS, une poignee de requetes sur plusieurs
    centaines echoue au hasard alors que la suivante passe. Sans reprise, une
    longue serie -- l'evaluation du modele en particulier -- perd des matchs
    sans raison. Les erreurs HTTP ne sont pas reessayees : un 403 le restera.
    """
    all_headers = {"User-Agent": USER_AGENT}
    all_headers.update(headers)

    last: Exception | None = None
    for attempt in range(HTTP_ATTEMPTS):
        try:
            response = requests.get(
                url, headers=all_headers, params=params,
                timeout=TIMEOUT, verify=_verify(),
            )
            break
        except (
            requests.Timeout,
            requests.exceptions.SSLError,
            requests.exceptions.ConnectionError,
        ) as exc:
            last = exc
            if attempt + 1 < HTTP_ATTEMPTS:
                time.sleep(HTTP_BACKOFF * (attempt + 1))
        except requests.RequestException as exc:
            raise ApiError("Echec reseau : %s" % exc) from None
    else:
        if isinstance(last, requests.Timeout):
            raise ApiError(
                "Delai depasse (%ss) en contactant %s, apres %d tentatives."
                % (TIMEOUT, url, HTTP_ATTEMPTS)
            ) from None
        if isinstance(last, requests.exceptions.SSLError):
            raise ApiError(
                "Verification du certificat HTTPS impossible pour %s, apres %d "
                "tentatives.\n"
                "Cause frequente : un antivirus ou un proxy inspecte le trafic "
                "HTTPS.\n"
                "Correctif : lancez `python setup_ca.py` puis renseignez CA_BUNDLE "
                "dans le fichier .env.\nDetail : %s"
                % (url, HTTP_ATTEMPTS, str(last)[:200])
            ) from None
        raise ApiError(
            "Echec reseau apres %d tentatives : %s" % (HTTP_ATTEMPTS, last)
        ) from None

    if response.status_code in (401, 403):
        raise MissingKeyError(
            "Cle API refusee (HTTP %s). Verifiez sa valeur dans le fichier .env."
            % response.status_code
        )
    if response.status_code == 429:
        raise QuotaError(
            "Quota ou cadence depasse (HTTP 429). Patientez avant de reessayer ; "
            "le cache local evite de rappeler l'API pour une date deja consultee."
        )
    if response.status_code >= 400:
        raise ApiError(
            "HTTP %s depuis %s : %s" % (response.status_code, url, response.text[:200])
        )
    return response


def _request(url: str, headers: dict[str, str], params: dict[str, Any]) -> dict:
    response = _http_get(url, headers, params)
    try:
        return response.json()
    except ValueError:
        raise ApiError("Reponse non-JSON depuis %s" % url) from None


def _request_text(url: str, headers: dict[str, str]) -> str:
    """Corps de reponse en texte UTF-8 (flux Flashscore, qui n'est pas du JSON)."""
    response = _http_get(url, headers, {})
    # Flashscore ne declare pas de charset : requests retomberait sur latin-1 et
    # abimerait les noms de clubs accentues.
    response.encoding = "utf-8"
    return response.text


# ---------------------------------------------------------------------------
# Provider : TheSportsDB (fonctionne sans cle)
# ---------------------------------------------------------------------------

_TSDB_STATUS = {
    "match finished": FINISHED,
    "ft": FINISHED,
    "aet": FINISHED,
    "pen": FINISHED,
    "not started": SCHEDULED,
    "ns": SCHEDULED,
    "postponed": POSTPONED,
    "pst": POSTPONED,
    "cancelled": POSTPONED,
    "canc": POSTPONED,
}


def _tsdb_key() -> str:
    return os.getenv("THESPORTSDB_KEY", "123").strip() or "123"


def _tsdb_url(endpoint: str) -> str:
    return "https://www.thesportsdb.com/api/v1/json/%s/%s" % (_tsdb_key(), endpoint)


def _tsdb_match(event: dict[str, Any], tz_name: str) -> dict[str, Any]:
    raw_status = (event.get("strStatus") or "").strip().lower()
    if raw_status in _TSDB_STATUS:
        status = _TSDB_STATUS[raw_status]
    elif raw_status:
        status = LIVE  # ex: "1H", "HT", "63" -> minute de jeu en cours
    else:
        status = SCHEDULED

    kickoff = event.get("strTimestamp")
    if not kickoff and event.get("dateEvent"):
        # Certains endpoints ne renseignent pas strTimestamp : on recompose.
        kickoff = "%sT%s" % (event["dateEvent"], event.get("strTime") or "00:00:00")

    match = _match(
        provider="thesportsdb",
        match_id=event.get("idEvent"),
        kickoff_utc=kickoff,
        league=event.get("strLeague") or "",
        country=event.get("strCountry") or "",
        home=event.get("strHomeTeam") or "",
        away=event.get("strAwayTeam") or "",
        status=status,
        score_home=_int_or_none(event.get("intHomeScore")),
        score_away=_int_or_none(event.get("intAwayScore")),
        tz_name=tz_name,
    )
    match["journee"] = event.get("intRound") or ""
    # TheSportsDB expose l'identifiant API-Football du meme match : c'est le
    # pont qui permet d'aller chercher les statistiques detaillees (corners,
    # fautes, cartons, arbitre) que TheSportsDB ne fournit pas en gratuit.
    match["api_football_id"] = str(event.get("idAPIfootball") or "")
    # Repli sur la date brute de l'API si le fuseau n'a pas pu etre applique.
    if not match["date"]:
        match["date"] = event.get("dateEvent") or ""
    return match


def _fetch_thesportsdb(date: str, tz_name: str) -> list[dict[str, Any]]:
    data = _request(_tsdb_url("eventsday.php"), {}, {"d": date, "s": "Soccer"})
    return [_tsdb_match(event, tz_name) for event in data.get("events") or []]


# ---------------------------------------------------------------------------
# TheSportsDB : mode "journee de championnat"
#
# La cle publique gratuite plafonne `eventsday.php` a un echantillon de 3
# matchs. En revanche `eventsround.php` renvoie une journee complete. Pour les
# grandes competitions europeennes on passe donc par les journees, puis on
# filtre sur la date demandee.
# ---------------------------------------------------------------------------

# Identifiants TheSportsDB des competitions couvertes par le mode journee.
TSDB_LEAGUES: dict[str, tuple[int, str]] = {
    "premier league": (4328, "English Premier League"),
    "ligue 1": (4334, "French Ligue 1"),
    "la liga": (4335, "Spanish La Liga"),
    "serie a": (4332, "Italian Serie A"),
    "bundesliga": (4331, "German Bundesliga"),
    "champions league": (4480, "UEFA Champions League"),
}

# Alias acceptes en ligne de commande.
TSDB_ALIASES = {
    "epl": "premier league",
    "angleterre": "premier league",
    "england": "premier league",
    "l1": "ligue 1",
    "france": "ligue 1",
    "liga": "la liga",
    "espagne": "la liga",
    "spain": "la liga",
    "italie": "serie a",
    "italy": "serie a",
    "allemagne": "bundesliga",
    "germany": "bundesliga",
    "ldc": "champions league",
    "c1": "champions league",
    "ucl": "champions league",
}

ROUND_WINDOW = 2  # journees explorees de part et d'autre de celle trouvee
MAX_ROUND = 60    # borne haute de la recherche dichotomique (38 journees + barrages)


def resolve_tsdb_league(name: str) -> tuple[int, str] | None:
    """Associe un nom saisi par l'utilisateur a une competition connue."""
    needle = " ".join(name.strip().lower().split())
    needle = TSDB_ALIASES.get(needle, needle)
    if needle in TSDB_LEAGUES:
        return TSDB_LEAGUES[needle]
    for canonical, value in TSDB_LEAGUES.items():
        if needle in canonical or needle in value[1].lower():
            return value
    return None


def season_for(date: str) -> str:
    """Saison europeenne au format TheSportsDB : 2026-09-12 -> '2026-2027'."""
    day = datetime.strptime(date, "%Y-%m-%d").date()
    start = day.year if day.month >= 7 else day.year - 1
    return "%d-%d" % (start, start + 1)


def _fetch_round(
    league_id: int, season: str, round_no: int, tz_name: str, use_cache: bool = True
) -> list[dict[str, Any]]:
    """Une journee de championnat, mise en cache individuellement."""
    cache_key = "v%d|tsdb-round|%d|%s|%d|%s" % (
        CACHE_SCHEMA, league_id, season, round_no, tz_name
    )
    if use_cache:
        cached = cache.get(cache_key, _cache_ttl())
        if cached is not None:
            return cached

    data = _request(
        _tsdb_url("eventsround.php"),
        {},
        {"id": league_id, "r": round_no, "s": season},
    )
    matches = [_tsdb_match(event, tz_name) for event in data.get("events") or []]
    cache.set(cache_key, matches)
    return matches


def _round_last_date(
    league_id: int, season: str, round_no: int, tz_name: str, use_cache: bool
) -> str | None:
    """Derniere date jouee d'une journee, ou None si la journee n'existe pas."""
    matches = _fetch_round(league_id, season, round_no, tz_name, use_cache)
    dates = [m["date"] for m in matches if m["date"]]
    return max(dates) if dates else None


def _find_round(
    league_id: int, season: str, date: str, tz_name: str, use_cache: bool = True
) -> int:
    """Premiere journee dont la derniere date est >= `date`, par dichotomie.

    Une estimation par semaines calendaires serait plus simple mais fausse des
    que la competition n'a pas une cadence hebdomadaire : en Ligue des
    champions les tours preliminaires (journees 1 a 3) s'etalent sur des mois
    et se chevauchent. La date de fin de journee, elle, reste croissante avec
    le numero de journee dans toutes les competitions couvertes.
    """
    lo, hi = 1, MAX_ROUND
    found = 1
    while lo <= hi:
        mid = (lo + hi) // 2
        last = _round_last_date(league_id, season, mid, tz_name, use_cache)
        if last is None:  # journee inexistante : la saison s'arrete avant
            hi = mid - 1
        elif last < date:  # journee entierement passee : chercher plus loin
            lo = mid + 1
        else:
            found = mid
            hi = mid - 1
    return found


def fetch_league_rounds(
    league_id: int,
    date: str,
    tz_name: str,
    window: int = ROUND_WINDOW,
    round_no: int | None = None,
    use_cache: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Retourne (matchs de la date demandee, tous les matchs des journees lues).

    Le second element sert a proposer une journee proche quand la date exacte
    ne contient aucun match.
    """
    season = season_for(date)

    if round_no is not None:
        collected = _fetch_round(league_id, season, round_no, tz_name, use_cache)
    else:
        found = _find_round(league_id, season, date, tz_name, use_cache)
        collected = []
        seen: set[str] = set()
        for candidate in range(max(1, found - window), found + window + 1):
            for match in _fetch_round(league_id, season, candidate, tz_name, use_cache):
                if match["match_id"] not in seen:
                    seen.add(match["match_id"])
                    collected.append(match)

    on_date = [m for m in collected if m["date"] == date]
    return sort_matches(on_date), sort_matches(collected)


# ---------------------------------------------------------------------------
# Provider : football-data.org
# ---------------------------------------------------------------------------

_FD_STATUS = {
    "SCHEDULED": SCHEDULED,
    "TIMED": SCHEDULED,
    "IN_PLAY": LIVE,
    "PAUSED": LIVE,
    "FINISHED": FINISHED,
    "POSTPONED": POSTPONED,
    "SUSPENDED": POSTPONED,
    "CANCELLED": POSTPONED,
}


def _fetch_football_data(date: str, tz_name: str) -> list[dict[str, Any]]:
    key = os.getenv("FOOTBALL_DATA_KEY", "").strip()
    if not key:
        raise MissingKeyError(
            "FOOTBALL_DATA_KEY absente. Creez une cle gratuite sur "
            "https://www.football-data.org/client/register puis renseignez .env"
        )
    data = _request(
        "https://api.football-data.org/v4/matches",
        {"X-Auth-Token": key},
        {"dateFrom": date, "dateTo": date},
    )
    matches = []
    for item in data.get("matches") or []:
        full_time = (item.get("score") or {}).get("fullTime") or {}
        competition = item.get("competition") or {}
        matches.append(
            _match(
                provider="football-data",
                match_id=item.get("id"),
                kickoff_utc=item.get("utcDate"),
                league=competition.get("name") or "",
                country=(competition.get("area") or {}).get("name") or "",
                home=(item.get("homeTeam") or {}).get("name") or "",
                away=(item.get("awayTeam") or {}).get("name") or "",
                status=_FD_STATUS.get(item.get("status") or "", UNKNOWN),
                score_home=full_time.get("home"),
                score_away=full_time.get("away"),
                tz_name=tz_name,
            )
        )
    return matches


# ---------------------------------------------------------------------------
# Provider : API-Football (api-sports.io)
# ---------------------------------------------------------------------------

_AF_STATUS = {
    "TBD": SCHEDULED,
    "NS": SCHEDULED,
    "1H": LIVE,
    "HT": LIVE,
    "2H": LIVE,
    "ET": LIVE,
    "BT": LIVE,
    "P": LIVE,
    "LIVE": LIVE,
    "FT": FINISHED,
    "AET": FINISHED,
    "PEN": FINISHED,
    "AWD": FINISHED,
    "WO": FINISHED,
    "PST": POSTPONED,
    "CANC": POSTPONED,
    "ABD": POSTPONED,
    "SUSP": POSTPONED,
    "INT": POSTPONED,
}


def _fetch_api_football(date: str, tz_name: str) -> list[dict[str, Any]]:
    key = os.getenv("API_FOOTBALL_KEY", "").strip()
    if not key:
        raise MissingKeyError(
            "API_FOOTBALL_KEY absente. Creez une cle gratuite sur "
            "https://dashboard.api-football.com/register puis renseignez .env"
        )
    data = _request(
        "https://v3.football.api-sports.io/fixtures",
        {"x-apisports-key": key},
        {"date": date},
    )

    # API-Football repond HTTP 200 meme sur erreur applicative (cle, quota...).
    errors = data.get("errors")
    if errors:
        detail = errors if isinstance(errors, list) else list(errors.values())
        text = "; ".join(str(item) for item in detail)
        if "limit" in text.lower() or "quota" in text.lower():
            raise QuotaError("Quota API-Football atteint : %s" % text)
        raise ApiError("API-Football a renvoye une erreur : %s" % text)

    matches = []
    for item in data.get("response") or []:
        fixture = item.get("fixture") or {}
        league = item.get("league") or {}
        teams = item.get("teams") or {}
        goals = item.get("goals") or {}
        short = ((fixture.get("status") or {}).get("short") or "").upper()
        matches.append(
            _match(
                provider="api-football",
                match_id=fixture.get("id"),
                kickoff_utc=fixture.get("date"),
                league=league.get("name") or "",
                country=league.get("country") or "",
                home=(teams.get("home") or {}).get("name") or "",
                away=(teams.get("away") or {}).get("name") or "",
                status=_AF_STATUS.get(short, UNKNOWN),
                score_home=goals.get("home"),
                score_away=goals.get("away"),
                tz_name=tz_name,
            )
        )
    return matches


# ---------------------------------------------------------------------------
# Provider : Flashscore (flashscore.fr, aucune cle, couverture mondiale)
# ---------------------------------------------------------------------------

# Le site consomme ses propres donnees via un flux texte delimite plutot que du
# JSON. Un enregistrement est `CLE<FS_KV>valeur`, les enregistrements sont separes
# par <FS_FIELD> et les blocs (un tournoi, puis ses matchs) par <FS_BLOCK>.
FS_BLOCK = "~"
FS_FIELD = "¬"  # ¬
FS_KV = "÷"  # ÷

# 16 = flashscore.fr (libelles et noms de competitions en francais). Chaque
# edition nationale a son propre identifiant, lisible dans le HTML de la page
# d'accueil sous la cle "projectId".
FS_PROJECT = 16
FS_HOST = "https://%d.flashscore.ninja/%d/x/feed/" % (FS_PROJECT, FS_PROJECT)
FS_REFERER = "https://www.flashscore.fr/"

# Page publique d'un match. Cette forme courte redirige vers l'URL canonique
# (avec les slugs des deux equipes), ce qui evite d'avoir a la reconstruire.
FS_MATCH_URL = "https://www.flashscore.fr/match/%s/"
FS_SPORT = 1  # football

# Jeton constant attendu par le flux. Surchargeable par .env au cas ou il change.
FS_DEFAULT_SIGN = "SW9D1eZo"

# Le flux ne s'adresse que par decalage en jours autour d'aujourd'hui.
FS_MAX_OFFSET = 7

# Statut detaille (champ AC), plus precis que le statut global AB.
_FS_STATUS = {
    "1": SCHEDULED,
    "2": LIVE,
    "3": FINISHED,
    "4": POSTPONED,   # reporte : aucun score
    "5": POSTPONED,   # annule : aucun score
    "9": FINISHED,    # forfait
    "10": FINISHED,   # apres prolongation
    "11": FINISHED,   # apres tirs au but
    "12": LIVE,       # 1re mi-temps
    "13": LIVE,       # 2e mi-temps
    "36": POSTPONED,  # interrompu : score partiel, pas de vainqueur
    "38": LIVE,       # prolongation en cours
    "42": LIVE,       # tirs au but en cours
    "43": LIVE,       # mi-temps
    "54": FINISHED,   # arrete, resultat homologue
}

# Repli sur le statut global si le code detaille est inconnu.
_FS_STATUS_COARSE = {"1": SCHEDULED, "2": LIVE, "3": FINISHED}


def _fs_sign() -> str:
    return os.getenv("FLASHSCORE_FSIGN", "").strip() or FS_DEFAULT_SIGN


def _fs_get(endpoint: str) -> str:
    return _request_text(
        FS_HOST + endpoint,
        {"x-fsign": _fs_sign(), "Referer": FS_REFERER},
    )


def _fs_blocks(payload: str) -> Iterable[dict[str, str]]:
    """Decoupe le flux en blocs, chacun rendu sous forme de dict cle -> valeur."""
    for block in payload.split(FS_BLOCK):
        fields = {}
        for record in block.split(FS_FIELD):
            key, sep, value = record.partition(FS_KV)
            if sep:
                fields[key] = value
        if fields:
            yield fields


def _fs_status(block: dict[str, str]) -> str:
    detailed = _FS_STATUS.get(block.get("AC", ""))
    if detailed:
        return detailed
    return _FS_STATUS_COARSE.get(block.get("AB", ""), UNKNOWN)


def _fs_tournament(block: dict[str, str]) -> tuple[str, str]:
    """(championnat, pays) depuis un bloc tournoi.

    `ZA` vaut "PAYS: Competition - Etape" et `ZY` le pays seul ; on retire le
    prefixe pour ne pas repeter le pays dans le nom du championnat.
    """
    country = block.get("ZY", "").strip()
    label = block.get("ZA", "").strip()
    head, sep, rest = label.partition(": ")
    if sep and head == head.upper():
        return rest.strip(), country or head.title()
    return label, country


def _fs_day_offset(date: str, tz_name: str) -> int:
    today = datetime.now(_tz(tz_name)).date()
    target = datetime.strptime(date, "%Y-%m-%d").date()
    offset = (target - today).days
    if abs(offset) > FS_MAX_OFFSET:
        raise ApiError(
            "Flashscore ne publie que %s jours autour d'aujourd'hui (du %s au %s) ; "
            "%s est hors de cette plage. Utilisez --provider football-data ou "
            "api-football pour une date plus lointaine."
            % (
                FS_MAX_OFFSET,
                (today - timedelta(days=FS_MAX_OFFSET)).isoformat(),
                (today + timedelta(days=FS_MAX_OFFSET)).isoformat(),
                date,
            )
        )
    return offset


def _fs_utc_offset_hours(tz_name: str) -> int:
    """Decalage horaire en heures entieres, tel que l'attend le flux.

    Le flux decoupe ses journees a minuit dans ce fuseau. Les fuseaux a la
    demi-heure sont arrondis : le flux deborde d'environ une heure de chaque
    cote de la journee, et le filtrage par date locale plus bas rattrape l'ecart.
    """
    offset = datetime.now(_tz(tz_name)).utcoffset()
    return round(offset.total_seconds() / 3600) if offset else 0


def _fetch_flashscore(date: str, tz_name: str) -> list[dict[str, Any]]:
    day = _fs_day_offset(date, tz_name)
    endpoint = "f_%d_%d_%d_fr_1" % (FS_SPORT, day, _fs_utc_offset_hours(tz_name))
    payload = _fs_get(endpoint)

    if not payload.strip():
        raise ApiError(
            "Flashscore a renvoye un flux vide pour %s. Le jeton x-fsign a "
            "probablement change : relevez sa valeur dans les requetes de "
            "https://www.flashscore.fr/ et renseignez FLASHSCORE_FSIGN dans .env." % date
        )

    matches: list[dict[str, Any]] = []
    league = country = ""
    for block in _fs_blocks(payload):
        if "ZA" in block:
            league, country = _fs_tournament(block)
            continue
        if "AA" not in block:
            continue

        kickoff = _int_or_none(block.get("AD"))
        matches.append(
            _match(
                provider="flashscore",
                match_id=block["AA"],
                kickoff_utc=(
                    _utc_iso(kickoff) or None
                ),
                league=league,
                country=country,
                home=block.get("AE", ""),
                away=block.get("AF", ""),
                status=_fs_status(block),
                score_home=_int_or_none(block.get("AG")),
                score_away=_int_or_none(block.get("AH")),
                tz_name=tz_name,
                url=FS_MATCH_URL % block["AA"],
            )
        )

    # Le flux deborde sur la veille et le lendemain : on ne garde que la journee
    # demandee, calculee dans le fuseau de l'utilisateur.
    return [m for m in matches if m["date"] == date]


PROVIDERS = {
    "thesportsdb": _fetch_thesportsdb,
    "football-data": _fetch_football_data,
    "api-football": _fetch_api_football,
    "flashscore": _fetch_flashscore,
}


# ---------------------------------------------------------------------------
# Statistiques de match (matchs termines)
# ---------------------------------------------------------------------------

# Les deux API partagent le meme vocabulaire de statistiques : TheSportsDB
# reprend les libelles d'API-Football, dont il est alimente.
STAT_LABELS = {
    "corner kicks": "corners",
    "fouls": "fautes",
    "yellow cards": "cartons_jaunes",
    "red cards": "cartons_rouges",
    "shots on goal": "tirs_cadres",
    "total shots": "tirs_total",
    "ball possession": "possession",
}

# Ordre d'affichage et libelles lisibles.
STAT_ORDER = [
    ("tirs_cadres", "Tirs cadres"),
    ("tirs_total", "Tirs (total)"),
    ("corners", "Corners"),
    ("fautes", "Fautes"),
    ("cartons_jaunes", "Cartons jaunes"),
    ("cartons_rouges", "Cartons rouges"),
    ("possession", "Possession"),
]

# Statistiques collectees mais absentes de la fiche de match.
#
# Elles ne servent pas a decrire un match a un lecteur -- STAT_ORDER s'en
# charge -- mais a etablir le PROFIL d'une equipe : xG pour la qualite des
# occasions plutot que leur nombre, touches dans la surface et passes dans le
# dernier tiers pour la maniere de progresser, duels et tacles pour l'intensite
# defensive. `contexte.py` en tire le style de jeu et les buts attendus.
#
# Les ajouter a STAT_ORDER aurait allonge de treize lignes l'affichage de chaque
# match, pour des grandeurs que personne ne lit une par une. Le flux, lui, les
# livre dans la meme requete : les collecter ne coute rien de plus.
STAT_EXTRA = [
    ("xg", "Buts attendus (xG)"),
    ("xgot", "xG cadres (xGOT)"),
    ("grosses_occasions", "Grosses occasions"),
    ("tirs_surface", "Tirs dans la surface"),
    ("touches_surface", "Touches dans la surface adverse"),
    ("passes", "Passes reussies (%)"),
    ("passes_dernier_tiers", "Passes dans le dernier tiers (%)"),
    ("passes_longues", "Passes longues (%)"),
    ("centres", "Centres reussis (%)"),
    ("duels", "Duels remportes"),
    ("tacles", "Tacles reussis (%)"),
    ("hors_jeux", "Hors-jeu"),
    ("arrets", "Arrets du gardien"),
]

# Tout ce qui est collecte. `STAT_ORDER` reste ce qui est AFFICHE : la fiche de
# match, l'export et le classement continuent de ne connaitre que lui.
STAT_FIELDS = STAT_ORDER + STAT_EXTRA

# Un match termine ne change plus : inutile de re-interroger l'API.
STATS_TTL = 30 * 24 * 3600


class StatsUnavailable(ApiError):
    """La source ne peut pas fournir les statistiques demandees."""


def _blank_stats() -> dict[str, Any]:
    return {key: None for key, _ in STAT_FIELDS}


# Nombre de tete d'une valeur textuelle : "86% (450/526)" -> "86".
_LEADING_NUMBER = re.compile(r"^[+-]?\d+(?:[.,]\d+)?")


def stat_number(value: Any) -> float | None:
    """Valeur comparable d'une statistique. '51%' -> 51.0, sinon None.

    Le flux exprime les statistiques de reussite sous la forme
    "86% (450/526)" : le taux, puis le detail. C'est le taux qui compare deux
    equipes -- 450 passes reussies ne veut rien dire sans le nombre tente --,
    donc c'est le nombre de tete qui est retenu.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().rstrip("%").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        pass
    found = _LEADING_NUMBER.match(str(value).strip().replace(",", "."))
    return float(found.group(0)) if found else None


def _assign(bucket: dict[str, Any], raw_label: str, value: Any) -> None:
    field = STAT_LABELS.get((raw_label or "").strip().lower())
    if field is None:
        return
    if isinstance(value, str) and value.endswith("%"):
        bucket[field] = value
    else:
        parsed = _int_or_none(value)
        bucket[field] = parsed if parsed is not None else value


def _stats_from_api_football(fixture_id: str) -> dict[str, Any]:
    """Statistiques completes + arbitre, en une seule requete.

    `/fixtures?id=` renvoie le detail complet du match (arbitre, statistiques,
    evenements), la ou `/fixtures/statistics` ne donnerait que les stats.
    """
    key = os.getenv("API_FOOTBALL_KEY", "").strip()
    if not key:
        raise MissingKeyError(
            "Les statistiques detaillees (corners, fautes, cartons, arbitre) "
            "necessitent une cle API-Football gratuite.\n"
            "Creez-la sur https://dashboard.api-football.com/register puis "
            "renseignez API_FOOTBALL_KEY dans .env."
        )

    data = _request(
        "https://v3.football.api-sports.io/fixtures",
        {"x-apisports-key": key},
        {"id": fixture_id},
    )
    errors = data.get("errors")
    if errors:
        detail = errors if isinstance(errors, list) else list(errors.values())
        text = "; ".join(str(item) for item in detail)
        if "limit" in text.lower() or "quota" in text.lower():
            raise QuotaError("Quota API-Football atteint : %s" % text)
        raise ApiError("API-Football a renvoye une erreur : %s" % text)

    response = data.get("response") or []
    if not response:
        raise StatsUnavailable(
            "API-Football ne connait pas le match %s." % fixture_id
        )

    item = response[0]
    teams = item.get("teams") or {}
    home_name = (teams.get("home") or {}).get("name") or ""

    result = {
        "source": "api-football",
        "arbitre": (item.get("fixture") or {}).get("referee") or "",
        "domicile": _blank_stats(),
        "exterieur": _blank_stats(),
        "indisponible": [],
    }

    for block in item.get("statistics") or []:
        team_name = (block.get("team") or {}).get("name") or ""
        side = "domicile" if team_name == home_name else "exterieur"
        for entry in block.get("statistics") or []:
            _assign(result[side], entry.get("type"), entry.get("value"))

    return result


def _stats_from_thesportsdb(event_id: str) -> dict[str, Any]:
    """Statistiques partielles : la cle gratuite ne renvoie que les tirs.

    `lookupeventstats.php` est plafonne a 5 lignes, toutes liees aux tirs, et
    `lookupevent.php` ne comporte aucun champ arbitre. Les champs manquants
    sont signales explicitement plutot que laisses vides sans explication.
    """
    data = _request(_tsdb_url("lookupeventstats.php"), {}, {"id": event_id})
    rows = data.get("eventstats") or []
    if not rows:
        raise StatsUnavailable(
            "TheSportsDB n'a aucune statistique pour le match %s." % event_id
        )

    result = {
        "source": "thesportsdb",
        "arbitre": "",
        "domicile": _blank_stats(),
        "exterieur": _blank_stats(),
        "indisponible": [],
    }
    for row in rows:
        _assign(result["domicile"], row.get("strStat"), row.get("intHome"))
        _assign(result["exterieur"], row.get("strStat"), row.get("intAway"))

    result["indisponible"] = [
        field
        for field, _ in STAT_ORDER
        if result["domicile"][field] is None and result["exterieur"][field] is None
    ]
    return result


# Le flux de statistiques identifie chaque ligne par un code numerique stable
# (champ SD), independant de la langue du site, plus sur que le libelle.
FS_STAT_IDS = {
    "12": "possession",
    "13": "tirs_cadres",
    "16": "corners",
    "21": "fautes",
    "22": "cartons_rouges",
    "23": "cartons_jaunes",
    "34": "tirs_total",
    # Grandeurs de contexte (STAT_EXTRA). Le flux les publie dans la meme
    # reponse : les lire ne coute aucune requete supplementaire.
    "432": "xg",
    "499": "xgot",
    "459": "grosses_occasions",
    "461": "tirs_surface",
    "471": "touches_surface",
    "342": "passes",
    "467": "passes_dernier_tiers",
    "517": "passes_longues",
    "433": "centres",
    "513": "duels",
    "475": "tacles",
    "17": "hors_jeux",
    "19": "arrets",
}


def _stats_from_flashscore(match_id: str) -> dict[str, Any]:
    """Statistiques d'un match Flashscore.

    Le flux les decoupe en periodes (`SE`) : match entier, 1re puis 2e mi-temps.
    Seule la premiere periode nous interesse ; les suivantes reprennent les
    memes libelles et ecraseraient le total par un partiel.
    """
    payload = _fs_get("df_st_%d_%s" % (FS_SPORT, match_id))
    if not payload.strip():
        raise StatsUnavailable(
            "Flashscore ne collecte pas de statistiques pour cette competition. "
            "La page du match n'en montre pas davantage : %s"
            % (FS_MATCH_URL % match_id)
        )

    result = {
        "source": "flashscore",
        "arbitre": "",
        "domicile": _blank_stats(),
        "exterieur": _blank_stats(),
        "indisponible": [],
    }

    period = 0
    for block in _fs_blocks(payload):
        if "SE" in block:
            period += 1
        if period > 1:
            break
        field = FS_STAT_IDS.get(block.get("SD", ""))
        if field is None:
            continue
        for bucket, key in (("domicile", "SH"), ("exterieur", "SI")):
            raw = block.get(key)
            if raw in (None, ""):
                continue
            parsed = _int_or_none(raw)
            result[bucket][field] = raw if parsed is None else parsed

    # Flashscore omet la ligne des cartons rouges quand il n'y en a eu aucun.
    # La conclure a zero n'est legitime que si les cartons jaunes, eux, sont
    # presents : c'est la preuve que la source a bien suivi les cartons du match.
    if result["domicile"]["cartons_jaunes"] is not None:
        for bucket in ("domicile", "exterieur"):
            if result[bucket]["cartons_rouges"] is None:
                result[bucket]["cartons_rouges"] = 0

    result["indisponible"] = [
        field
        for field, _ in STAT_ORDER
        if result["domicile"][field] is None and result["exterieur"][field] is None
    ]
    if len(result["indisponible"]) == len(STAT_ORDER):
        raise StatsUnavailable(
            "Flashscore n'expose aucune statistique exploitable pour ce match : %s"
            % (FS_MATCH_URL % match_id)
        )
    return result


def get_stats(match: dict[str, Any], use_cache: bool = True) -> dict[str, Any]:
    """Statistiques d'un match termine.

    Privilegie API-Football, seule source gratuite fournissant corners, fautes,
    cartons et arbitre. Si aucune cle n'est configuree, se rabat sur les
    statistiques partielles de TheSportsDB.
    """
    fixture_id = ""
    if match.get("provider") == "api-football":
        fixture_id = match.get("match_id") or ""
    else:
        fixture_id = match.get("api_football_id") or ""

    has_key = bool(os.getenv("API_FOOTBALL_KEY", "").strip())
    use_af = bool(fixture_id) and has_key

    cache_key = "v%d|stats|%s|%s" % (
        CACHE_SCHEMA,
        "af" if use_af else match.get("provider", "?"),
        fixture_id if use_af else match.get("match_id", ""),
    )
    if use_cache:
        cached = cache.get(cache_key, STATS_TTL)
        if cached is not None:
            return cached

    if use_af:
        stats = _stats_from_api_football(fixture_id)
    elif match.get("provider") == "flashscore":
        # Flashscore couvre les sept statistiques suivies, sans clef : inutile
        # de se rabattre sur API-Football, qui en plus n'a pas cet identifiant.
        stats = _stats_from_flashscore(match.get("match_id") or "")
    elif match.get("provider") == "thesportsdb":
        stats = _stats_from_thesportsdb(match.get("match_id") or "")
        if not has_key:
            stats["note"] = (
                "Corners, fautes, cartons et arbitre ne sont pas fournis par la cle "
                "gratuite TheSportsDB. Renseignez API_FOOTBALL_KEY dans .env pour les "
                "obtenir (cle gratuite, 100 requetes/jour)."
            )
    elif not fixture_id:
        raise StatsUnavailable(
            "Le provider %r ne fournit pas d'identifiant permettant de "
            "recuperer les statistiques detaillees." % match.get("provider")
        )
    else:
        stats = _stats_from_api_football(fixture_id)

    cache.set(cache_key, stats)
    return stats


# ---------------------------------------------------------------------------
# Forme recente : les N derniers matchs de chaque equipe (Flashscore)
# ---------------------------------------------------------------------------

FORM_DEFAULT = 10
FORM_MAX = 30  # le flux en renvoie une cinquantaine par equipe

# Nom conventionnel du groupe des confrontations directes, qui n'en porte pas
# dans le flux.
H2H_GROUP = "__confrontations__"

# Resultat du point de vue de l'equipe suivie (champ WIS).
_FS_RESULT = {"w": "V", "d": "N", "l": "D"}


def _fs_team_name(raw: str) -> str:
    """Retire l'asterisque dont le flux prefixe le nom du vainqueur."""
    return (raw or "").lstrip("*").strip()


def _fs_form_groups(payload: str) -> list[dict[str, Any]]:
    """Groupes "Derniers matchs" de la section Global, dans l'ordre du flux.

    Le flux repete ensuite les memes matchs filtres par lieu ("X - Domicile",
    "Y - Exterieur") : on s'arrete a la section Global, seule a donner la forme
    toutes competitions et tous lieux confondus.

    Le groupe "Confrontations" n'a pas de nom d'equipe apres le deux-points : il
    est collecte a part, et rendu comme dernier element sous le nom conventionnel
    ci-dessous.
    """
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    section = ""

    for block in _fs_blocks(payload):
        if "KA" in block:
            section = block["KA"]
        if section != "Global":
            continue
        if "KB" in block:
            _, sep, team = block["KB"].partition(":")
            label = team.strip() if sep else H2H_GROUP
            current = {"equipe": label, "matchs": []}
            groups.append(current)
            continue
        if current is not None and "KC" in block:
            current["matchs"].append(block)
    return groups


def _fs_form_entry(
    block: dict[str, str], match_id: str, tz_name: str
) -> dict[str, Any] | None:
    """Une ligne de forme, vue par l'equipe suivie. None si a ignorer."""
    if block.get("AC") != "3":  # match non joue : ne compte pas dans la forme
        return None
    if block.get("KP") == match_id:  # le match consulte lui-meme
        return None

    at_home = block.get("KS") == "home"
    scored, conceded = _int_or_none(block.get("KU")), _int_or_none(block.get("KT"))
    if not at_home:
        scored, conceded = conceded, scored

    kickoff = _int_or_none(block.get("KC"))
    date = ""
    if kickoff is not None:
        date = _local_time(
            _utc_iso(kickoff), tz_name
        )[0]

    competition = block.get("KF", "")
    return {
        "match_id": block.get("KP", ""),
        "date": date,
        "kickoff_utc": _utc_iso(kickoff),
        "competition": competition,
        "adversaire": _fs_team_name(block.get("KK" if at_home else "KJ", "")),
        "lieu": "domicile" if at_home else "exterieur",
        "buts_pour": scored,
        "buts_contre": conceded,
        "resultat": _FS_RESULT.get(block.get("WIS", ""), ""),
        # Un 8-0 en amical de pre-saison ne dit rien de la forme : on le garde
        # (l'utilisateur a demande les N derniers matchs) mais on le signale.
        "amical": block.get("KI", "").startswith("AMI")
        or "amical" in competition.lower(),
    }


def _fs_form_averages(
    entries: list[dict[str, Any]], use_cache: bool
) -> dict[str, Any]:
    """Moyennes par match des statistiques detaillees, du point de vue de l'equipe.

    Une requete par match, d'ou l'activation explicite par l'appelant. Les matchs
    dont la competition n'a pas de statistiques sont ignores sans faire echouer
    l'ensemble : le nombre de matchs reellement pris en compte est rendu, pour
    que l'affichage puisse dire sur quoi la moyenne porte.
    """
    produced: dict[str, list[float]] = {field: [] for field, _ in STAT_FIELDS}
    conceded: dict[str, list[float]] = {field: [] for field, _ in STAT_FIELDS}
    covered = 0

    for entry in entries:
        if not entry["match_id"]:
            continue
        try:
            stats = get_stats(
                {"provider": "flashscore", "match_id": entry["match_id"]},
                use_cache=use_cache,
            )
        except ApiError:
            continue
        covered += 1
        at_home = entry["lieu"] == "domicile"
        mine = stats["domicile" if at_home else "exterieur"]
        theirs = stats["exterieur" if at_home else "domicile"]

        # Attache le detail au match : la prevision doit pouvoir recalculer les
        # moyennes sur un sous-ensemble (matchs officiels seulement).
        entry["stats"] = {"pour": {}, "contre": {}}
        for field, _ in STAT_FIELDS:
            for label, bucket, sink in (
                ("pour", mine, produced),
                ("contre", theirs, conceded),
            ):
                value = stat_number(bucket.get(field))
                entry["stats"][label][field] = value
                if value is not None:
                    sink[field].append(value)

    def _mean(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 1) if values else None

    return {
        "matchs_couverts": covered,
        "valeurs": {field: _mean(values) for field, values in produced.items()},
        "concedees": {field: _mean(values) for field, values in conceded.items()},
    }


# Duree de vie du cache de l'historique brut. Distincte de celle des matchs du
# jour : le backtest evalue une centaine de matchs passes, chacun avec sa propre
# date de coupe, et un historique un peu ancien ne peut lui manquer que des
# matchs POSTERIEURS a cette coupe, qu'il ecarte de toute facon.
HISTORY_TTL_DEFAULT = 0  # 0 = suit CACHE_TTL


def _fs_history(
    match_id: str, tz_name: str, use_cache: bool, ttl: int = HISTORY_TTL_DEFAULT
) -> list[dict[str, Any]]:
    """Historique complet des deux equipes, tel que publie par le flux.

    Mis en cache SANS `count` ni `before` : une seule requete reseau sert
    ensuite toutes les profondeurs d'historique et toutes les dates de coupe.
    Sans cela, evaluer le modele sur cent matchs passes demanderait une requete
    par (match, coupe), soit dix fois plus de trafic sur une source qui n'est
    pas une API publiee.
    """
    key = "v%d|hist|%s|%s" % (CACHE_SCHEMA, match_id, tz_name)
    if use_cache:
        cached = cache.get(key, ttl or _cache_ttl())
        if cached is not None:
            return cached

    payload = _fs_get("df_hh_%d_%s" % (FS_SPORT, match_id))
    sides = []
    for group in _fs_form_groups(payload):
        if group["equipe"] == H2H_GROUP:
            # Les confrontations se lisent depuis les blocs bruts : elles n'ont
            # pas d'equipe suivie, donc aucun champ "resultat" ni "lieu" a
            # interpreter comme pour une forme.
            sides.append({"equipe": H2H_GROUP, "blocs": group["matchs"]})
            continue
        entries = [
            entry
            for entry in (
                _fs_form_entry(block, match_id, tz_name) for block in group["matchs"]
            )
            if entry is not None
        ]
        sides.append({"equipe": group["equipe"], "matchs": entries})
    cache.set(key, sides)
    return sides


def _fs_head_to_head(
    group: dict[str, Any] | None,
    home_team: str,
    match_id: str,
    tz_name: str,
    before: str = "",
) -> list[dict[str, Any]]:
    """Confrontations directes passees entre les deux equipes.

    Rendues du point de vue du match consulte : `buts_domicile` est le total de
    l'equipe qui recoit AUJOURD'HUI, meme si elle se deplacait ce jour-la. Sans
    cela, comparer les rencontres entre elles demanderait de verifier a chaque
    ligne qui recevait.
    """
    if not group:
        return []

    blocks = group.get("blocs") or []
    meetings: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("AC") != "3" or block.get("KP") == match_id:
            continue
        kickoff = _int_or_none(block.get("KC"))
        stamp = _utc_iso(kickoff)
        if before and stamp and stamp >= before:
            continue
        host = _fs_team_name(block.get("KJ", ""))
        guest = _fs_team_name(block.get("KK", ""))
        host_goals, guest_goals = (
            _int_or_none(block.get("KU")),
            _int_or_none(block.get("KT")),
        )
        if host_goals is None or guest_goals is None:
            continue
        # Rapporte au sens du match a venir.
        home_first = host == _fs_team_name(home_team)
        meetings.append(
            {
                "match_id": block.get("KP", ""),
                "date": _local_time(stamp, tz_name)[0] if stamp else "",
                "kickoff_utc": stamp,
                "competition": block.get("KF", ""),
                "recevait": host,
                "buts_domicile": host_goals if home_first else guest_goals,
                "buts_exterieur": guest_goals if home_first else host_goals,
            }
        )
    return meetings


def _fs_form_side(
    side: dict[str, Any],
    count: int,
    before: str = "",
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for entry in side["matchs"]:
        # `before` coupe l'historique au coup d'envoi du match etudie. Le flux
        # rend les 50 derniers matchs A CE JOUR : sans cette coupe, evaluer le
        # modele sur un match passe utiliserait des resultats posterieurs, et
        # produirait un score flatteur qui ne veut rien dire.
        if before and entry["kickoff_utc"] and entry["kickoff_utc"] >= before:
            continue
        entries.append(dict(entry))
        if len(entries) >= count:
            break

    tally = {"V": 0, "N": 0, "D": 0}
    for entry in entries:
        if entry["resultat"] in tally:
            tally[entry["resultat"]] += 1

    return {
        "equipe": side["equipe"],
        "matchs": entries,
        "bilan": tally,
        "amicaux": sum(1 for e in entries if e["amical"]),
        "buts_pour": sum(e["buts_pour"] or 0 for e in entries),
        "buts_contre": sum(e["buts_contre"] or 0 for e in entries),
        "moyennes": None,
    }


def get_form(
    match: dict[str, Any],
    count: int = FORM_DEFAULT,
    tz_name: str = "Europe/Paris",
    use_cache: bool = True,
    with_stats: bool = False,
    before: str = "",
    history_ttl: int = 0,
) -> dict[str, Any]:
    """Les `count` derniers matchs joues par chacune des deux equipes.

    `before` (horodatage ISO) ne retient que les matchs anterieurs. Indispensable
    pour evaluer le modele sur des matchs passes sans lui donner connaissance de
    ce qui s'est produit apres.

    Une seule requete suffit pour les resultats : le flux des confrontations
    porte deja l'historique des deux equipes. `with_stats` ajoute les moyennes
    de tirs, corners, possession... et coute alors une requete par match.

    `count` et `before` sont appliques APRES le cache (voir `_fs_history`) :
    changer la profondeur ou la date de coupe ne redemande rien au reseau.
    """
    if match.get("provider") != "flashscore":
        raise StatsUnavailable(
            "La forme recente n'est disponible que sur le provider flashscore "
            "(les API REST gratuites ne fournissent pas cet historique)."
        )

    count = max(1, min(FORM_MAX, count))
    match_id = match.get("match_id") or ""
    sides = _fs_history(match_id, tz_name, use_cache, history_ttl)
    if len(sides) < 2:
        raise StatsUnavailable(
            "Flashscore ne publie pas l'historique des deux equipes pour ce "
            "match : %s" % (FS_MATCH_URL % match_id)
        )

    # Le flux liste l'equipe a domicile en premier, mais on recale sur les noms
    # du match plutot que de s'y fier : l'ordre n'est garanti nulle part.
    teams = [s for s in sides if s["equipe"] != H2H_GROUP]
    if len(teams) < 2:
        raise StatsUnavailable(
            "Flashscore ne publie pas l'historique des deux equipes pour ce "
            "match : %s" % (FS_MATCH_URL % match_id)
        )
    home, away = teams[0], teams[1]
    if _fs_team_name(match.get("exterieur", "")) == home["equipe"]:
        home, away = away, home

    form = {
        "count": count,
        "domicile": _fs_form_side(home, count, before),
        "exterieur": _fs_form_side(away, count, before),
        "confrontations": _fs_head_to_head(
            next((s for s in sides if s["equipe"] == H2H_GROUP), None),
            match.get("domicile", ""),
            match_id,
            tz_name,
            before,
        ),
    }
    if with_stats:
        for side in ("domicile", "exterieur"):
            form[side]["moyennes"] = _fs_form_averages(
                form[side]["matchs"], use_cache
            )
    return form


# ---------------------------------------------------------------------------
# Reference de championnat, calculee a partir des resultats Flashscore
# ---------------------------------------------------------------------------

# Nombre de matchs du jour dont on exploite l'historique pour batir la reference.
# Une journee de championnat en compte 9 a 10 : cela suffit a couvrir toutes les
# equipes, et chaque appel est mis en cache.
BASELINE_MAX_FIXTURES = 10

# Profondeur d'historique demandee par equipe.
BASELINE_HISTORY = 30

# En deca, l'echantillon ne vaut pas mieux que pas de reference du tout.
BASELINE_MIN_MATCHES = 20

# Sous ce nombre de matchs, la force d'une equipe n'est pas assez etablie pour
# servir a corriger les moyennes d'une autre.
BASELINE_MIN_TEAM_MATCHES = 3


# Grandeurs detaillees auxquelles s'applique la meme normalisation que les buts.
#
# `xg` y figure sans etre une grandeur pariable : les buts attendus servent a
# corriger le nombre de buts attendu (critere 11), et cette correction n'a de
# sens que si le xG passe par le meme modele que les buts -- donc s'il a, comme
# eux, une moyenne de competition et des forces par equipe.
BASELINE_STAT_FIELDS = ("corners", "tirs_cadres", "cartons_jaunes", "xg")

# Plafond de matchs interroges pour la reference detaillee. Chacun coute une
# requete la premiere fois, puis rien pendant trente jours (un match termine ne
# change plus). Au-dela, le temps d'attente cesse d'etre raisonnable.
BASELINE_STATS_MAX = 120

# Sous ce nombre de matchs effectivement couverts, la reference detaillee n'est
# pas rendue : mieux vaut retomber sur la moyenne brute que normaliser par une
# valeur tiree de trois matchs.
BASELINE_STATS_MIN = 15


def _stat_baseline(
    seen: dict[str, tuple[int, int]],
    line_up: dict[str, tuple[str, str]],
    use_cache: bool,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Reference de competition pour corners, tirs cadres, cartons et xG.

    Meme construction que pour les buts, mais la valeur ne se lit pas dans le
    flux du jour : il faut la fiche statistique de chaque match. On la demande
    donc pour les matchs deja retenus, en s'arretant au plafond.

    Rend (moyennes de la competition, forces par equipe), les deux vides si trop
    peu de matchs ont des statistiques -- ce qui arrive dans les divisions que
    Flashscore ne couvre pas en detail.
    """
    totals = {f: {"domicile": 0.0, "exterieur": 0.0, "matchs": 0}
              for f in BASELINE_STAT_FIELDS}
    tally: dict[str, dict[str, dict[str, float]]] = {}

    def _credit(team: str, field: str, produced: float, conceded: float) -> None:
        if not team:
            return
        bucket = tally.setdefault(team, {}).setdefault(
            field, {"matchs": 0.0, "pour": 0.0, "contre": 0.0}
        )
        bucket["matchs"] += 1
        bucket["pour"] += produced
        bucket["contre"] += conceded

    for match_id in list(seen)[:BASELINE_STATS_MAX]:
        home_team, away_team = line_up.get(match_id, ("", ""))
        try:
            stats = get_stats(
                {"provider": "flashscore", "match_id": match_id},
                use_cache=use_cache,
            )
        except ApiError:
            continue
        for field in BASELINE_STAT_FIELDS:
            home = stat_number(stats["domicile"].get(field))
            away = stat_number(stats["exterieur"].get(field))
            if home is None or away is None:
                continue
            totals[field]["domicile"] += home
            totals[field]["exterieur"] += away
            totals[field]["matchs"] += 1
            _credit(home_team, field, home, away)
            _credit(away_team, field, away, home)

    reference: dict[str, Any] = {}
    for field, bucket in totals.items():
        count = bucket["matchs"]
        if count < BASELINE_STATS_MIN:
            continue
        reference[field] = {
            "matchs": count,
            "moyenne_domicile": bucket["domicile"] / count,
            "moyenne_exterieur": bucket["exterieur"] / count,
            "moyenne_globale": (bucket["domicile"] + bucket["exterieur"])
            / (2 * count),
        }

    strengths: dict[str, dict[str, Any]] = {}
    for team, fields in tally.items():
        for field, bucket in fields.items():
            mean = (reference.get(field) or {}).get("moyenne_globale")
            if not mean or bucket["matchs"] < BASELINE_MIN_TEAM_MATCHES:
                continue
            strengths.setdefault(team, {})[field] = {
                "matchs": bucket["matchs"],
                "attaque": (bucket["pour"] / bucket["matchs"]) / mean,
                "defense": (bucket["contre"] / bucket["matchs"]) / mean,
            }
    return reference, strengths


def league_baseline(
    match: dict[str, Any],
    tz_name: str = "Europe/Paris",
    use_cache: bool = True,
    before: str = "",
    history_ttl: int = 0,
    with_stats: bool = False,
) -> dict[str, Any]:
    """Moyennes de buts de la competition, reconstituees depuis les resultats.

    Flashscore ne publie pas de classement exploitable par ces flux. La
    reference dont le modele a besoin (buts marques a domicile et a l'exterieur,
    en moyenne, dans cette competition) est donc recalculee ici a partir des
    resultats reels : on prend les autres matchs de la meme competition le meme
    jour, on lit l'historique des equipes concernees, et on deduplique.

    L'echantillon couvre ainsi toutes les equipes de la competition, et pas
    seulement les deux du match, ce qui evite de normaliser une equipe par
    elle-meme.
    """
    competition, country = match.get("championnat", ""), match.get("pays", "")
    if not competition:
        raise StatsUnavailable("Match sans competition identifiee.")

    same_day = get_matches(
        match["date"], provider="flashscore", tz_name=tz_name, use_cache=use_cache
    )
    # Comparaison sur le nom normalise : le flux du jour porte un suffixe de
    # phase que les historiques n'ont pas.
    wanted = normalize_competition(competition)
    fixtures = [
        m
        for m in same_day
        if normalize_competition(m["championnat"]) == wanted
        and m["pays"] == country
    ][:BASELINE_MAX_FIXTURES]

    # Chaque match d'historique est vu deux fois (une par equipe) : on le compte
    # une seule fois, sinon la moyenne serait juste mais l'effectif surestime.
    seen: dict[str, tuple[int, int]] = {}
    # Bilan par equipe, pour mesurer la force des adversaires rencontres. Chaque
    # match credite les DEUX equipes : celle dont on lit l'historique et son
    # adversaire. Sans cela, seules les equipes jouant le jour choisi seraient
    # notees, et la moitie du championnat resterait sans force connue.
    tally: dict[str, dict[str, int]] = {}
    # match_id -> (equipe qui recoit, equipe qui se deplace)
    line_up: dict[str, tuple[str, str]] = {}

    def _credit(team: str, scored: int, conceded: int) -> None:
        if not team:
            return
        bucket = tally.setdefault(
            team, {"matchs": 0, "buts_pour": 0, "buts_contre": 0}
        )
        bucket["matchs"] += 1
        bucket["buts_pour"] += scored
        bucket["buts_contre"] += conceded

    for fixture in fixtures:
        try:
            form = get_form(
                fixture,
                count=BASELINE_HISTORY,
                tz_name=tz_name,
                use_cache=use_cache,
                before=before,
                history_ttl=history_ttl,
            )
        except ApiError:
            continue
        for side in ("domicile", "exterieur"):
            team = form[side]["equipe"]
            for entry in form[side]["matchs"]:
                if normalize_competition(entry["competition"]) != wanted:
                    continue
                if entry["buts_pour"] is None or entry["buts_contre"] is None:
                    continue
                if entry["match_id"] in seen:
                    continue
                if entry["lieu"] == "domicile":
                    goals = (entry["buts_pour"], entry["buts_contre"])
                else:
                    goals = (entry["buts_contre"], entry["buts_pour"])
                seen[entry["match_id"]] = goals
                # Qui recevait : indispensable pour rattacher plus bas les
                # statistiques detaillees, que `get_stats` rend cote domicile /
                # cote exterieur du match, sans nommer les equipes.
                if entry["lieu"] == "domicile":
                    line_up[entry["match_id"]] = (team, entry["adversaire"])
                else:
                    line_up[entry["match_id"]] = (entry["adversaire"], team)
                _credit(team, entry["buts_pour"], entry["buts_contre"])
                _credit(entry["adversaire"], entry["buts_contre"], entry["buts_pour"])

    played = len(seen)
    if played < BASELINE_MIN_MATCHES:
        raise StatsUnavailable(
            "Seulement %d match(s) de %s retrouves : trop peu pour une reference "
            "de championnat fiable (minimum %d)."
            % (played, competition, BASELINE_MIN_MATCHES)
        )

    home_goals = sum(g[0] for g in seen.values())
    away_goals = sum(g[1] for g in seen.values())
    mean_per_team = (home_goals + away_goals) / (2 * played)

    # Force de chaque equipe, relative a la moyenne de la competition. Sert a
    # corriger les moyennes d'une equipe par le niveau de ses adversaires.
    strengths = {
        name: {
            "matchs": bucket["matchs"],
            "attaque": (bucket["buts_pour"] / bucket["matchs"]) / mean_per_team,
            "defense": (bucket["buts_contre"] / bucket["matchs"]) / mean_per_team,
        }
        for name, bucket in tally.items()
        if bucket["matchs"] >= BASELINE_MIN_TEAM_MATCHES and mean_per_team > 0
    }

    stat_reference: dict[str, Any] = {}
    stat_strengths: dict[str, dict[str, Any]] = {}
    if with_stats:
        stat_reference, stat_strengths = _stat_baseline(
            seen, line_up, use_cache
        )

    return {
        "competition": wanted,
        "matchs": played,
        "equipes_notees": len(strengths),
        "moyenne_domicile": home_goals / played,
        "moyenne_exterieur": away_goals / played,
        "moyenne_globale": mean_per_team,
        "forces": strengths,
        # Memes grandeurs pour corners, tirs cadres et cartons. Vides si la
        # reference detaillee n'a pas ete demandee : elle coute une requete par
        # match de la competition.
        "stats": stat_reference,
        "forces_stats": stat_strengths,
    }


# ---------------------------------------------------------------------------
# Contexte d'un match : classement, informations, compositions, meteo
# ---------------------------------------------------------------------------
#
# Le modele de Maher ne connait que des comptages passes. Tout ce qui les
# entoure -- ou en est l'equipe au classement, qui arbitre, sur quel terrain,
# apres combien de jours de repos -- se lit ailleurs. Ces quatre fonctions
# rassemblent ces sources ; `contexte.py` en fait des criteres.
#
# Chacune est INDEPENDANTE et faillible : une competition sans classement, un
# arbitre pas encore designe, une ville introuvable ne doivent jamais empecher
# une prevision. Elles rendent donc {} plutot que de lever, et l'appelant
# traite l'absence comme une information a afficher.

# Un match termine ne bouge plus : son arbitre et sa composition non plus.
# Meme duree que les statistiques.
CONTEXT_TTL = STATS_TTL

# Le classement d'une competition en cours change apres chaque journee. Une
# heure suffit : c'est deja la duree de vie des matchs du jour.
STANDINGS_TTL = 3600


def _fs_match_info(match_id: str) -> dict[str, Any]:
    """Encadrement du match : stade, ville, capacite, arbitre, affluence.

    Le flux `df_sui_` sert deux choses selon le moment : avant le match, les
    informations pratiques ; apres, le fil des evenements SUIVI de ces memes
    informations. On ne lit que les couples MIT/MIV, presents dans les deux cas.

    L'arbitre n'est publie que tardivement -- souvent le jour du match, parfois
    seulement apres. Son absence n'est donc pas une anomalie.
    """
    payload = _fs_get("df_sui_%d_%s" % (FS_SPORT, match_id))
    labels = {
        "REF": "arbitre",
        "RCC": "arbitre_pays",
        "VEN": "stade",
        "TWN": "ville",
        "CAP": "capacite",
        "ATT": "affluence",
    }
    info: dict[str, Any] = {}
    # Les couples se suivent a plat dans le meme bloc : MIT porte la cle, MIV la
    # valeur qui la suit. `_fs_blocks` ecraserait les doublons d'une meme cle,
    # donc on relit le flux brut plutot que ses blocs.
    pending = ""
    # Les enregistrements sont separes par FS_FIELD, mais le dernier d'un bloc
    # est colle au separateur de bloc : sans couper sur les deux, la cle "REF"
    # qui suit immediatement le fil des evenements est lue comme la fin du
    # record precedent, et l'arbitre disparait des matchs termines.
    for record in re.split("[%s%s]" % (FS_FIELD, FS_BLOCK), payload):
        key, sep, value = record.partition(FS_KV)
        if not sep:
            continue
        if key == "MIT":
            pending = labels.get(value, "")
        elif key == "MIV" and pending:
            info[pending] = value.strip()
            pending = ""
    for numeric in ("capacite", "affluence"):
        if numeric in info:
            digits = "".join(c for c in str(info[numeric]) if c.isdigit())
            info[numeric] = int(digits) if digits else None
    return info


def match_info(match: dict[str, Any], use_cache: bool = True) -> dict[str, Any]:
    """Stade, ville, capacite, arbitre et affluence, ou {} si indisponible.

    Rend un dictionnaire vide plutot que de lever : aucune de ces informations
    n'est indispensable, et une competition qui n'en publie aucune ne doit pas
    faire echouer la prevision qui la demande.
    """
    if match.get("provider") != "flashscore":
        return {}
    match_id = match.get("match_id") or ""
    if not match_id:
        return {}

    key = "v%d|info|%s" % (CACHE_SCHEMA, match_id)
    # Un match termine ne change plus ; un match a venir peut encore recevoir
    # son arbitre, donc on ne le fige qu'une heure.
    ttl = CONTEXT_TTL if match.get("statut") == FINISHED else STANDINGS_TTL
    if use_cache:
        cached = cache.get(key, ttl)
        if cached is not None:
            return cached
    try:
        info = _fs_match_info(match_id)
    except ApiError:
        return {}
    cache.set(key, info)
    return info


def _fs_standings(match_id: str, tz_name: str) -> dict[str, Any]:
    """Classement de la competition du match, equipe par equipe.

    Le flux `df_to_` ("table overall") rend, pour chaque equipe et dans l'ordre
    du classement : son rang, son bilan, ses derniers resultats et son prochain
    match. Ce dernier point n'existe nulle part ailleurs -- l'historique d'une
    equipe s'arrete par definition a aujourd'hui -- et c'est lui qui permet de
    voir venir une rotation avant un match important.

    Une ligne d'equipe (`TR`) est suivie de ses matchs (`LMU`) : l'appartenance
    se lit a l'ordre du flux, pas a un identifiant.
    """
    payload = _fs_get("df_to_%d_%s" % (FS_SPORT, match_id))
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for block in _fs_blocks(payload):
        if "TR" in block and "TN" in block:
            scored = conceded = None
            goals = block.get("TG", "")
            if ":" in goals:
                scored, conceded = (_int_or_none(part) for part in goals.split(":", 1))
            current = {
                "rang": _int_or_none(block.get("TR")),
                "equipe": block.get("TN", ""),
                "joues": _int_or_none(block.get("TM")),
                "gagnes": _int_or_none(block.get("TW")),
                "nuls": _int_or_none(block.get("TDR")),
                "perdus": _int_or_none(block.get("TL")),
                "buts_pour": scored,
                "buts_contre": conceded,
                "points": _int_or_none(block.get("TP")),
                # Zone de qualification ou de relegation, telle que le site la
                # colore. Son sens exact depend de la competition ; ce qui
                # compte ici est que deux equipes de meme zone jouent le meme
                # objectif.
                "zone": block.get("TU", ""),
                "derniers": [],
                "prochains": [],
            }
            rows.append(current)
            continue
        if current is None or "LMU" not in block:
            continue
        stamp = _utc_iso(_int_or_none(block.get("LMC")))
        fixture = {
            "match_id": block.get("LME", ""),
            "kickoff_utc": stamp,
            "date": _local_time(stamp, tz_name)[0] if stamp else "",
            "domicile": block.get("LMJ", ""),
            "exterieur": block.get("LMK", ""),
        }
        if block.get("LMU") == "upcoming":
            current["prochains"].append(fixture)
        else:
            fixture["resultat"] = _FS_RESULT.get(block.get("LMU", ""), "")
            current["derniers"].append(fixture)

    return {"lignes": rows, "equipes": len(rows)}


def standings(
    match: dict[str, Any], tz_name: str = "Europe/Paris", use_cache: bool = True
) -> dict[str, Any]:
    """Classement de la competition du match. {} si elle n'en a pas.

    Une coupe a elimination directe n'a pas de classement, et le flux rend alors
    un tableau vide : ce n'est pas une erreur, c'est une information -- elle dit
    que le critere « rang de l'adversaire » ne s'applique pas a ce match.
    """
    if match.get("provider") != "flashscore":
        return {}
    match_id = match.get("match_id") or ""
    if not match_id:
        return {}

    key = "v%d|classement|%s|%s" % (CACHE_SCHEMA, match_id, tz_name)
    if use_cache:
        cached = cache.get(key, STANDINGS_TTL)
        if cached is not None:
            return cached
    try:
        table = _fs_standings(match_id, tz_name)
    except ApiError:
        return {}
    if not table["lignes"]:
        return {}
    cache.set(key, table)
    return table


# Sections du flux des compositions. Les libelles sont ceux de flashscore.fr ;
# la comparaison est faite sans accent ni casse pour ne pas dependre d'eux au
# caractere pres.
_FS_LINEUP_SECTIONS = {
    "compositions de depart": "titulaires",
    "remplacants": "remplacants",
    "joueurs absents": "absents",
    "absents": "absents",
    "blesses": "absents",
    "entraineurs": "entraineurs",
}


def _plain(text: str) -> str:
    """Minuscules sans accents : comparer des libelles sans en dependre."""
    stripped = unicodedata.normalize("NFD", text or "")
    return "".join(
        c for c in stripped if unicodedata.category(c) != "Mn"
    ).lower().strip()


def _fs_lineups(match_id: str) -> dict[str, Any]:
    """Systeme de jeu, titulaires et absents de chaque equipe.

    Flashscore ne publie les compositions qu'a l'approche du coup d'envoi --
    une heure environ, parfois moins. Interroger le flux trois jours avant rend
    une reponse vide : c'est le cas NORMAL, et l'appelant doit le presenter
    comme tel plutot que comme une panne.

    `LC` vaut 1 pour l'equipe qui recoit et 2 pour l'autre ; `LD` porte le
    systeme ("1-4-2-3-1", le premier chiffre etant le gardien) et `LL` la PLACE
    du joueur dans ce dispositif -- 1 pour le gardien, puis la ligne defensive,
    et ainsi de suite jusqu'a 11. C'est cette place, et non l'ordre du flux (qui
    est alphabetique), qui permet de reconstituer une composition lisible.
    """
    payload = _fs_get("df_li_%d_%s" % (FS_SPORT, match_id))
    if not payload.strip():
        return {}

    def _vide() -> dict[str, Any]:
        return {
            "systeme": "", "titulaires": [], "onze": [],
            "remplacants": [], "absents": [],
        }

    sides: dict[str, dict[str, Any]] = {"domicile": _vide(), "exterieur": _vide()}
    section = ""
    side = ""
    for block in _fs_blocks(payload):
        if "LB" in block:
            section = _FS_LINEUP_SECTIONS.get(_plain(block["LB"]), "")
        if "LC" in block:
            side = {"1": "domicile", "2": "exterieur"}.get(block["LC"], side)
        if "LD" in block and side:
            sides[side]["systeme"] = block["LD"]
        name = block.get("LI") or block.get("LN")
        if not name or not section or not side or section == "entraineurs":
            continue
        name = name.strip()
        sides[side][section].append(name)
        if section == "titulaires":
            sides[side]["onze"].append(
                {
                    "joueur": name,
                    "numero": _int_or_none(block.get("LJ")),
                    "place": _int_or_none(block.get("LL")),
                }
            )

    for bloc in sides.values():
        # Le flux liste les titulaires par ordre alphabetique ; on les remet
        # dans l'ordre du terrain, seul lisible.
        bloc["onze"].sort(key=lambda j: (j["place"] is None, j["place"] or 0))
    return sides


def lineups(match: dict[str, Any], use_cache: bool = True) -> dict[str, Any]:
    """Compositions annoncees. {} tant qu'elles ne sont pas publiees."""
    if match.get("provider") != "flashscore":
        return {}
    match_id = match.get("match_id") or ""
    if not match_id:
        return {}

    key = "v%d|compos|%s" % (CACHE_SCHEMA, match_id)
    # Un quart d'heure avant le coup d'envoi : c'est le delai dans lequel une
    # composition annoncee peut encore etre corrigee.
    ttl = CONTEXT_TTL if match.get("statut") == FINISHED else 900
    if use_cache:
        cached = cache.get(key, ttl)
        if cached is not None:
            return cached
    try:
        found = _fs_lineups(match_id)
    except ApiError:
        return {}
    if not found:
        return {}
    cache.set(key, found)
    return found


# Profondeur par defaut pour reconstituer un onze probable. Chaque match coute
# une requete la premiere fois, puis rien : une composition passee ne change
# plus. Cinq matchs suffisent a distinguer un titulaire d'un remplacant, et ne
# remontent pas si loin que l'effectif ait change.
LINEUP_HISTORY = 5


def recent_lineups(
    entries: list[dict[str, Any]],
    profondeur: int = LINEUP_HISTORY,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Qui a commence les derniers matchs d'une equipe, et a quelle place.

    Rend, par joueur, son nombre de titularisations, sa place la plus frequente
    dans le dispositif et son numero. C'est la matiere d'un onze probable :
    aucune source gratuite ne publie de composition probable, mais la
    composition REELLE des matchs precedents en dit l'essentiel -- un joueur qui
    a commence les cinq derniers commencera vraisemblablement le sixieme.

    Les matchs sont pris dans l'ordre ou ils sont fournis, donc du plus recent
    au plus ancien. Un match dont la composition n'a pas ete publiee est saute
    sans faire echouer l'ensemble ; le nombre de matchs reellement couverts est
    rendu, pour que l'affichage puisse dire sur quoi le onze repose.
    """
    joueurs: dict[str, dict[str, Any]] = {}
    systemes: list[str] = []
    couverts = 0

    for entry in entries[:profondeur]:
        match_id = entry.get("match_id") or ""
        if not match_id:
            continue
        compos = lineups(
            {"provider": "flashscore", "match_id": match_id, "statut": FINISHED},
            use_cache=use_cache,
        )
        cote = "domicile" if entry.get("lieu") == "domicile" else "exterieur"
        bloc = compos.get(cote) or {}
        if not bloc.get("onze"):
            continue
        couverts += 1
        if bloc.get("systeme"):
            systemes.append(bloc["systeme"])
        for titulaire in bloc["onze"]:
            fiche = joueurs.setdefault(
                titulaire["joueur"],
                {"titularisations": 0, "places": [], "numero": titulaire["numero"]},
            )
            fiche["titularisations"] += 1
            if titulaire["place"] is not None:
                fiche["places"].append(titulaire["place"])

    for fiche in joueurs.values():
        places = fiche.pop("places")
        # La place MEDIANE, pas la derniere : un lateral depanne une fois en
        # defense centrale ne doit pas changer de ligne pour autant.
        fiche["place"] = sorted(places)[len(places) // 2] if places else None

    return {
        "matchs_couverts": couverts,
        "joueurs": joueurs,
        "systemes": systemes,
        # Le systeme le plus employe sur la periode, a defaut du systeme annonce.
        "systeme": max(set(systemes), key=systemes.count) if systemes else "",
    }


# Meteo : Open-Meteo, gratuit, sans cle et sans inscription, avec une licence
# qui autorise l'usage non commercial. C'est la seule source de ce projet a ne
# pas etre une source de football : elle est appelee avec la ville du stade,
# lue dans les informations du match.
OPEN_METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"

# La prevision horaire ne porte que sur seize jours ; au-dela, l'API repond
# sans erreur mais sans l'heure demandee.
WEATHER_HORIZON_DAYS = 14

# Les villes ne bougent pas : leurs coordonnees sont gardees un an. La
# prevision, elle, est reevaluee toutes les trois heures -- c'est le rythme
# auquel les modeles meteorologiques sont republies.
GEOCODE_TTL = 365 * 24 * 3600
WEATHER_TTL = 3 * 3600


def _geocode(town: str, use_cache: bool = True) -> tuple[float, float] | None:
    """Coordonnees d'une ville, ou None si elle n'est pas trouvee.

    L'echec est mis en cache au meme titre que le succes : une ville que le
    service ne connait pas ne sera pas davantage connue au prochain match qui
    s'y joue, et la redemander a chaque fois couterait une requete pour rien.
    """
    key = "v%d|ville|%s" % (CACHE_SCHEMA, town.lower())
    if use_cache:
        cached = cache.get(key, GEOCODE_TTL)
        if cached is not None:
            return (cached[0], cached[1]) if cached else None
    try:
        data = _request(
            OPEN_METEO_GEOCODE, {}, {"name": town, "count": 1, "format": "json"}
        )
    except ApiError:
        return None
    results = data.get("results") or []
    found = (
        (float(results[0]["latitude"]), float(results[0]["longitude"]))
        if results
        else None
    )
    cache.set(key, list(found) if found else [])
    return found


def weather(town: str, kickoff_utc: str, use_cache: bool = True) -> dict[str, Any]:
    """Meteo prevue a l'heure du coup d'envoi. {} si elle ne peut pas l'etre.

    Trois grandeurs seulement, celles dont l'effet sur un match de football est
    documente : la pluie (terrain lourd, ballon qui glisse), le vent (jeu long
    et centres) et la temperature. Le reste -- humidite, pression -- n'aurait
    servi qu'a alourdir la fiche.
    """
    if not town or not kickoff_utc:
        return {}
    try:
        moment = datetime.fromisoformat(kickoff_utc)
    except ValueError:
        return {}
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    horizon = abs((moment - datetime.now(timezone.utc)).days)
    if horizon > WEATHER_HORIZON_DAYS:
        return {}

    point = _geocode(town, use_cache)
    if point is None:
        return {}
    moment = moment.astimezone(timezone.utc)
    day = moment.strftime("%Y-%m-%d")
    hour = moment.strftime("%Y-%m-%dT%H:00")

    key = "v%d|meteo|%.2f|%.2f|%s" % (CACHE_SCHEMA, point[0], point[1], hour)
    if use_cache:
        cached = cache.get(key, WEATHER_TTL)
        if cached is not None:
            return cached
    try:
        data = _request(
            OPEN_METEO_FORECAST,
            {},
            {
                "latitude": point[0],
                "longitude": point[1],
                "hourly": "temperature_2m,precipitation,wind_speed_10m",
                "start_date": day,
                "end_date": day,
                "timezone": "UTC",
            },
        )
    except ApiError:
        return {}

    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    if hour not in times:
        return {}
    index = times.index(hour)

    def _at(field: str) -> float | None:
        values = hourly.get(field) or []
        if index >= len(values) or values[index] is None:
            return None
        return float(values[index])

    found = {
        "ville": town,
        "heure_utc": hour,
        "temperature_c": _at("temperature_2m"),
        "precipitation_mm": _at("precipitation"),
        "vent_kmh": _at("wind_speed_10m"),
        "source": "open-meteo",
    }
    cache.set(key, found)
    return found

# ---------------------------------------------------------------------------
# Blessures et suspensions : sportsgambler.com
# ---------------------------------------------------------------------------
#
# Flashscore ne publie les absents qu'avec la composition, environ une heure
# avant le coup d'envoi -- trop tard pour une prevision emise la veille. Cette
# source-ci les publie en continu, par championnat, avec le motif et la date de
# retour attendue.
#
# Une difficulte que BetExplorer n'avait pas : **les identifiants de match ne
# sont pas partages**, et il faut donc rapprocher les equipes par leur NOM.
# C'est exactement ce qui rend fragiles les assemblages de sources
# ("Manchester Utd" ici, "Manchester United" la), et le rapprochement est donc
# volontairement strict (voir `_same_team`) : en cas de doute, on ne rapproche
# pas. Un critere qui se declare indisponible est sans consequence ; un critere
# qui attribue a Manchester City les blesses de Manchester United fausserait la
# prevision sans que rien ne le signale.

# En-tetes d'un navigateur. Les deux sources HTML de ce module (celle-ci et
# betexplorer) servent une page differente, sans les donnees attendues, au
# client par defaut. Ce n'est pas un contournement d'authentification : la page
# publique est la meme, seule sa mise en forme change.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "fr,en;q=0.8",
}

SPORTSGAMBLER_LEAGUE = "https://www.sportsgambler.com/injuries/football/%s/"

# Les absences changent au fil de la semaine, pas de l'heure. Six heures.
INJURY_TTL = 6 * 3600

# Correspondance (pays, competition) -> section du site. Les cles sont
# normalisees par `_plain`, ce qui absorbe la casse et les accents.
INJURY_LEAGUES = {
    ("angleterre", "premier league"): "england-premier-league",
    ("angleterre", "championship"): "england-championship",
    ("france", "ligue 1"): "france-ligue-1",
    ("espagne", "laliga"): "spain-la-liga",
    ("espagne", "laliga2"): "spain-la-liga-2",
    ("italie", "serie a"): "italy-serie-a",
    ("italie", "serie b"): "italy-serie-b",
    ("allemagne", "bundesliga"): "germany-bundesliga",
    ("allemagne", "2. bundesliga"): "germany-2-bundesliga",
    ("pays-bas", "eredivisie"): "netherlands-eredivisie",
    ("portugal", "liga portugal"): "portugal-primeira-liga",
    ("belgique", "pro league"): "belgium-first-division-a",
    ("turquie", "super lig"): "turkey-super-lig",
    ("ecosse", "premiership"): "scottish-premiership",
    ("suisse", "super league"): "switzerland-super-league",
    ("autriche", "bundesliga"): "austria-bundesliga",
    ("danemark", "superliga"): "denmark-superliga",
    ("norvege", "eliteserien"): "norway-eliteserien",
    ("suede", "allsvenskan"): "sweden-allsvenskan",
    ("russie", "premier league"): "russia-premier-league",
    ("bresil", "serie a"): "brazil-serie-a",
    ("argentine", "liga profesional"): "argentine-primera-division",
    ("mexique", "liga mx"): "mexico-liga-mx",
    ("usa", "mls"): "usa-mls",
    ("etats-unis", "mls"): "usa-mls",
    ("irlande", "premier division"): "ireland-premier-division",
    ("europe", "ligue des champions"): "uefa-champions-league",
    ("europe", "ligue europa"): "uefa-europa-league",
    ("europe", "ligue europa conference"): "uefa-europa-conference-league",
}

_SG_BLOCK = re.compile(
    r'<h3 class="injuries-title"[^>]*>\s*<a[^>]*>(?P<equipe>[^<]+)</a>\s*</h3>'
    r'(?P<corps>.*?)(?=<h3 class="injuries-title"|</main>|$)',
    re.DOTALL,
)
# L'espace avant le groupe n'est pas decoratif : il ecarte la ligne d'en-tete
# du tableau, dont la classe est `inj-type` sans suffixe et dont le "joueur"
# s'appelle « Name ».
_SG_ROW = re.compile(
    r'<span class="inj-type (?P<type>[a-z-]+)"[^>]*>.*?'
    r'<span class="inj-player">(?P<joueur>[^<]*)</span>.*?'
    r'<span class="inj-position[^"]*">(?P<poste>[^<]*)</span>.*?'
    r'<span class="inj-info">(?P<info>[^<]*)</span>',
    re.DOTALL,
)

# Motifs tels que le site les code dans la classe CSS. La distinction entre
# `blesse` et `incertain` compte : un joueur douteux joue une fois sur deux, et
# le compter comme absent surestimerait l'affaiblissement de son equipe.
_SG_MOTIFS = {
    "injury-plus": "blesse",
    "injury-questionmark": "incertain",
    "redcard": "suspendu",
    "yellowcard": "suspendu",
}

# Postes tels que le site les abrege, ramenes a ce qui interesse le modele :
# une absence en attaque retire des buts a son equipe, une absence en defense
# en donne a l'adversaire.
_SG_POSTES = {"G": "gardien", "D": "defenseur", "M": "milieu", "F": "attaquant"}


def _sportsgambler(slug: str) -> dict[str, list[dict[str, str]]]:
    """Absents par equipe pour une competition, sous le nom du site."""
    payload = _http_get(SPORTSGAMBLER_LEAGUE % slug, _BROWSER_HEADERS, {}).text
    par_equipe: dict[str, list[dict[str, str]]] = {}
    for bloc in _SG_BLOCK.finditer(payload):
        equipe = bloc.group("equipe").strip()
        absents = [
            {
                "joueur": row.group("joueur").strip(),
                "motif": _SG_MOTIFS.get(row.group("type").strip(), "indisponible"),
                "poste": _SG_POSTES.get(row.group("poste").strip(), ""),
                "detail": row.group("info").strip(),
            }
            for row in _SG_ROW.finditer(bloc.group("corps"))
            if row.group("joueur").strip()
        ]
        if equipe:
            par_equipe[equipe] = absents
    return par_equipe


# Mots qui ne distinguent pas deux clubs : les retirer avant de comparer.
_CLUB_BRUIT = {
    "fc", "afc", "cf", "sc", "ac", "as", "ss", "us", "sv", "vfl", "vfb", "tsg",
    "bsc", "rc", "cd", "ud", "club", "calcio", "football",
}

# Abreviations que les deux sources n'ecrivent pas pareil.
_CLUB_SYNONYMES = {"utd": "united", "st": "saint", "man": "manchester"}


def _tokens(nom: str) -> frozenset[str]:
    """Mots distinctifs d'un nom de club, normalises."""
    plat = re.sub(r"[^a-z0-9 ]", " ", _plain(nom))
    mots = [_CLUB_SYNONYMES.get(mot, mot) for mot in plat.split()]
    return frozenset(mot for mot in mots if mot and mot not in _CLUB_BRUIT)


def _same_team(gauche: str, droite: str) -> bool:
    """Deux ecritures designent-elles le meme club ?

    Vrai si l'un des deux jeux de mots distinctifs contient l'autre :
    « Nottingham » et « Nottingham Forest » se rapprochent, « Manchester
    United » et « Manchester City » non -- aucun des deux ne contient l'autre.

    Un nom reduit a un seul mot tres commun ne suffit pas : « Manchester » seul
    ne doit pas se rapprocher des deux clubs de la ville. C'est le sens de la
    verification d'unicite faite par l'appelant.
    """
    a, b = _tokens(gauche), _tokens(droite)
    if not a or not b:
        return False
    return a <= b or b <= a


def _absents_de(par_equipe: dict[str, list], equipe: str) -> list[dict[str, str]] | None:
    """Absents d'une equipe, ou None si le rapprochement n'est pas certain.

    Deux correspondances valent aucune : mieux vaut un critere indisponible
    qu'un critere qui attribue a une equipe les absents d'une autre.
    """
    trouves = [nom for nom in par_equipe if _same_team(nom, equipe)]
    return par_equipe[trouves[0]] if len(trouves) == 1 else None


def injuries(
    match: dict[str, Any], use_cache: bool = True
) -> dict[str, Any]:
    """Blessures et suspensions des deux equipes. {} si la source ne couvre pas.

    Rend, pour chaque cote, la liste des absents avec leur motif et leur poste,
    et dit explicitement quand une equipe n'a pas pu etre rapprochee : un
    « aucun absent » et un « equipe non trouvee » se ressemblent trop pour etre
    confondus.
    """
    cle_ligue = (
        _plain(match.get("pays", "")),
        _plain(normalize_competition(match.get("championnat", ""))),
    )
    slug = INJURY_LEAGUES.get(cle_ligue)
    if not slug:
        return {}

    key = "v%d|absents|%s" % (CACHE_SCHEMA, slug)
    par_equipe = cache.get(key, INJURY_TTL) if use_cache else None
    if par_equipe is None:
        try:
            par_equipe = _sportsgambler(slug)
        except ApiError:
            return {}
        if not par_equipe:
            return {}
        cache.set(key, par_equipe)

    resultat: dict[str, Any] = {"source": "sportsgambler", "competition": slug}
    for cote, equipe in (
        ("domicile", match.get("domicile", "")),
        ("exterieur", match.get("exterieur", "")),
    ):
        absents = _absents_de(par_equipe, equipe)
        resultat[cote] = {
            "equipe": equipe,
            "rapprochee": absents is not None,
            "absents": absents or [],
        }
    return resultat

# ---------------------------------------------------------------------------
# Cotes du marche : betexplorer.com
# ---------------------------------------------------------------------------
#
# Flashscore ne publie aucune cote sur son edition francaise -- son point
# d'entree `df_od_` repond vide, et sur les quatre editions essayees (fr, com,
# co.uk, livescore.in). La reglementation francaise l'explique probablement ;
# le resultat est le meme.
#
# BetExplorer appartient au meme groupe (Livesport), compare une trentaine
# d'operateurs, et -- ce qui rend la chose exploitable -- **emploie les memes
# identifiants de match que Flashscore**. Une cote relevee la se rattache donc
# au match sans aucun rapprochement par nom d'equipe. C'est loin d'etre un
# detail : le rapprochement par nom est ce qui rend fragiles les assemblages de
# sources ("Manchester Utd" contre "Manchester United"), et il est ici inutile.
#
# Deux cotes sont rendues pour chaque issue, et elles ne disent pas la meme
# chose :
#
#   moyennes    la cote moyenne des operateurs. C'est le consensus du marche,
#               donc celle a laquelle comparer une probabilite : elle resume ce
#               que le marche croit.
#   meilleures  la meilleure cote offerte. C'est celle qu'un parieur obtient
#               reellement, donc la seule qui dise si un pari rapporte.
#
# Une seule requete par JOURNEE rend les cotes de tous les matchs du jour.

BETEXPLORER_DAY = "https://www.betexplorer.com/next/football/"

# Les cotes bougent -- c'est tout l'interet du critere 13. Un quart d'heure est
# le compromis : assez court pour voir une ligne se deplacer dans la journee,
# assez long pour ne pas rappeler la page a chaque prevision emise.
ODDS_TTL = 15 * 60

# Un bouton de pari porte, dans le meme element de ligne, l'identifiant du
# match, celui de l'issue, la cote moyenne et la meilleure. Les lire ensemble
# evite d'avoir a comprendre la structure de la page, qui differe deja entre la
# vue « championnat » (un tableau) et la vue « journee » (des listes).
_BE_ODD = re.compile(
    r"matchid=(?P<match>[A-Za-z0-9]{6,12})"
    r"&outcomeid=(?P<issue>[A-Za-z0-9]+)"
    r".*?data-odd=\"(?P<moyenne>\d+(?:\.\d+)?)\""
    r"(?:\s+data-odd-max=\"(?P<meilleure>\d+(?:\.\d+)?)\")?"
)

# Ordre d'apparition des trois issues dans la colonne des cotes.
_BE_ISSUES = ("domicile", "nul", "exterieur")

def _betexplorer_day(date: str) -> dict[str, dict[str, dict[str, float]]]:
    """Cotes 1X2 de tous les matchs d'une journee, par identifiant de match."""
    year, month, day = date.split("-")
    payload = _http_get(
        BETEXPLORER_DAY,
        _BROWSER_HEADERS,
        {"year": int(year), "month": int(month), "day": int(day)},
    ).text

    vus: dict[str, list[str]] = {}
    releve: dict[str, dict[str, dict[str, float]]] = {}
    for found in _BE_ODD.finditer(payload):
        match_id = found.group("match")
        issue = found.group("issue")
        connues = vus.setdefault(match_id, [])
        if issue in connues:
            continue
        rang = len(connues)
        connues.append(issue)
        if rang >= len(_BE_ISSUES):
            # Un match qui porte plus de trois issues n'est pas un 1X2 : mieux
            # vaut ne rien en dire que de ranger la quatrieme cote sous un nom
            # qui ne lui va pas.
            continue
        moyenne = float(found.group("moyenne"))
        meilleure = float(found.group("meilleure") or moyenne)
        bloc = releve.setdefault(match_id, {"moyennes": {}, "meilleures": {}})
        bloc["moyennes"][_BE_ISSUES[rang]] = moyenne
        bloc["meilleures"][_BE_ISSUES[rang]] = meilleure

    # Un match dont une seule issue a ete lue n'est pas exploitable : la marge
    # ne peut pas etre retiree, donc aucune probabilite ne peut en etre tiree.
    return {
        match_id: bloc
        for match_id, bloc in releve.items()
        if len(bloc["moyennes"]) == len(_BE_ISSUES)
    }


def market_odds(
    date: str, use_cache: bool = True
) -> dict[str, dict[str, dict[str, float]]]:
    """Cotes 1X2 des matchs d'une journee. {} si la source est muette.

    Indexees par l'identifiant de match Flashscore, donc directement
    utilisables. Rend un dictionnaire vide plutot que de lever : une prevision
    doit pouvoir sortir sans cote, et le critere 13 se declare alors
    indisponible.
    """
    key = "v%d|cotes|%s" % (CACHE_SCHEMA, date)
    if use_cache:
        cached = cache.get(key, ODDS_TTL)
        if cached is not None:
            return cached
    try:
        found = _betexplorer_day(date)
    except (ApiError, ValueError):
        return {}
    if found:
        # Un releve vide n'est pas mis en cache : c'est le plus souvent une
        # page qui a change ou une coupure, et le figer un quart d'heure
        # priverait de cotes toutes les previsions emises entre-temps.
        cache.set(key, found)
    return found


def odds_for(
    match: dict[str, Any], cote: str = "meilleures", use_cache: bool = True
) -> dict[str, float] | None:
    """Cotes 1X2 d'un match, ou None si la source ne les publie pas.

    `cote` vaut "meilleures" (celle qu'un parieur obtient) ou "moyennes" (le
    consensus du marche). La journee entiere est relevee puis mise en cache :
    deux matchs du meme jour ne coutent qu'une requete a eux deux.
    """
    match_id = match.get("match_id") or ""
    if not match_id or not match.get("date"):
        return None
    bloc = market_odds(match["date"], use_cache).get(match_id)
    return (bloc or {}).get(cote)

# ---------------------------------------------------------------------------
# API publique du module
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Cotes agregees (the-odds-api.com) : tous les marches, plusieurs operateurs
# ---------------------------------------------------------------------------
#
# BetExplorer et 1xbet ont ete essayes avant, et ecartes pour la meme raison :
# leurs cotes ne sont pas dans le HTML. Seule la page du JOUR de BetExplorer est
# rendue cote serveur, et uniquement en 1X2 ; les pages par match des deux sites
# chargent leurs marches en JavaScript. Les lire demanderait un navigateur sans
# interface -- une dependance de plusieurs centaines de megaoctets pour un
# projet qui en compte cinq -- ou de retro-concevoir une API interne qui a deja
# change (les points d'entree `LineFeed` de 1xbet rendent aujourd'hui 404).
#
# Un agregateur documente evite les trois ecueils : il publie du JSON stable, il
# couvre tous les marches, et il nomme ses operateurs -- dont `onexbet`, si
# c'est 1xbet qu'on veut. Il coute une inscription et une cle.
#
# Ce qu'il ne resout PAS : ses identifiants de match ne sont pas ceux de
# Flashscore. Le rapprochement se fait sur les NOMS d'equipes, et c'est le point
# fragile de toute la chaine -- apparier deux matchs differents valoriserait un
# pari avec le prix d'un autre, sans jamais lever d'erreur. D'ou la regle
# retenue : les DEUX equipes doivent se reconnaitre, sinon le match est ecarte
# et compte a part.

ODDS_API = "https://api.the-odds-api.com/v4"

# Quinze minutes, comme les cotes de BetExplorer : au-dela, une cote relevee
# n'est plus celle qu'on peut prendre. Le quota gratuit est mensuel, et le cache
# est ce qui empeche de le bruler en rejouant la meme journee.
AGGREGATED_TTL = 15 * 60

# Marches demandes. `h2h` est le 1X2, `totals` les plus/moins de buts. On ne
# demande pas les handicaps : le modele n'a aucune proposition qui leur
# corresponde, et payer une requete pour des cotes inexploitables serait du
# quota perdu.
ODDS_MARKETS = "h2h,totals"

#: Region des operateurs. "eu" couvre les operateurs europeens, dont 1xBet.
ODDS_REGIONS = "eu"


#: Competitions Flashscore -> cle de l'agregateur, indexees par (pays, nom).
#:
#: L'agregateur n'a pas de cle « football » generique : il facture et indexe par
#: COMPETITION, ce qui impose de savoir laquelle demander. La table ne couvre
#: donc que les competitions qu'il publie, et rien n'est devine : une
#: competition absente n'est simplement pas interrogee, et le match est compte
#: comme non couvert plutot que rapproche au hasard.
#:
#: Le pays fait partie de la cle, et ce n'est pas une precaution theorique : le
#: projet a deja mesure que « Premier League » designe une dizaine de
#: championnats dans le monde. Demander les cotes anglaises pour un match
#: kazakh ne leverait aucune erreur -- l'appariement par noms d'equipes
#: echouerait ensuite, et le match ressortirait « non apparie » sans qu'on sache
#: que la faute etait ici.
CLES_AGREGATEUR = {
    ("angleterre", "premier league"): "soccer_epl",
    ("angleterre", "championship"): "soccer_efl_champ",
    ("angleterre", "league one"): "soccer_england_league1",
    ("angleterre", "league two"): "soccer_england_league2",
    ("angleterre", "efl cup"): "soccer_england_efl_cup",
    ("angleterre", "carabao cup"): "soccer_england_efl_cup",
    ("angleterre", "fa cup"): "soccer_fa_cup",
    ("espagne", "laliga"): "soccer_spain_la_liga",
    ("espagne", "laliga2"): "soccer_spain_segunda_division",
    ("france", "ligue 1"): "soccer_france_ligue_one",
    ("france", "ligue 2"): "soccer_france_ligue_two",
    ("allemagne", "bundesliga"): "soccer_germany_bundesliga",
    ("allemagne", "2. bundesliga"): "soccer_germany_bundesliga2",
    ("allemagne", "3. liga"): "soccer_germany_liga3",
    ("italie", "serie a"): "soccer_italy_serie_a",
    ("italie", "serie b"): "soccer_italy_serie_b",
    ("pays-bas", "eredivisie"): "soccer_netherlands_eredivisie",
    ("portugal", "liga portugal"): "soccer_portugal_primeira_liga",
    ("belgique", "pro league"): "soccer_belgium_first_div",
    ("turquie", "super lig"): "soccer_turkey_super_league",
    ("ecosse", "premiership"): "soccer_spl",
    ("mexique", "liga mx"): "soccer_mexico_ligamx",
    ("bresil", "serie a betano"): "soccer_brazil_campeonato",
    ("bresil", "serie b"): "soccer_brazil_serie_b",
    ("argentine", "liga profesional"): "soccer_argentina_primera_division",
    ("etats-unis", "mls"): "soccer_usa_mls",
    ("danemark", "superliga"): "soccer_denmark_superliga",
    ("norvege", "eliteserien"): "soccer_norway_eliteserien",
    ("suede", "allsvenskan"): "soccer_sweden_allsvenskan",
    ("suisse", "super league"): "soccer_switzerland_superleague",
    ("autriche", "bundesliga"): "soccer_austria_bundesliga",
    ("grece", "super league"): "soccer_greece_super_league",
    ("pologne", "ekstraklasa"): "soccer_poland_ekstraklasa",
    ("japon", "j1 league"): "soccer_japan_j_league",
    # Les coupes d'Europe portent « Europe » comme pays dans le flux.
    ("europe", "ligue des champions"): "soccer_uefa_champs_league",
    ("europe", "ligue europa"): "soccer_uefa_europa_league",
    ("europe", "ligue europa conference"): "soccer_uefa_europa_conference_league",
    ("europe", "ligue des nations"): "soccer_uefa_nations_league",
}


def _cle_competition(texte: str) -> str:
    """Nom de competition reduit a sa forme comparable.

    Le flux ecrit « Liga Profesional - Cloture (Argentine)» ou « Ligue des
    Champions - Phase de ligue » : le suffixe de phase change en cours de
    saison et ne designe pas une autre competition. On le retire, comme le fait
    deja `normalize_competition`, puis on efface accents et casse.
    """
    base = (texte or "").split(" - ", 1)[0]
    base = unicodedata.normalize("NFKD", base)
    base = "".join(c for c in base if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", base).strip()


def cle_agregateur(competition: str, pays: str) -> str:
    """Cle de l'agregateur pour cette competition, ou "" si non couverte.

    Rendre "" est un resultat, pas un echec : l'agregateur ne publie qu'une
    cinquantaine de championnats sur les milliers que couvre le flux, et
    interroger une cle inventee couterait du quota pour rien.
    """
    return CLES_AGREGATEUR.get(
        (_cle_competition(pays), _cle_competition(competition)), ""
    )


def _cle_odds_api() -> str:
    return (os.getenv("ODDS_API_KEY") or "").strip()


def _nom_comparable(nom: str) -> str:
    """Nom d'equipe reduit a ce qui permet de le reconnaitre d'une source a
    l'autre.

    Flashscore ecrit « Manchester Utd », l'agregateur « Manchester United ».
    Comparer les chaines brutes echouerait sur presque tout. On retire donc les
    accents, la ponctuation, la casse, et les mots qui ne distinguent rien --
    « FC », « AS », « United »... Ce qui reste doit suffire a identifier le club
    sans jamais confondre deux clubs differents, ce qui est la contrainte
    importante : rater un rapprochement coute une cote, en inventer un coute un
    pari valorise au prix d'un autre match.
    """
    texte = unicodedata.normalize("NFKD", nom or "")
    texte = "".join(c for c in texte if not unicodedata.combining(c)).lower()
    texte = re.sub(r"[^a-z0-9 ]+", " ", texte)
    mots = [m for m in texte.split() if m not in _MOTS_VIDES]
    return " ".join(mots)


#: Mots qui n'identifient aucun club et varient d'une source a l'autre.
_MOTS_VIDES = {
    "fc", "afc", "cf", "sc", "ac", "as", "ss", "us", "sv", "vfb", "vfl", "bsc",
    "rc", "cd", "ud", "sd", "club", "de", "the", "and",
    # « United » / « Utd » et « City » distinguent parfois deux clubs d'une meme
    # ville : on les GARDE, en normalisant seulement l'abreviation.
}

#: Abreviations a developper avant comparaison. Volontairement COURTE : chaque
#: entree est une regle de traduction, et une regle fausse apparie deux clubs
#: differents en silence. On n'y met que des sigles sans ambiguite, jamais des
#: equivalences de langue -- « Munich » et « Munchen » designent bien la meme
#: ville, mais ouvrir cette porte reviendrait a maintenir un dictionnaire
#: multilingue dont chaque erreur coute un pari valorise au prix d'un autre. Un
#: nom non reconnu est RENDU comme non apparie, et se voit.
_ABREVIATIONS = {
    "utd": ("united",),
    "manu": ("manchester", "united"),
    "psg": ("paris", "saint", "germain"),
    "sg": ("saint", "germain"),
    "atl": ("atletico",),
    "ath": ("athletic",),
    # Sigles anglais courants, sans ambiguite : « QPR » ne designe qu'un club,
    # « West Brom » qu'un autre. Ce sont des ABREVIATIONS du meme nom, pas des
    # traductions -- la difference compte, et c'est pourquoi « Barcelone » /
    # « Barcelona » et « Naples » / « Napoli » restent hors de cette table :
    # traduire des noms d'une langue a l'autre ouvrirait un dictionnaire dont
    # chaque erreur valorise un pari au prix d'un autre match.
    "qpr": ("queens", "park", "rangers"),
    "brom": ("bromwich",),
    "wba": ("west", "bromwich", "albion"),
    "spurs": ("tottenham",),
    "wolves": ("wolverhampton",),
}


def _mots_cles(nom: str) -> frozenset:
    """Ensemble des mots identifiants d'un nom, abreviations developpees.

    Le developpement produit PLUSIEURS mots quand il le faut : « psg » vaut
    trois mots, et les ranger comme une seule chaine faisait echouer le test
    d'inclusion sur tout nom abrege en plusieurs morceaux.
    """
    mots: set[str] = set()
    for mot in _nom_comparable(nom).split():
        mots.update(_ABREVIATIONS.get(mot, (mot,)))
    return frozenset(mots)


def memes_equipes(gauche: str, droite: str) -> bool:
    """Les deux noms designent-ils le meme club ?

    Regle volontairement stricte : l'un des deux ensembles de mots doit contenir
    l'autre. « Manchester Utd » et « Manchester United » se reconnaissent,
    « Manchester United » et « Manchester City » non -- et c'est bien la
    distinction qui compte. Un rapprochement rate se voit (le match est compte
    comme non apparie) ; un rapprochement faux ne se verrait jamais.
    """
    # Une equipe feminine, de jeunes ou reserve n'est PAS le club premier, et
    # leurs noms ne different que par un suffixe que la normalisation efface :
    # « Everton » et « Everton F » se retrouvaient apparies, donc une fiche
    # masculine valorisee aux cotes du match feminin. `is_variant_team` fait
    # deja cette distinction ailleurs dans le module ; elle vaut ici aussi.
    if is_variant_team(gauche) != is_variant_team(droite):
        return False
    a, b = _mots_cles(gauche), _mots_cles(droite)
    if not a or not b:
        return False
    return a <= b or b <= a


def aggregated_odds(
    sport_key: str, date: str, use_cache: bool = True, operateur: str = ""
) -> list[dict[str, Any]]:
    """Cotes agregees des matchs de football d'une journee. [] si pas de cle.

    `operateur` restreint a un operateur (« onexbet » pour 1xBet) ; vide garde
    la MEILLEURE cote de chaque issue, tous operateurs confondus -- ce qui est
    ce qu'un parieur obtient reellement s'il a plusieurs comptes, et ce que le
    critere 13 appelle deja « meilleures ».

    Rend une liste d'evenements normalises, sans lever : une prevision doit
    pouvoir sortir sans cote. L'absence de cle n'est pas une panne, c'est une
    configuration -- `--valeur` le dira.
    """
    cle = _cle_odds_api()
    if not cle or not sport_key:
        return []

    marque = "v%d|agregat|%s|%s|%s" % (
        CACHE_SCHEMA, sport_key, date, operateur or "toutes"
    )
    if use_cache:
        cached = cache.get(marque, AGGREGATED_TTL)
        if cached is not None:
            return cached

    params = {
        "apiKey": cle,
        "regions": ODDS_REGIONS,
        "markets": ODDS_MARKETS,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    if operateur:
        params["bookmakers"] = operateur
    try:
        reponse = _http_get(
            "%s/sports/%s/odds" % (ODDS_API, sport_key), _BROWSER_HEADERS, params
        )
        brut = reponse.json()
    except (ApiError, ValueError):
        return []
    if not isinstance(brut, list):
        return []

    evenements = []
    for event in brut:
        # La journee entiere est demandee puis filtree ici : l'API facture la
        # requete, pas les evenements, et decouper par date coute plus de quota
        # qu'elle n'en economise.
        debut = (event.get("commence_time") or "")[:10]
        if date and debut != date:
            continue
        evenements.append(
            {
                # L'identifiant sert a redemander CE match sur les marches non
                # vedettes, qui ne passent pas par l'appel groupe.
                "id": event.get("id", ""),
                "domicile": event.get("home_team", ""),
                "exterieur": event.get("away_team", ""),
                "debut": event.get("commence_time", ""),
                "cotes": _meilleures_cotes(event.get("bookmakers") or []),
            }
        )
    if evenements:
        cache.set(marque, evenements)
    return evenements


def aggregated_event_odds(
    sport_key: str,
    event_id: str,
    markets: str,
    use_cache: bool = True,
    operateur: str = "",
) -> dict[str, Any]:
    """Cotes d'UN match sur les marches non vedettes. {} si pas de cle.

    L'appel groupe les refuse explicitement (HTTP 422 : « Markets not supported
    by this endpoint »), et l'erreur ne coute rien. Il faut donc un appel par
    match, facture `[nombre de marches] x [nombre de regions]` : six marches sur
    dix matchs coutent soixante credits, la ou l'appel groupe en coute deux pour
    toute une competition. D'ou l'activation explicite cote appelant.
    """
    cle = _cle_odds_api()
    if not cle or not sport_key or not event_id:
        return {}
    marque = "v%d|agregat-match|%s|%s|%s" % (
        CACHE_SCHEMA, event_id, markets, operateur or "toutes"
    )
    if use_cache:
        cached = cache.get(marque, AGGREGATED_TTL)
        if cached is not None:
            return cached
    params = {
        "apiKey": cle,
        "regions": ODDS_REGIONS,
        "markets": markets,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    if operateur:
        params["bookmakers"] = operateur
    try:
        reponse = _http_get(
            "%s/sports/%s/events/%s/odds" % (ODDS_API, sport_key, event_id),
            _BROWSER_HEADERS, params,
        )
        brut = reponse.json()
    except (ApiError, ValueError):
        return {}
    if not isinstance(brut, dict) or not brut.get("bookmakers"):
        return {}
    cache.set(marque, brut)
    return brut


def _meilleures_cotes(bookmakers: list[dict[str, Any]]) -> dict[str, Any]:
    """Meilleure cote de chaque issue et de chaque ligne, tous operateurs.

    Une cote plus haute est toujours preferable a mise egale : garder le maximum
    n'est pas un choix discutable, c'est ce qu'un parieur obtient. L'operateur
    retenu est conserve pour que la fiche puisse dire OU la prendre.
    """
    issues: dict[str, tuple[float, str]] = {}
    totaux: dict[tuple[str, float], tuple[float, str]] = {}
    for bookmaker in bookmakers:
        titre = bookmaker.get("title") or bookmaker.get("key") or ""
        for marche in bookmaker.get("markets") or []:
            for issue in marche.get("outcomes") or []:
                prix = issue.get("price")
                if not prix or float(prix) <= 1.0:
                    continue
                prix = float(prix)
                if marche.get("key") == "h2h":
                    nom = issue.get("name", "")
                    if prix > issues.get(nom, (0.0, ""))[0]:
                        issues[nom] = (prix, titre)
                elif marche.get("key") == "totals" and issue.get("point") is not None:
                    reference = (issue.get("name", ""), float(issue["point"]))
                    if prix > totaux.get(reference, (0.0, ""))[0]:
                        totaux[reference] = (prix, titre)
    return {
        "issues": {nom: prix for nom, (prix, _) in issues.items()},
        "issues_operateur": {nom: op for nom, (_, op) in issues.items()},
        "totaux": {
            "%s|%g" % (sens, ligne): prix
            for (sens, ligne), (prix, _) in totaux.items()
        },
        "totaux_operateur": {
            "%s|%g" % (sens, ligne): op
            for (sens, ligne), (_, op) in totaux.items()
        },
    }


def validate_date(date: str) -> None:
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise ApiError(
            "Date invalide : %r. Format attendu : YYYY-MM-DD (ex: 2026-09-10)." % (date,)
        ) from None


def filter_by_league(
    matches: Iterable[dict[str, Any]], league: str
) -> list[dict[str, Any]]:
    """Filtre par sous-chaine insensible a la casse sur le nom du championnat."""
    needle = league.strip().lower()
    return [m for m in matches if needle in m["championnat"].lower()]


# Declinaisons d'un meme club : equipe feminine, categories de jeunes, equipe
# reserve. Le suffixe est la seule marque disponible ; les motifs ci-dessous ont
# ete releves sur un jour complet du flux (3 518 equipes) sans faux positif :
# "Genoa F", "Dortmund II", "FC Porto B", "Columbus Crew 2", "Itabaiana -20".
_TEAM_VARIANT = re.compile(
    r"""
      \s (?: F              # feminine
           | II | III       # reserve
           | B
           | [23]
        ) $
    | \s- \d{2} \b          # categorie d'age : "-17", "-20"
    | \b U \d{2} \b         # idem, autre notation
    """,
    re.VERBOSE,
)

# Certaines competitions marquent la declinaison quand le nom d'equipe ne le
# fait pas ("Bundesliga - Femmes", "Campeonato Nacional U19").
_COMPETITION_VARIANT = re.compile(
    r"Femmes|F[eé]minin|\bU\d{2}\b|\s-\d{2}\b", re.IGNORECASE
)


def normalize_competition(name: str) -> str:
    """Nom de competition sans son suffixe de phase.

    Le flux du jour nomme "Liga Profesional - Cloture" ce que les historiques
    appellent "Liga Profesional" : la comparaison stricte ne retrouvait alors
    aucun match, et la reference de championnat echouait pour tous les
    championnats a phases -- soit presque toute l'Amerique du Sud.

    On coupe donc au premier " - ". Deux phases d'un meme championnat se
    retrouvent ainsi confondues, ce qui est voulu : elles opposent les memes
    equipes au meme niveau, et c'est ce niveau que la reference mesure. Le
    procede fusionne aussi les groupes regionaux d'une meme division, ce qui
    reste acceptable pour une moyenne de buts.
    """
    return (name or "").split(" - ", 1)[0].strip()


def is_variant_team(name: str, competition: str = "") -> bool:
    """Vrai si le nom designe une equipe feminine, de jeunes ou une reserve."""
    if _TEAM_VARIANT.search(name or ""):
        return True
    return bool(competition and _COMPETITION_VARIANT.search(competition))


def filter_by_team(
    matches: Iterable[dict[str, Any]], team: str, include_variants: bool = False
) -> list[dict[str, Any]]:
    """Filtre sur l'une ou l'autre equipe, par sous-chaine insensible a la casse.

    Par defaut, chercher un club renvoie son equipe premiere : les equipes
    feminines, de jeunes et reserves sont ecartees, car "Monchengladbach"
    ramenait aussi "B. Monchengladbach II". Elles sont conservees si la
    recherche les vise explicitement ("Dortmund II", "Lyon F") ou si l'appelant
    demande tout.
    """
    needle = team.strip().lower()
    hits = [
        m
        for m in matches
        if needle in m["domicile"].lower() or needle in m["exterieur"].lower()
    ]
    if include_variants or is_variant_team(team.strip()):
        return hits
    return [
        m
        for m in hits
        if not is_variant_team(m["domicile"], m["championnat"])
        and not is_variant_team(m["exterieur"], m["championnat"])
    ]


def _cache_ttl() -> int:
    try:
        return int(os.getenv("CACHE_TTL", "3600"))
    except ValueError:
        return 3600


def sort_matches(matches: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tri par date, puis competition, puis heure.

    La competition passe avant l'heure pour que les matchs d'un meme championnat
    restent contigus : l'affichage les regroupe sous un seul en-tete. Un tri
    purement chronologique produirait un en-tete par match des que la source
    couvre plusieurs centaines de competitions le meme jour (cas de Flashscore).
    Le pays fait partie de la cle car des championnats homonymes coexistent
    ("Ligue 1" en France, en Tunisie et en Algerie).
    """
    return sorted(
        matches,
        key=lambda m: (
            m["date"],
            m["pays"],
            m["championnat"],
            m["heure"] or "99:99",
        ),
    )


def resolve_settings(
    provider: str | None, tz_name: str | None
) -> tuple[str, str]:
    """Valide et normalise provider + fuseau horaire."""
    provider = (provider or os.getenv("PROVIDER") or "flashscore").strip().lower()
    if provider not in PROVIDERS:
        raise ApiError(
            "Provider inconnu : %r. Valeurs possibles : %s"
            % (provider, ", ".join(sorted(PROVIDERS)))
        )
    tz_name = tz_name or os.getenv("TIMEZONE") or "Europe/Paris"
    _tz(tz_name)  # echoue tot si le fuseau est invalide
    return provider, tz_name


def get_matches(
    date: str,
    league: str | None = None,
    provider: str | None = None,
    tz_name: str | None = None,
    use_cache: bool = True,
    cache_ttl: int | None = None,
) -> list[dict[str, Any]]:
    """Retourne les matchs du jour `date` (YYYY-MM-DD), tries par heure.

    `league` filtre le championnat par sous-chaine insensible a la casse.
    """
    validate_date(date)
    provider, tz_name = resolve_settings(provider, tz_name)
    ttl = _cache_ttl() if cache_ttl is None else cache_ttl

    cache_key = "v%d|%s|%s|%s" % (CACHE_SCHEMA, provider, date, tz_name)
    matches = cache.get(cache_key, ttl) if use_cache else None
    if matches is None:
        matches = PROVIDERS[provider](date, tz_name)
        # Ecrit meme sous --no-cache : le drapeau signifie "ne relis pas une
        # entree existante", pas "n'ecris rien". Un resultat frais rafraichit
        # l'entree, et les trois fonctions mises en cache se comportent pareil.
        cache.set(cache_key, matches)

    if league:
        matches = filter_by_league(matches, league)

    return sort_matches(matches)
