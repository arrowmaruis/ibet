"""Planificateur : emettre, reemettre juste avant le match, verifier -- sans intervention.

Trois constats l'ont rendu necessaire (29/09/2026) :

  - **l'arbitre n'etait connu dans aucune fiche** (critere 12 absent ou partiel
    70 fois sur 70) et **les compositions etaient partielles dans 93 %** : les
    deux sont publies une heure environ avant le coup d'envoi, alors que les
    fiches etaient emises la veille. Le modele des cartons 3.x, construit sur
    l'arbitre, perdait ainsi son principal apport ;
  - **103 fiches en trois semaines**, dont 27 de qualifications CAN et 3 de
    LaLiga : trop peu, et trop desequilibre, pour juger une version ;
  - le bilan ne bougeait que si quelqu'un cliquait « Verifier ».

Toutes les `INTERVALLE_MINUTES`, un passage :

  1. **verifie** les fiches dont le match est termine ;
  2. **reemet** une fois les fiches dont le match commence dans 30 a 90 minutes
     (arbitre designe, compositions publiees) ; la premiere emission reste
     tracee dans la fiche (`premiere_emission`) ;
  3. toutes les `EMISSION_HEURES`, **emet** les matchs a venir des grandes
     competitions sur `JOURS` jours.

Toute emission passe par la garde de `forecast` : jamais a moins de 5 minutes
du coup d'envoi, a l'heure fiable (`horloge`).

Usage :
    python -m ibet planificateur             # tourne en continu
    python -m ibet planificateur --une-fois  # un seul passage (tache Windows)
Le serveur (`python -m ibet serveur`) le fait tourner lui-meme en arriere-plan.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ibet.evaluation import verify
from ibet.prevision import forecast, predict
from ibet.sources import api_client, horloge
from ibet.stockage import store

INTERVALLE_MINUTES = 10
#: Fenetre de reemission, en minutes avant le coup d'envoi. Les compositions
#: sortent environ une heure avant ; a moins de 30 minutes, le calcul (qui peut
#: durer plusieurs minutes) risquerait de buter sur la garde de 5 minutes.
FENETRE_REEMISSION = (30.0, 90.0)
EMISSION_HEURES = 6
JOURS = 2
MAX_PAR_EMISSION = 20

#: Ce que le dernier passage a fait, pour l'API et le journal.
etat: dict[str, Any] = {"dernier_passage": None, "derniere_emission": None,
                        "verifiees": 0, "reemises": 0, "emises": 0, "erreurs": []}


def _journal(message: str) -> None:
    heure = horloge.maintenant(timezone.utc).astimezone().strftime("%d/%m %H:%M")
    print("[planificateur %s] %s" % (heure, message), flush=True)


def _coup_denvoi_utc(fiche: dict[str, Any], tz_name: str) -> datetime | None:
    try:
        local = datetime.strptime(fiche.get("coup_denvoi_local") or "", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return local.replace(tzinfo=ZoneInfo(tz_name)).astimezone(timezone.utc)


def a_reemettre(fiche: dict[str, Any], tz_name: str,
                fenetre: tuple[float, float] = FENETRE_REEMISSION) -> bool:
    """La fiche doit-elle etre reemise maintenant ? Une seule fois par match."""
    if fiche.get("emission") == "avant-match":
        return False
    coup = _coup_denvoi_utc(fiche, tz_name)
    if coup is None:
        return False
    minutes = (coup - horloge.maintenant(timezone.utc)).total_seconds() / 60.0
    return fenetre[0] <= minutes <= fenetre[1]


def reemettre_avant_match(tz_name: str, use_cache: bool = True) -> int:
    """Reemet les fiches dont le match approche. Rend le nombre de fiches reemises."""
    faites = 0
    fiches = [f for f in store.pending() if a_reemettre(f, tz_name)]
    matchs_du_jour: dict[str, list[dict[str, Any]]] = {}
    for fiche in fiches:
        jour = (fiche.get("coup_denvoi_local") or "")[:10]
        if jour not in matchs_du_jour:
            matchs_du_jour[jour] = forecast.upcoming(jour, tz_name, None, use_cache, variants=True)
        match = next((m for m in matchs_du_jour[jour] if m.get("match_id") == fiche.get("match_id")), None)
        if not match:
            continue
        if forecast.trop_tard(match):
            continue
        try:
            record = forecast.forecast_match(match, tz_name, use_cache)
        except (predict.NotEnoughData, api_client.ApiError) as exc:
            etat["erreurs"].append("reemission %s : %s" % (fiche.get("match"), exc))
            continue
        # Deuxieme controle : le calcul a pu durer.
        if forecast.trop_tard(match):
            continue
        record["emission"] = "avant-match"
        record["premiere_emission"] = {
            "emis_le": fiche.get("emis_le"),
            "minutes_avant_coup_denvoi": fiche.get("minutes_avant_coup_denvoi"),
        }
        store.save(record)
        faites += 1
        arbitre = next((c for c in ((record.get("contexte") or {}).get("criteres") or [])
                        if c.get("numero") == 12), {})
        _journal("reemise %s (%s min avant ; arbitre : %s)"
                 % (record.get("match"), record.get("minutes_avant_coup_denvoi"),
                    "connu" if arbitre.get("disponible") not in (False, None) else "inconnu"))
    return faites


def emettre_a_venir(tz_name: str, use_cache: bool = True) -> int:
    """Emet les matchs a venir des grandes competitions, sur `JOURS` jours."""
    debut = horloge.maintenant(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    emises, avertissements = forecast.emit(
        JOURS, debut, tz_name, None, MAX_PAR_EMISSION, use_cache, majors=True
    )
    for a in avertissements[:10]:
        etat["erreurs"].append("emission : %s" % a)
    return emises


def passage(tz_name: str | None = None, emettre: bool | None = None) -> dict[str, Any]:
    """Un passage complet. `emettre` None : selon le delai depuis la derniere emission."""
    _, tz_name = api_client.resolve_settings("flashscore", tz_name)
    etat["erreurs"] = []
    maintenant = horloge.maintenant(timezone.utc)
    try:
        if verify.due_count(tz_name):
            rapport = verify.verify_store(tz_name)
            tranchees = sum(1 for l in rapport if l.get("statut") == "verifie")
            etat["verifiees"] += tranchees
            if tranchees:
                _journal("%d fiche(s) tranchee(s)" % tranchees)
    except Exception as exc:  # noqa: BLE001 - un echec n'arrete pas le passage
        etat["erreurs"].append("verification : %s" % exc)
    try:
        etat["reemises"] += reemettre_avant_match(tz_name)
    except Exception as exc:  # noqa: BLE001
        etat["erreurs"].append("reemission : %s" % exc)
    derniere = etat["derniere_emission"]
    if emettre is None:
        emettre = derniere is None or maintenant - derniere >= timedelta(hours=EMISSION_HEURES)
    if emettre:
        try:
            n = emettre_a_venir(tz_name)
            etat["emises"] += n
            etat["derniere_emission"] = maintenant
            _journal("%d fiche(s) emise(s) sur %d jour(s)" % (n, JOURS))
        except Exception as exc:  # noqa: BLE001
            etat["erreurs"].append("emission : %s" % exc)
    etat["dernier_passage"] = maintenant
    for e in etat["erreurs"]:
        _journal(e)
    return etat


def resume() -> dict[str, Any]:
    """Etat lisible par l'API."""
    return {
        "dernier_passage": etat["dernier_passage"].isoformat(timespec="seconds") if etat["dernier_passage"] else None,
        "derniere_emission": etat["derniere_emission"].isoformat(timespec="seconds") if etat["derniere_emission"] else None,
        "verifiees": etat["verifiees"], "reemises": etat["reemises"], "emises": etat["emises"],
        "erreurs": etat["erreurs"][:5],
        "intervalle_minutes": INTERVALLE_MINUTES,
    }


def tourner(tz_name: str | None = None, delai_premiere_emission: float = 120.0,
            avec_emission: bool = True) -> None:
    """Boucle sans fin. La premiere emission attend `delai_premiere_emission`
    secondes : un serveur qui redemarre a chaque modification du code ne doit
    pas relancer une emission complete a chaque fois. `avec_emission` False :
    verifier et reemettre seulement."""
    debut = time.monotonic()
    while True:
        if not avec_emission or time.monotonic() - debut < delai_premiere_emission:
            emettre: bool | None = False
        else:
            emettre = None
        passage(tz_name, emettre)
        time.sleep(INTERVALLE_MINUTES * 60)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m ibet planificateur",
                                     description="Verifie, reemet avant le match et emet, en continu.")
    parser.add_argument("--une-fois", action="store_true", help="Un seul passage, puis s'arrete")
    parser.add_argument("--sans-emission", action="store_true",
                        help="Verifier et reemettre seulement, sans emettre de nouvelles fiches")
    parser.add_argument("--tz", default=None)
    args = parser.parse_args(argv)
    if args.une_fois:
        passage(args.tz, emettre=not args.sans_emission)
        print(resume())
        return 0
    tourner(args.tz, delai_premiere_emission=0.0, avec_emission=not args.sans_emission)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
