"""Point d'entree unique : `python -m ibet <commande> [options]`.

Sans commande (ou avec des options seulement), c'est la ligne de commande
principale -- matchs, statistiques, forme, prevision, backtest, valeur :

    python -m ibet --date 2026-09-10 --league "Ligue 1" --predict

Chaque commande a son aide : `python -m ibet <commande> --help`.
"""

from __future__ import annotations

import importlib
import sys

# commande -> (module, description). L'ordre est celui de l'aide.
COMMANDES: dict[str, tuple[str, str]] = {
    "matchs": ("ibet.interfaces.cli", "matchs, stats, forme, prevision, backtest, valeur (commande par defaut)"),
    "serveur": ("ibet.interfaces.serveur", "API HTTP pour le front (port 8000, rechargement auto)"),
    "emettre": ("ibet.prevision.forecast", "emet et enregistre les previsions des matchs a venir"),
    "verifier": ("ibet.evaluation.verify", "tranche les previsions dont le match est fini"),
    "etude": ("ibet.evaluation.etude", "compare ce que chaque version de chaque modele a donne"),
    "forces": ("ibet.prevision.forces", "reconstruit les notes attaque / defense (donnees/forces.json)"),
    "rattraper": ("ibet.collecte.resultats", "retrouve les resultats sortis de la fenetre de la source"),
    "rattraper-feuilles": ("ibet.collecte.feuilles", "rattrape les feuilles de match (arbitres, cartons)"),
    "rattraper-joueurs": ("ibet.collecte.joueurs", "releve les statistiques par joueur"),
    "arbitres": ("ibet.stockage.arbitres", "releve l'historique des arbitres (worldfootball.net, par saison)"),
    "historique": ("ibet.collecte.historique", "verse l'historique long des grands championnats (football-data.co.uk)"),
    "entraineurs": ("ibet.stockage.entraineurs", "base des entraineurs : systeme, style, pressing, evenements"),
    "catalogue": ("ibet.interfaces.catalogue", "styles de jeu des joueurs, par equipe ou championnat"),
    "mesurer-sens": ("ibet.evaluation.mesure_sens", "calibration des « plus de » et « moins de », par grandeur"),
    "mesurer-cartons": ("ibet.evaluation.mesure_cartons", "mesure l'apport de l'arbitre, des joueurs, de l'entraineur"),
    "mesurer-styles": ("ibet.evaluation.mesure_styles", "mesure l'apport des styles aux corners et tirs cadres"),
    "mesurer-historique": ("ibet.evaluation.mesure_historique", "mesure l'apport de l'historique long et des cotes aux corners"),
    "base": ("ibet.stockage.store", "cree les tables, importe l'historique, reprend le cache"),
    "certificats": ("ibet.sources.certificats", "genere le bundle de certificats (antivirus / proxy HTTPS)"),
    "tests": ("tests.test_stats", "lance les tests (sans reseau)"),
}


def aide() -> str:
    largeur = max(len(nom) for nom in COMMANDES)
    lignes = [
        "usage : python -m ibet [commande] [options]",
        "",
        "Sans commande : la ligne de commande principale (`matchs`).",
        "",
        "commandes :",
    ]
    lignes += ["  %-*s  %s" % (largeur, nom, desc) for nom, (_, desc) in COMMANDES.items()]
    lignes += ["", "Aide d'une commande : python -m ibet <commande> --help"]
    return "\n".join(lignes)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("aide", "-h", "--help", "help"):
        print(aide())
        return 0
    if argv and argv[0] in COMMANDES:
        nom, argv = argv[0], argv[1:]
    elif argv and not argv[0].startswith("-"):
        print("Commande inconnue : %s\n\n%s" % (argv[0], aide()), file=sys.stderr)
        return 2
    else:
        nom = "matchs"

    # Pour que l'aide de chaque commande affiche la commande a taper.
    sys.argv[0] = "python -m ibet %s" % nom
    module = importlib.import_module(COMMANDES[nom][0])
    return module.main(argv) or 0


if __name__ == "__main__":
    raise SystemExit(main())
