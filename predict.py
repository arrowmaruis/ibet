"""Prevision statistique a partir de la forme recente des deux equipes.

Aucune formule maison : le modele est celui utilise dans la litterature sur la
prevision du football.

  1. Nombre attendu d'evenements (buts, corners, tirs cadres, cartons) pour
     chaque equipe : modele attaque / defense de Maher (1982), "Modelling
     association football scores", Statistica Neerlandica 36(3), normalise par
     la moyenne de la competition.

         Attaque_i  = (evenements produits par i et par match) / moyenne
         Defense_j  = (evenements concedes par j et par match) / moyenne
         lambda_dom = Attaque_dom * Defense_ext * (moyenne a domicile)
         lambda_ext = Attaque_ext * Defense_dom * (moyenne a l'exterieur)

     Deux equipes exactement moyennes redonnent la moyenne de la competition.

     Les moyennes par equipe ne sont pas prises brutes : chaque match est
     d'abord ramene a un adversaire moyen ET a un lieu neutre (voir
     `_adjusted_averages`). Sans reference de competition, le modele retombe sur
     la forme elementaire lambda = (produit + concede par l'adversaire) / 2.

  2. Distribution du nombre d'evenements : loi de Poisson de parametre lambda.
     C'est le choix classique pour un comptage d'evenements sur une duree fixe,
     et l'hypothese retenue par Maher puis par Dixon et Coles (1997), "Modelling
     Association Football Scores and Inefficiencies in the Football Betting
     Market", Applied Statistics 46(2).

         P(X = k) = exp(-lambda) * lambda^k / k!

  3. Probabilites de resultat : les deux scores sont supposes independants, donc
     la loi jointe est le produit des deux lois de Poisson. On somme les cases
     de la matrice selon le resultat.

  4. Ponderation par l'anciennete : un match d'il y a trois mois en dit moins
     qu'un match de la semaine derniere. Le poids decroit exponentiellement avec
     l'age, comme chez Dixon et Coles. La demi-vie est reglee par `Params`.

  5. Dependance des petits scores : le facteur tau de Dixon et Coles corrige les
     quatre cases 0-0, 1-0, 0-1, 1-1, que l'independance des deux Poisson
     sous-estime. Il est pilote par `Params.rho`, et vaut l'identite a rho = 0 --
     valeur retenue par defaut, faute d'un gain mesurable sur l'echantillon
     disponible (voir README, "Reglage des parametres").

Limites assumees, rappelees a l'affichage :

  - Corners et cartons sont plus disperses qu'une loi de Poisson (la variance
    observee depasse la moyenne). Les probabilites de seuil pour ces deux
    grandeurs sont donc trop tranchees aux extremes ; celles des buts, ou
    l'ajustement de Poisson tient, ne le sont pas.
  - Les trois reglages de `Params` ont ete choisis par comparaison de log-
    vraisemblance sur des matchs passes (`backtest.tune`), pas estimes par
    maximum de vraisemblance sur plusieurs saisons comme le fait la
    litterature : l'echantillon d'evaluation recoupe en partie celui du reglage.
  - Echantillon de quelques matchs : l'incertitude est large. Les probabilites
    affichees decrivent le modele, pas la realite.

Architecture
------------

Ce module est le chef d'orchestre. Chaque evenement a son modele dedie dans le
paquet `modeles` (buts, issue, corners, tirs cadres, cartons jaunes, et le xG
en auxiliaire) ; `build` les fait travailler ensemble :

  1. chaque modele ESTIME son nombre attendu, independamment des autres ;
  2. le contexte s'intercale (criteres 5 et 11, corrections combinees) ;
  3. chaque modele PREVOIT sa grandeur -- le modele des buts recoit le xG et
     les notes globales, et passe ses nombres definitifs au modele de l'issue.

Les noms historiques (`METRICS`, `DISPERSION`, `score_matrix`,
`offer_candidates`...) restent exposes ici : le reste du projet n'a pas a
savoir comment les modeles sont ranges.
"""

from __future__ import annotations

from typing import Any

import context

import modeles
from modeles import BUTS, MODELES, XG

# Les imports qui suivent sont en bonne partie des RE-EXPORTS : le reste du
# projet et les tests lisent ces noms sous `predict.`, et ils y restent.
from modeles.base import Metric, ModeleEvenement, _ranges, corriger
from modeles.base import dispersion_effective as _dispersion_effective
from modeles.buts import (
    GOALS_LINE,
    LAMBDA_MIN,
    POIDS_MODELE,
    melange_xg_effectif,
    melanger_lambdas,
)
from modeles.cartons import CARDS_LINE
from modeles.corners import CORNERS_LINE
from modeles.estimation import (
    _adjusted_averages,
    _competition_key,
    _entry_weight,
    _lambda,
    _maher_lambdas,
    _observation,
    _official,
    _raw_averages,
    _recency_weight,
    _reference,
    _scope,
    _shrink,
    _usable,
    _weighted_mean,
    forces_des_equipes,
    shrink_lambda,
    venue_edge,
)
from modeles.issue import both_teams_score, most_likely_scores, outcome_probabilities
from modeles.lois import (
    MAX_EVENTS,
    _binomiale_pmf,
    _blend,
    _grid_probability,
    _grid_size,
    count_distribution,
    count_pmf,
    dispersion_du_total,
    dixon_coles_tau,
    over_probability,
    poisson_distribution,
    poisson_pmf,
    score_matrix,
    team_over_probability,
    total_over_probability,
)
from modeles.offres import (
    LADDER_FAMILIES,
    MARKET_MIN,
    MARKET_PER_FAMILY,
    OFFER_CEILING,
    OFFER_MAX,
    OFFER_MIN,
    OFFER_PER_FAMILY,
    market_board,
    select_offers,
)
from modeles.reglages import (
    DEFAULT_PARAMS,
    ESTIMATION_DISPERSION,
    HALF_LIFE,
    HOME_EDGE,
    LAMBDA_SHRINK,
    RHO,
    SHRINKAGE,
    XG_ECHANTILLON,
    NotEnoughData,
    Params,
)
from modeles.tirs_cadres import SHOTS_LINE

# Vues d'ensemble tirees des modeles : chaque valeur vit dans le fichier de son
# evenement, et ces tables n'en sont que le releve.
METRICS = [m.metric for m in MODELES]
METRIQUE_XG = XG.metric
CALIBRATION = {m.cle: m.calibration for m in MODELES}
DISPERSION = {m.cle: m.dispersion for m in MODELES}
CORRELATION = {m.cle: m.correlation for m in MODELES}


def _modele(metric: Metric) -> ModeleEvenement:
    return modeles.modele(metric.key)


def effective_dispersion(
    key: str, sample: float, params: Params = DEFAULT_PARAMS
) -> float:
    """Rapport variance / moyenne effectif d'une grandeur (voir `modeles.base`)."""
    return _dispersion_effective(DISPERSION.get(key, 1.0), sample, params)


def _metric_total_over(
    metric: Metric,
    lam_home: float,
    lam_away: float,
    line: float,
    rho: float,
    phi: float | None = None,
) -> float:
    """P(total > line) sous la loi du modele de cette grandeur."""
    return _modele(metric).probabilite_total(lam_home, lam_away, line, rho, phi)


def _ladders(
    metric: Metric,
    lam_home: float,
    lam_away: float,
    rho: float = RHO,
    phi_home: float | None = None,
    phi_away: float | None = None,
) -> dict[str, Any]:
    return _modele(metric).echelles(lam_home, lam_away, rho, phi_home, phi_away)


def offer_candidates(
    metric: Metric,
    teams: tuple[str, str],
    lam_home: float,
    lam_away: float,
    outcome: dict[str, float] | None = None,
    btts: float | None = None,
    rho: float = RHO,
    phi_home: float | None = None,
    phi_away: float | None = None,
) -> list[dict[str, Any]]:
    """Toutes les propositions d'une grandeur, avant selection.

    `outcome` et `btts` sont ceux du modele de l'issue ; ses propositions sont
    greffees sur la grandeur comme `build` le fait pour les buts.
    """
    return _modele(metric).candidats(
        teams, lam_home, lam_away, rho, phi_home, phi_away,
        modeles.ISSUE.candidats(teams, outcome, btts),
    )


_corriger = corriger


def build(
    match: dict[str, Any],
    form: dict[str, Any],
    baseline: dict[str, Any] | None = None,
    params: Params = DEFAULT_PARAMS,
    with_candidates: bool = False,
    collecte: dict[str, Any] | None = None,
    cotes: dict[str, Any] | None = None,
    historique_cotes: list[dict[str, Any]] | None = None,
    lambdas_forces: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Prevision complete d'un match a partir de sa forme recente.

    `form` est la structure rendue par `api_client.get_form`. Les grandeurs
    autres que les buts exigent que la forme ait ete calculee avec
    `with_stats=True`, sinon elles sont simplement absentes ; leur normalisation
    par la competition exige en plus que la reference ait ete demandee avec
    `with_stats=True`, faute de quoi elles retombent sur la moyenne brute.

    `params` porte les reglages du modele et se retrouve dans la fiche : une
    prevision qui ne dit pas comment elle a ete produite n'est pas verifiable.
    `backtest.tune` s'en sert pour les comparer.

    `collecte` est le contexte rendu par `context.collecter` -- les quatorze
    criteres. Il est FACULTATIF : sans lui, la fonction se comporte exactement
    comme avant, ce qui garantit que le contexte peut etre mesure contre son
    absence sur les memes matchs. Avec lui, trois choses changent, et rien
    d'autre :

      - l'historique est repondere avant d'etre moyenne (critere 4) ;
      - le nombre de buts attendu est melange a celui tire des xG (critere 11) ;
      - les nombres attendus recoivent les corrections combinees, plafonnees.

    Chacune est enregistree dans la fiche avec le critere qui l'a produite : une
    correction qu'on ne pourrait pas retrouver apres coup serait indefendable.
    """
    home_entries = _official(form["domicile"]["matchs"])
    away_entries = _official(form["exterieur"]["matchs"])
    if not home_entries or not away_entries:
        raise NotEnoughData(
            "Aucun match officiel dans l'historique de l'une des deux equipes "
            "(les amicaux sont ecartes du calcul)."
        )

    teams = (form["domicile"]["equipe"], form["exterieur"]["equipe"])
    competition = match.get("championnat", "")
    # Date de reference de la ponderation : le coup d'envoi du match prevu. Sur
    # un match passe rejoue par le backtest, c'est bien celui-la qu'il faut, et
    # non aujourd'hui, sans quoi l'anciennete serait comptee depuis le mauvais
    # bord et la ponderation cesserait d'etre celle qu'aurait eue la prevision.
    as_of = match.get("kickoff_utc") or ""
    poids_contexte = (collecte or {}).get("poids_entrees") or {}

    def estimer(modele: ModeleEvenement) -> dict[str, Any] | None:
        return modele.estimer(
            home_entries, away_entries, competition, baseline,
            params, as_of, poids_contexte,
        )

    # --- Premiere phase : chaque modele estime, sans rien conclure ----------
    estimations: dict[str, dict[str, Any]] = {}
    for modele in MODELES:
        found = estimer(modele)
        if found is not None:
            estimations[modele.cle] = found

    if not estimations:
        raise NotEnoughData("Aucune grandeur calculable sur cet echantillon.")

    # --- Le contexte s'intercale ici ---------------------------------------
    contexte: dict[str, Any] | None = None
    corrections: dict[str, dict[str, float]] = {}
    criteres_modele: list[Any] = []
    lam_xg: tuple[float, float] | None = None
    melange_xg = 0.0

    if collecte is not None:
        # Le modele auxiliaire du xG n'est estime que pour servir le contexte
        # et le modele des buts.
        estimation_xg = estimer(XG)
        lam_xg = estimation_xg["lambda"] if estimation_xg else None
        buts = estimations.get(BUTS.cle)
        criteres_modele, corrections = context.confronter(
            collecte,
            buts["lambda"] if buts else (0.0, 0.0),
            lam_xg,
            (estimation_xg or {}).get("effectif_efficace", (0.0, 0.0)),
        )
        melange = collecte["poids"].xg if lam_xg is not None else 0.0
        # Le poids du critere est un plancher : l'echantillon peut le relever
        # quand les buts observes sont trop peu nombreux pour valoir le xG.
        # C'est l'effectif des BUTS qui commande -- c'est lui qui se degrade --
        # et le plus petit des deux cotes, parce qu'une equipe mal estimee suffit
        # a rendre le lambda du match incertain.
        effectifs = (estimations.get(BUTS.cle) or {}).get("effectif_efficace")
        melange_xg = (
            melange_xg_effectif(melange, min(effectifs), params.xg_echantillon)
            if effectifs
            else melange
        )

    # Ce que chaque modele recoit de l'exterieur. Seuls les buts en ont : le
    # xG (critere 11) et les notes attaque / defense globales.
    apports: dict[str, dict[str, Any]] = {
        BUTS.cle: {"xg": (lam_xg, melange_xg), "forces": lambdas_forces},
    }

    # --- Seconde phase : chaque modele corrige, puis prevoit ---------------
    prediction: dict[str, Any] = {
        "equipes": teams,
        # Une prevision ne vaut que pour un match a venir. Sur un match joue,
        # le calcul reste juste mais ce n'est plus une prevision : le signaler
        # evite de prendre une retrodiction pour une performance du modele.
        "a_venir": match.get("statut") == "A venir",
        "statut": match.get("statut", ""),
        "score_reel": (
            "%s - %s" % (match.get("score_domicile"), match.get("score_exterieur"))
            if match.get("score_domicile") is not None
            else ""
        ),
        "echantillon": {
            "domicile": len(home_entries),
            "exterieur": len(away_entries),
            "amicaux_ecartes": (
                len(form["domicile"]["matchs"]) - len(home_entries)
                + len(form["exterieur"]["matchs"]) - len(away_entries)
            ),
        },
        # Affichees a titre de contexte : le critere 5 s'en sert, le modele lui-
        # meme n'a toujours pas de terme de confrontation directe.
        "confrontations": form.get("confrontations") or [],
        "reglage": params._asdict(),
        "grandeurs": [],
    }

    outcome_final: dict[str, float] | None = None
    for modele in MODELES:
        estimation = estimations.get(modele.cle)
        if estimation is None:
            continue
        entry = modele.prevoir(
            estimation, teams, baseline, params,
            corrections.get(modele.cle), with_candidates, apports.get(modele.cle),
        )
        if "resultat" in entry:
            # L'issue, publiee par le modele de l'issue dans la fiche des buts.
            outcome_final = entry["resultat"]
        prediction["grandeurs"].append(entry)

    if not prediction["grandeurs"]:
        raise NotEnoughData("Aucune grandeur calculable sur cet echantillon.")

    if collecte is not None:
        contexte = context.finaliser(
            collecte,
            criteres_modele,
            {key: est["effectif_efficace"] for key, est in estimations.items()},
            corrections,
            outcome_final,
            cotes,
            historique_cotes,
        )
        prediction["contexte"] = contexte
        prediction["confiance"] = contexte["confiance"]
        prediction["reglage"] = dict(
            prediction["reglage"], poids_contexte=contexte["poids"]
        )
        # La confiance est reportee sur chaque grandeur : c'est la ou le lecteur
        # decide, et un 88 % estime sur trois matchs n'est pas le meme 88 %
        # qu'un 88 % estime sur douze.
        for entry in prediction["grandeurs"]:
            entry["confiance"] = contexte["confiance"]["par_grandeur"].get(
                entry["cle"]
            )

    return prediction
