"""Estimation du nombre attendu d'evenements : modele attaque / defense de Maher.

Moteur commun a tous les modeles d'evenement. Il ne sait rien de l'evenement
qu'il estime : il lit le champ qu'on lui designe (`field`, None pour les buts,
qui se lisent au score) et rend deux nombres attendus. C'est chaque modele qui
decide ensuite quelle loi, quelle dispersion et quels marches en tirer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from .reglages import DEFAULT_PARAMS, SHRINKAGE, Params


def _official(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Matchs officiels seulement : un amical de pre-saison fausse la moyenne."""
    return [e for e in entries if not e.get("amical")]


def _competition_key(name: str) -> str:
    """Nom de competition sans son suffixe de phase.

    Meme convention que `api_client.normalize_competition` : le flux du jour
    nomme "Liga Profesional - Cloture" ce que les historiques appellent "Liga
    Profesional". La comparaison stricte ne retrouvait alors aucun match, et le
    perimetre commun ne se restreignait jamais dans les championnats a phases.
    Dupliquee ici en une ligne plutot qu'importee : ce module reste sans
    dependance, donc testable sans reseau ni cle.
    """
    return (name or "").split(" - ", 1)[0].strip()


def _observation(entry: dict[str, Any], field: str | None) -> tuple[float, float] | None:
    """(produit, concede) par l'equipe suivie sur ce match, ou None si absent."""
    if field is None:
        produced, conceded = entry.get("buts_pour"), entry.get("buts_contre")
    else:
        stats = entry.get("stats") or {}
        produced = (stats.get("pour") or {}).get(field)
        conceded = (stats.get("contre") or {}).get(field)
    if produced is None or conceded is None:
        return None
    return float(produced), float(conceded)


def _usable(
    entries: list[dict[str, Any]], field: str | None
) -> list[dict[str, Any]]:
    """Matchs sur lesquels cette grandeur est effectivement mesuree.

    Filtrer d'emblee evite deux defauts : une taille d'echantillon surestimee
    passee a la regularisation (donc une regularisation trop faible), et un
    nombre de matchs annonce a l'utilisateur superieur a celui reellement
    moyenne.
    """
    return [e for e in entries if _observation(e, field) is not None]


def _entry_weight(
    entry: dict[str, Any], as_of: str, half_life: float, extra: dict[str, float]
) -> float:
    """Poids total d'un match d'historique : anciennete fois contexte.

    Deux ponderations independantes se composent ici. La premiere repond a
    « ce match est-il recent ? » (Dixon et Coles) ; la seconde, fournie par
    `context` au titre du critere 4, repond a « l'adversaire de ce jour-la
    ressemble-t-il a celui qui vient ? ». Les deux portent sur la meme question
    -- ce match nous renseigne-t-il sur le prochain ? -- et se multiplient donc.

    `extra` est vide quand aucun contexte n'est fourni : le poids est alors
    exactement celui d'avant.
    """
    weight = _recency_weight(entry, as_of, half_life)
    return weight * extra.get(entry.get("match_id", ""), 1.0)


def _recency_weight(
    entry: dict[str, Any], as_of: str, half_life: float
) -> float:
    """Poids d'un match, decroissant exponentiellement avec son age.

    Un match d'il y a trois mois en dit moins qu'un match de la semaine
    derniere : l'effectif a change, la forme aussi. Dixon et Coles introduisent
    exactement cette ponderation ; on l'exprime ici par une demi-vie, plus
    lisible qu'un taux (a `half_life` jours, le match compte pour moitie).

    Poids 1 si l'age ne peut pas etre etabli -- ne pas savoir dater un match ne
    doit pas revenir a l'effacer.
    """
    if half_life <= 0 or not as_of:
        return 1.0
    stamp = entry.get("kickoff_utc")
    if not stamp:
        return 1.0
    try:
        age = (datetime.fromisoformat(as_of) - datetime.fromisoformat(stamp)).days
    except (TypeError, ValueError):
        return 1.0
    if age <= 0:
        return 1.0
    return 0.5 ** (age / half_life)


def _weighted_mean(
    values: Sequence[float], weights: Sequence[float]
) -> float | None:
    total = sum(weights)
    if total <= 0:
        return None
    return sum(v * w for v, w in zip(values, weights)) / total


def _shrink(rating: float, sample: float, shrinkage: float = SHRINKAGE) -> float:
    """Ramene une force vers 1 (la moyenne) selon la taille de l'echantillon.

    `shrinkage` est expose pour que la valeur puisse etre variee -- par un test,
    ou par une evaluation qui cherche celle qui prevoit le mieux. La valeur par
    defaut reste la constante du module, seule employee par le modele.
    """
    if sample <= 0:
        return 1.0
    return (sample * rating + shrinkage) / (sample + shrinkage)


def _reference(
    baseline: dict[str, Any] | None, field: str | None
) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]]]:
    """Moyennes et forces de la competition pour cette grandeur.

    Les buts se lisent a la racine de la reference ; les autres grandeurs dans
    `stats` / `forces_stats`, que `api_client.league_baseline(with_stats=True)`
    remplit au meme titre. Les forces detaillees y sont indexees equipe puis
    grandeur : on les remet a plat pour que le reste du module n'ait qu'une
    seule forme a connaitre.

    Rend (None, {}) si la reference n'existe pas -- corners en division non
    couverte, par exemple. L'appelant retombe alors sur la forme elementaire.
    """
    if not baseline:
        return None, {}
    if field is None:
        means: dict[str, Any] | None = baseline
        strengths = baseline.get("forces") or {}
    else:
        means = (baseline.get("stats") or {}).get(field)
        nested = baseline.get("forces_stats") or {}
        strengths = {
            team: fields[field]
            for team, fields in nested.items()
            if isinstance(fields, dict) and field in fields
        }
    if not means or not means.get("moyenne_globale"):
        return None, {}
    return means, strengths


def forces_des_equipes(
    baseline: dict[str, Any] | None,
    teams: tuple[str, str],
    field: str | None,
    params: Params = DEFAULT_PARAMS,
) -> dict[str, Any] | None:
    """Forces d'attaque et de defense des deux equipes, brutes et regularisees.

    Le modele les CALCULE deja -- c'est sur elles que repose toute la correction
    de calendrier -- mais il ne les enregistrait nulle part : la fiche disait
    « corrige du niveau des adversaires » sans jamais montrer de combien, ni
    dans quel sens. Un nombre attendu de 1.9 ne se lit pas de la meme facon
    selon qu'il vient d'une attaque a 1.4 contre une defense a 1.0, ou de deux
    forces moyennes qu'un avantage du terrain a deplacees.

    Deux valeurs par force, et la distinction est le coeur du modele :

      - **brute** : le rapport observe sur l'echantillon, `production de
        l'equipe / moyenne de la competition`. Sur quatre matchs, un 1.6 tient
        autant du bruit que du signal ;
      - **regularisee** : la meme, ramenee vers 1 proportionnellement a la
        taille de l'echantillon (`_shrink`, k = SHRINKAGE). C'est CELLE-CI que
        le modele emploie, et l'ecart entre les deux dit combien l'echantillon
        pesait vraiment.

    Un ecart important entre brute et regularisee est une information, pas un
    defaut : il signale une equipe dont le modele se mefie faute de matchs.

    Rend None quand la competition n'a pas de reference pour cette grandeur --
    corners en division non couverte, coupe entre deux championnats. C'est le
    meme silence que partout ailleurs : ne rien dire plutot que rendre 1.0, qui
    se lirait comme « equipe parfaitement moyenne » alors qu'on ne sait rien.
    """
    means, strengths = _reference(baseline, field)
    if not means or not strengths:
        return None

    rendu: dict[str, Any] = {
        "moyenne_competition": means.get("moyenne_globale"),
        "moyenne_domicile": means.get("moyenne_domicile"),
        "moyenne_exterieur": means.get("moyenne_exterieur"),
    }
    for cote, equipe in zip(("domicile", "exterieur"), teams):
        rating = strengths.get(equipe)
        if not rating:
            # Equipe absente de la reference : elle joue dans une autre
            # competition (coupe), et ses forces n'ont pas de sens ici.
            continue
        echantillon = float(rating.get("matchs", 0) or 0)
        attaque = float(rating.get("attaque", 1.0))
        defense = float(rating.get("defense", 1.0))
        rendu[cote] = {
            "equipe": equipe,
            "matchs": echantillon,
            "attaque": round(attaque, 3),
            "defense": round(defense, 3),
            "attaque_regularisee": round(
                _shrink(attaque, echantillon, params.shrinkage), 3
            ),
            "defense_regularisee": round(
                _shrink(defense, echantillon, params.shrinkage), 3
            ),
            # Ce que la regularisation accorde a l'observation : n / (n + k).
            # A quatre matchs elle n'en garde que 29 %, et c'est la raison pour
            # laquelle deux equipes tres differentes ressortent proches.
            "poids_observation": round(
                echantillon / (echantillon + params.shrinkage), 3
            )
            if echantillon + params.shrinkage > 0
            else 0.0,
        }
    return rendu if ("domicile" in rendu or "exterieur" in rendu) else None


def _adjusted_averages(
    entries: list[dict[str, Any]],
    means: dict[str, Any] | None,
    strengths: dict[str, dict[str, Any]] | None,
    field: str | None = None,
    params: Params = DEFAULT_PARAMS,
    as_of: str = "",
    poids_contexte: dict[str, float] | None = None,
) -> tuple[float | None, float | None, int, float]:
    """Moyennes par match ramenees a un adversaire moyen et a un lieu neutre.

    Trois buts contre la meilleure defense du championnat ne valent pas trois
    buts contre la pire : la moyenne brute traite les deux de la meme facon et
    recompense un calendrier facile. Chaque match est donc rapporte a la force
    de l'adversaire ce jour-la :

        produit corrige = produit / defense(adversaire)
        concede corrige = concede / attaque(adversaire)

    C'est la correction de calendrier ("strength of schedule") implicite dans
    l'estimation simultanee de Maher : chez lui, toutes les forces sont estimees
    ensemble, chacune tenant compte des autres. On l'applique ici en une passe,
    a partir des forces mesurees sur la competition entiere. Les forces des
    adversaires sont elles-memes regularisees : mesurees sur quelques matchs,
    les diviser telles quelles amplifierait leur bruit.

    Chaque match pese enfin selon son anciennete (voir `_recency_weight`).

    Deux raffinements ont ete essayes ici puis ecartes faute de resultat :
    ramener chaque match a un lieu neutre, et retirer le match courant du bilan
    de l'adversaire avant de s'en servir. Les deux sont defendables sur le
    papier ; mesures sur 149 matchs ils degradent la prevision (README, "Ce qui
    a ete essaye et ecarte"). Ils ne sont donc pas appliques.

    Retourne (production, concession, matchs corriges, effectif efficace). Un
    adversaire d'une autre competition (coupe) n'a pas de force connue et reste
    tel quel, d'ou le compte des matchs reellement corriges. L'effectif efficace
    est la somme des poids : c'est lui, et non le nombre de matchs, qui mesure
    ce que le modele sait vraiment, et donc ce sur quoi la regularisation doit
    se regler.
    """
    produced_rates: list[float] = []
    conceded_rates: list[float] = []
    weights: list[float] = []
    adjusted = 0

    for entry in entries:
        observed = _observation(entry, field)
        if observed is None:
            continue
        produced, conceded = observed

        attack = defence = 1.0
        rating = (strengths or {}).get(entry.get("adversaire", ""))
        if rating:
            sample = float(rating.get("matchs", 0) or 0)
            attack = _shrink(
                float(rating.get("attaque", 1.0)), sample, params.shrinkage
            )
            defence = _shrink(
                float(rating.get("defense", 1.0)), sample, params.shrinkage
            )
            if attack > 0 and defence > 0:
                adjusted += 1
            else:
                attack = defence = 1.0

        produced_rates.append(produced / defence)
        conceded_rates.append(conceded / attack)
        weights.append(
            _entry_weight(entry, as_of, params.half_life, poids_contexte or {})
        )

    return (
        _weighted_mean(produced_rates, weights),
        _weighted_mean(conceded_rates, weights),
        adjusted,
        sum(weights),
    )


def _raw_averages(
    entries: list[dict[str, Any]],
    field: str | None,
    params: Params = DEFAULT_PARAMS,
    as_of: str = "",
    poids_contexte: dict[str, float] | None = None,
) -> tuple[float | None, float | None, float]:
    """Moyennes sans correction d'adversaire ni de lieu : repli sans reference.

    La ponderation par anciennete s'applique quand meme : elle ne demande rien
    de la competition, seulement la date des matchs.
    """
    produced: list[float] = []
    conceded: list[float] = []
    weights: list[float] = []
    for entry in entries:
        observed = _observation(entry, field)
        if observed is None:
            continue
        produced.append(observed[0])
        conceded.append(observed[1])
        weights.append(
            _entry_weight(entry, as_of, params.half_life, poids_contexte or {})
        )
    return (
        _weighted_mean(produced, weights),
        _weighted_mean(conceded, weights),
        sum(weights),
    )


def _lambda(produced: float | None, conceded_by_opponent: float | None) -> float | None:
    """Moyenne des deux estimations. Si une seule est connue, on la garde."""
    values = [v for v in (produced, conceded_by_opponent) if v is not None]
    return sum(values) / len(values) if values else None


def _scope(
    home: list[dict[str, Any]],
    away: list[dict[str, Any]],
    competition: str,
    minimum: int = 4,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Restreint les deux equipes a la competition du match, ou aucune des deux.

    Le modele de Maher se calibre au sein d'une competition : melanger coupe et
    championnat compare des forces qui ne sont pas sur la meme echelle. En debut
    de saison, une equipe n'a parfois que deux matchs de championnat, ce qui
    serait pire.

    La decision est donc prise pour les deux equipes ensemble. La prendre equipe
    par equipe produisait le defaut suivant : Hoffenheim evalue sur 4 matchs de
    Bundesliga et Dortmund sur 5 matchs toutes competitions (Super Coupe et
    tournoi de pre-saison compris), les deux etant ensuite normalises par la
    meme reference de Bundesliga.

    La comparaison passe par `_competition_key` : le flux du jour suffixe la
    phase, les historiques non. Comparees telles quelles, les deux ne coincident
    jamais dans les championnats a phases, et la restriction ne s'appliquait
    donc jamais la ou elle etait demandee.
    """
    wanted = _competition_key(competition)
    if not wanted:
        return home, away, False
    home_only = [e for e in home if _competition_key(e.get("competition", "")) == wanted]
    away_only = [e for e in away if _competition_key(e.get("competition", "")) == wanted]
    if len(home_only) >= minimum and len(away_only) >= minimum:
        return home_only, away_only, True
    return home, away, False


def venue_edge(
    entries: list[dict[str, Any]],
    field: str | None,
    at_home: bool,
    league_ratio: float,
    params: Params = DEFAULT_PARAMS,
) -> float:
    """Avantage du terrain propre a une equipe, ramene a celui de sa competition.

    Rend un multiplicateur a appliquer au nombre attendu. Il vaut 1 quand
    l'equipe se comporte comme la moyenne de sa competition, plus que 1 quand
    elle tire davantage parti de son terrain, moins quand elle en tire moins.

    L'estimation est fragile par nature : une equipe n'a qu'une poignee de
    matchs de chaque cote. Elle est donc retrecie vers 1 selon le nombre de
    matchs disponibles du cote le moins fourni -- c'est lui qui limite ce que
    l'on sait --, puis ponderee par `params.home_edge`. A 0, la fonction rend 1
    et le modele est exactement celui d'avant.

    `league_ratio` est le rapport domicile/exterieur de la competition, deja
    porte par les multiplicateurs de Maher : on ne mesure ici que l'ECART de
    l'equipe a ce rapport, sans quoi l'avantage serait compte deux fois.
    """
    if params.home_edge <= 0 or league_ratio <= 0:
        return 1.0

    home_values: list[float] = []
    away_values: list[float] = []
    for entry in entries:
        observed = _observation(entry, field)
        if observed is None:
            continue
        produced = observed[0]
        if entry.get("lieu") == "domicile":
            home_values.append(produced)
        else:
            away_values.append(produced)

    # Un cote vide ne dit rien : sans les deux, il n'y a pas de rapport a former.
    if not home_values or not away_values:
        return 1.0
    home_mean = sum(home_values) / len(home_values)
    away_mean = sum(away_values) / len(away_values)
    if home_mean <= 0 or away_mean <= 0:
        return 1.0

    # Ecart de l'equipe au rapport de sa competition. Au-dessus de 1, son
    # terrain lui rapporte plus que la moyenne.
    observed_ratio = (home_mean / away_mean) / league_ratio
    sample = min(len(home_values), len(away_values))
    tempered = _shrink(observed_ratio, sample, params.shrinkage)

    # L'ecart s'applique a l'equipe qui recoit ; celle qui se deplace en subit
    # l'inverse, pour que le total attendu ne derive pas.
    edge = tempered if at_home else 1.0 / tempered
    return 1.0 + params.home_edge * (edge - 1.0)


def shrink_lambda(lam: float, league_mean: float, sample: float, k: float) -> float:
    """Ramene un nombre attendu vers la moyenne de sa competition.

    Meme formule que `_shrink`, appliquee cette fois au produit et non a ses
    facteurs : `(n * lambda + k * moyenne) / (n + k)`. A k = 0, lambda est rendu
    tel quel.
    """
    if k <= 0 or league_mean <= 0:
        return lam
    return (sample * lam + k * league_mean) / (sample + k)


def _maher_lambdas(
    home_for: float,
    home_against: float,
    away_for: float,
    away_against: float,
    baseline: dict[str, Any],
    home_sample: float = 0.0,
    away_sample: float = 0.0,
    shrinkage: float = SHRINKAGE,
) -> tuple[float, float]:
    """Modele attaque / defense de Maher, normalise par la competition.

        Attaque_i = (buts marques par i et par match) / moyenne de la competition
        Defense_j = (buts encaisses par j et par match) / moyenne de la competition
        lambda_dom = Attaque_dom * Defense_ext * (buts moyens a domicile)
        lambda_ext = Attaque_ext * Defense_dom * (buts moyens a l'exterieur)

    Deux equipes exactement moyennes redonnent la moyenne de la competition, ce
    qui est la propriete attendue du modele.
    """
    mean = baseline["moyenne_globale"]
    attack_home = _shrink(home_for / mean, home_sample, shrinkage)
    defence_home = _shrink(home_against / mean, home_sample, shrinkage)
    attack_away = _shrink(away_for / mean, away_sample, shrinkage)
    defence_away = _shrink(away_against / mean, away_sample, shrinkage)
    return (
        attack_home * defence_away * baseline["moyenne_domicile"],
        attack_away * defence_home * baseline["moyenne_exterieur"],
    )


def estimer(
    field: str | None,
    calibration: float,
    home_entries: list[dict[str, Any]],
    away_entries: list[dict[str, Any]],
    competition: str,
    baseline: dict[str, Any] | None,
    params: Params,
    as_of: str,
    poids_contexte: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    """Nombre d'evenements attendu de chaque cote, ou None si incalculable.

    Toute l'estimation est ici, et rien d'autre : ni probabilite, ni
    proposition. La separer de la suite est ce qui permet au contexte de
    s'intercaler entre les deux -- les criteres 5 et 11 comparent leurs donnees
    au nombre attendu, ce qui suppose qu'il existe deja, et les corrections
    s'appliquent a lui, ce qui suppose qu'aucune probabilite n'en a encore ete
    tiree. Melangees comme avant, les deux etapes rendaient cette insertion
    impossible sans calculer les lambdas deux fois.

    `field` est le champ statistique de l'evenement (None pour les buts) et
    `calibration` son recalage d'echelle : les deux appartiennent au modele de
    l'evenement, qui les passe ici.
    """
    # Le perimetre se decide sur les matchs ou la grandeur est mesuree : une
    # equipe peut avoir dix matchs de championnat mais trois fiches
    # statistiques, et l'echantillon des corners n'est pas celui des buts.
    home_usable = _usable(home_entries, field)
    away_usable = _usable(away_entries, field)
    if not home_usable or not away_usable:
        return None

    home_scope, away_scope, scoped = _scope(home_usable, away_usable, competition)
    means, strengths = _reference(baseline, field)

    if means:
        home_for, home_against, home_fixed, home_weight = _adjusted_averages(
            home_scope, means, strengths, field, params, as_of, poids_contexte
        )
        away_for, away_against, away_fixed, away_weight = _adjusted_averages(
            away_scope, means, strengths, field, params, as_of, poids_contexte
        )
        adjusted = (home_fixed, away_fixed)
    else:
        home_for, home_against, home_weight = _raw_averages(
            home_scope, field, params, as_of, poids_contexte
        )
        away_for, away_against, away_weight = _raw_averages(
            away_scope, field, params, as_of, poids_contexte
        )
        adjusted = (0, 0)

    covered = (len(home_scope), len(away_scope))
    known = all(
        v is not None for v in (home_for, home_against, away_for, away_against)
    )

    if means and known:
        # L'effectif efficace, et non le nombre de matchs : cinq matchs dont
        # quatre remontent a trois mois n'informent pas comme cinq matchs du
        # mois dernier, et la regularisation doit le refleter.
        lam_home, lam_away = _maher_lambdas(
            home_for, home_against, away_for, away_against, means,
            home_weight, away_weight, params.shrinkage,
        )

        # Avantage du terrain propre a chaque equipe, puis retrecissement du
        # nombre attendu lui-meme. Les deux sont neutres par defaut : a
        # `home_edge = 0` et `lambda_shrink = 0`, les lambdas ressortent
        # inchanges, et le modele est exactement celui d'avant.
        league_ratio = (
            means["moyenne_domicile"] / means["moyenne_exterieur"]
            if means.get("moyenne_exterieur")
            else 1.0
        )
        lam_home *= venue_edge(home_scope, field, True, league_ratio, params)
        lam_away *= venue_edge(away_scope, field, False, league_ratio, params)
        lam_home = shrink_lambda(
            lam_home, means["moyenne_domicile"], home_weight, params.lambda_shrink
        )
        lam_away = shrink_lambda(
            lam_away, means["moyenne_exterieur"], away_weight, params.lambda_shrink
        )
        # Les effectifs totaux sont deja affiches par l'exportateur : la methode
        # ne dit que ce qu'il ignore, soit combien de matchs ont vraiment pu
        # etre corriges (un adversaire de coupe n'a pas de force connue et reste
        # brut) et sous quels reglages.
        method = (
            "Maher sur %d matchs de %s, corrige du niveau des adversaires "
            "(%d et %d matchs), forces regularisees (k=%g), demi-vie %g j"
            % (
                means.get("matchs", 0),
                (baseline or {}).get("competition", competition),
                adjusted[0], adjusted[1],
                params.shrinkage, params.half_life,
            )
        )
    else:
        lam_home = _lambda(home_for, away_against)
        lam_away = _lambda(away_for, home_against)
        method = "moyenne production / concession"

    if lam_home is None or lam_away is None:
        return None

    # Recalage d'echelle mesure a posteriori (voir `calibration` dans chaque
    # modele). Il vient apres le modele et avant toute probabilite : echelles,
    # propositions et seuil mis en avant doivent tous decouler du meme lambda.
    return {
        "lambda": (lam_home * calibration, lam_away * calibration),
        "matchs_utilises": covered,
        "matchs_corriges": adjusted,
        "effectif_efficace": (home_weight, away_weight),
        "methode": method,
        "restreint_competition": scoped,
    }
