"""Heure fiable : l'horloge de la machine, corrigee de son ecart a l'heure d'Internet.

Tout le projet raisonne sur « maintenant » : quels matchs sont a venir, lesquels
sont termines et verifiables, quel jour demander a la source, quand une fiche a
ete emise. Le 28/09/2026, l'horloge de la machine avait 13 h 32 de retard
(Windows ne se synchronisait plus : « Local CMOS Clock ») : le systeme se
croyait la veille au soir, ne voyait aucun match termine, demandait a la
source le mauvais jour, et le tableau de bord restait fige.

Ce module mesure l'ecart une fois par heure, en lisant l'en-tete `Date` de
quelques serveurs (celui de la source d'abord), et `maintenant()` le corrige.
Si aucun serveur ne repond, l'ecart reste le dernier connu (0 au depart) :
l'horloge de la machine sert alors telle quelle, et `etat()` le dit.

Le TTL de la mesure repose sur `time.monotonic()`, qui ne depend pas de l'heure
murale : corriger l'horloge ne fausse pas le calendrier des mesures.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone, tzinfo
from email.utils import parsedate_to_datetime
from typing import Any

#: Serveurs dont l'en-tete `Date` fait foi, dans l'ordre d'interrogation.
SERVEURS = (
    "https://www.flashscore.fr/",
    "https://www.google.com/",
    "https://www.cloudflare.com/",
)

#: Duree de validite d'une mesure, en secondes.
TTL = 3600

#: Ecart au-dela duquel l'horloge de la machine est signalee comme fausse.
TOLERANCE = 120

_etat: dict[str, Any] = {"decalage": 0.0, "mesure_le": None, "source": None, "erreur": None}


def _mesurer() -> None:
    """Interroge les serveurs jusqu'au premier qui repond ; garde l'ecart."""
    import requests

    from ibet.sources import api_client

    for url in SERVEURS:
        try:
            avant = time.time()
            reponse = requests.head(url, timeout=5, allow_redirects=False,
                                    verify=api_client._verify())
            apres = time.time()
            entete = reponse.headers.get("Date")
            if not entete:
                continue
            internet = parsedate_to_datetime(entete).timestamp()
            # L'en-tete est arrondi a la seconde ; on le compare au milieu de
            # l'aller-retour.
            _etat.update(decalage=internet - (avant + apres) / 2, source=url, erreur=None)
            _etat["mesure_le"] = time.monotonic()
            return
        except Exception as exc:  # noqa: BLE001 - un serveur injoignable n'est pas fatal
            _etat["erreur"] = "%s : %s" % (url, exc)
    _etat["mesure_le"] = time.monotonic()


def decalage(forcer: bool = False) -> float:
    """Secondes a ajouter a l'horloge de la machine pour avoir l'heure reelle."""
    mesure = _etat["mesure_le"]
    if forcer or mesure is None or time.monotonic() - mesure > TTL:
        _mesurer()
    return float(_etat["decalage"])


def maintenant(tz: tzinfo | None = None) -> datetime:
    """`datetime.now(tz)`, corrige de l'ecart a l'heure d'Internet."""
    return datetime.now(tz or timezone.utc) + timedelta(seconds=decalage())


def etat(forcer: bool = False) -> dict[str, Any]:
    """Ce que l'horloge sait d'elle-meme, pour l'afficher ou le signaler."""
    ecart = decalage(forcer)
    machine = datetime.now(timezone.utc)
    return {
        "decalage_secondes": round(ecart),
        "decalage_minutes": round(ecart / 60),
        "horloge_machine_utc": machine.isoformat(timespec="seconds"),
        "heure_reelle_utc": (machine + timedelta(seconds=ecart)).isoformat(timespec="seconds"),
        "source": _etat["source"],
        "mesuree": _etat["source"] is not None,
        "machine_a_corriger": abs(ecart) > TOLERANCE,
        "erreur": _etat["erreur"],
    }


def fixer(secondes: float) -> None:
    """Impose un ecart (tests) : aucune requete n'est faite pendant une heure."""
    _etat.update(decalage=float(secondes), source="fixe", erreur=None)
    _etat["mesure_le"] = time.monotonic()
