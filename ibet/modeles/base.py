"""Contrat commun a tous les modeles d'evenement.

Un modele d'evenement (buts, corners, tirs cadres, cartons...) est responsable
de SA grandeur, de bout en bout :

  1. `estimer`  -- le nombre attendu de chaque cote, par le moteur commun de
                   Maher (`estimation.estimer`), avec son champ et son recalage ;
  2. `prevoir`  -- la fiche de la grandeur : corrections de contexte, lois,
                   echelles, propositions, tableau des marches.

Tout ce qui distingue un evenement d'un autre est declare en attributs de
classe -- seuils, dispersion, correlation, recalage -- ou dans deux points
d'extension :

  - `ajuster`          -- apports exterieurs sur les nombres attendus (les buts
                          y melangent le xG et les notes globales) ;
  - `completer`        -- ce que la fiche ajoute a la grandeur, et les
                          propositions qu'un modele partenaire y apporte (les
                          buts y branchent le modele de l'issue) ;
  - `candidats_propres` -- les marches propres a l'evenement (ecart et
                          combines pour les buts, duel pour les grandeurs par
                          equipe).

Ajouter un evenement revient donc a ecrire une sous-classe et a l'inscrire au
registre (`modeles/__init__.py`) ; rien d'autre n'a a changer.

Les trois constantes mesurees, rappel de ce qu'elles veulent dire :

  - `dispersion`  : rapport variance / moyenne par equipe, mesure sur les
    residus standardises. A 1, Poisson est exacte ; au-dessus, binomiale
    negative ; en dessous, binomiale (voir `lois.count_pmf`).
  - `correlation` : correlation des RESIDUS entre les deux equipes d'un meme
    match. Elle ne change pas les lois PAR EQUIPE, seulement celle du total
    (`lois.dispersion_du_total`).
  - `calibration` : recalage d'echelle, `lambda_corrige = lambda * calibration`.
    Un facteur mesure une fois vieillit avec le modele qu'il corrige : on ne
    recale pas sur une poignee de rencontres.
"""

from __future__ import annotations

from typing import Any, NamedTuple, Sequence

from .estimation import estimer, forces_des_equipes
from .lois import (
    _blend,
    _grid_probability,
    _grid_size,
    count_distribution,
    dispersion_du_total,
    over_probability,
    team_over_probability,
    total_over_probability,
)
from .offres import market_board, select_offers
from .reglages import DEFAULT_PARAMS, RHO, Params


class Metric(NamedTuple):
    """Une grandeur prevue et les seuils sur lesquels on la decline.

    `field` est le champ de `api_client.STAT_ORDER` a lire dans la fiche du
    match ; None designe les buts, qui se lisent au score et n'ont donc pas
    besoin des statistiques detaillees.
    """

    key: str
    label: str
    field: str | None
    line: float               # seuil mis en avant (marche le plus courant)
    team_lines: tuple[float, ...]
    total_lines: tuple[float, ...]
    prefere: str = "les deux"  # cote du marche mis en avant : "plus", "moins",
                               # ou "les deux"


def dispersion_effective(
    base: float, sample: float, params: Params = DEFAULT_PARAMS
) -> float:
    """Rapport variance / moyenne effectif d'une grandeur, echantillon compris.

    Somme de deux termes : la dispersion du processus (`dispersion` du modele,
    mesuree sur des residus standardises) et celle que l'estimation ajoute quand
    lambda est tire d'un petit nombre de matchs. A `estimation_dispersion = 0`,
    seule la premiere subsiste et le modele est celui d'avant.
    """
    weight = params.estimation_dispersion
    if weight <= 0 or sample <= 0:
        return base
    return base + weight / sample


def corriger(
    lam: tuple[float, float], correction: dict[str, float] | None
) -> tuple[float, float]:
    """Applique la correction de contexte a un couple de nombres attendus.

    Sans contexte, ou pour une grandeur qu'aucun critere ne touche, la
    correction est absente et les nombres ressortent inchanges : le modele est
    alors exactement celui d'avant, ce que les tests verifient.
    """
    if not correction:
        return lam
    return (
        lam[0] * correction.get("domicile", 1.0),
        lam[1] * correction.get("exterieur", 1.0),
    )


def _ranges(lines: tuple[float, ...]) -> list[tuple[int, int]]:
    """Fourchettes entieres deduites des seuils, larges de 2 et 3 unites.

    "entre 2 et 4 buts" est un marche courant, et c'est souvent le seul qui dise
    quelque chose sur un match ferme : les deux bornes ecartent a la fois le 0-0
    et la correction, la ou un seuil unique n'ecarte qu'un cote.
    """
    spans: list[tuple[int, int]] = []
    for start in range(len(lines)):
        for width in (2, 3):
            stop = start + width
            if stop >= len(lines):
                continue
            low, high = int(lines[start] + 0.5), int(lines[stop] - 0.5)
            if 0 <= low <= high:
                spans.append((low, high))
    return spans


class ModeleEvenement:
    """Modele d'un evenement de match compte par equipe.

    Les sous-classes ne font que declarer leurs attributs et, au besoin,
    surcharger les points d'extension. Les attributs de classe sont les seuls
    parametres du modele : ils sont lus ici et nulle part ailleurs.
    """

    cle: str = ""
    # Version du modele, au format MAJEURE.MINEURE.CORRECTIF. Elle est inscrite
    # dans chaque fiche emise : c'est elle qui permet de comparer, apres coup,
    # ce que chaque version a reellement donne (`python -m ibet etude`). Toute
    # modification qui change une probabilite oblige a l'incrementer ET a
    # documenter la nouvelle version dans `modeles/journal/<cle>.md`.
    version: str = "0.0.0"
    libelle: str = ""
    champ: str | None = None
    seuil: float = 0.0
    seuils_equipe: tuple[float, ...] = ()
    seuils_total: tuple[float, ...] = ()
    # Lignes proposees par les bookmakers : la plage ou ils en ouvrent
    # (`gamme_*`, bornes comprises), et combien de lignes de part et d'autre de
    # leur ligne principale -- celle ou « plus » et « moins » se valent. Un
    # match a 12 corners attendus ne se joue pas sur « plus de 7.5 », que
    # personne ne cote : il se joue de 9.5 a 14.5. Sans gamme, les seuils fixes
    # ci-dessus servent tels quels.
    gamme_total: tuple[float, float] | None = None
    gamme_equipe: tuple[float, float] | None = None
    ecart_total: int = 2
    ecart_equipe: int = 1
    # Le cote mis en avant. "plus de 9.5 corners" et "moins de 9.5 corners"
    # sont deux faces de la MEME probabilite : `prefere` ne change aucun calcul,
    # c'est un choix de PRESENTATION.
    prefere: str = "les deux"
    dispersion: float = 1.0
    correlation: float = 0.0
    calibration: float = 1.0
    # Le facteur de Dixon et Coles porte sur les quatre petits SCORES d'un
    # match : "0-0" n'a ce sens que pour des buts. Ailleurs, le modele reste
    # celui de deux lois independantes.
    dixon_coles: bool = False

    @property
    def metric(self) -> Metric:
        """La description de la grandeur, sous la forme qu'emploie le reste du projet."""
        return Metric(
            self.cle, self.libelle, self.champ, self.seuil,
            self.seuils_equipe, self.seuils_total, self.prefere,
        )

    def rho(self, params: Params) -> float:
        return params.rho if self.dixon_coles else RHO

    def dispersion_effective(
        self, sample: float, params: Params = DEFAULT_PARAMS, base: float | None = None
    ) -> float:
        return dispersion_effective(self.dispersion if base is None else base, sample, params)

    def dispersion_pour(self, trace: dict[str, Any] | None) -> float:
        """Dispersion du processus pour CETTE prevision, selon ce que les apports
        ont fait (`trace` rendue par `ajuster`). Par defaut, la constante."""
        return self.dispersion

    # --- Premiere phase : estimer ---------------------------------------------

    def estimer(
        self,
        home_entries: list[dict[str, Any]],
        away_entries: list[dict[str, Any]],
        competition: str,
        baseline: dict[str, Any] | None,
        params: Params,
        as_of: str,
        poids_contexte: dict[str, float] | None = None,
    ) -> dict[str, Any] | None:
        """Nombre d'evenements attendu de chaque cote, ou None si incalculable."""
        return estimer(
            self.champ, self.calibration, home_entries, away_entries,
            competition, baseline, params, as_of, poids_contexte,
        )

    # --- Lois -----------------------------------------------------------------

    def probabilite_total(
        self,
        lam_home: float,
        lam_away: float,
        line: float,
        rho: float,
        phi: float | None = None,
    ) -> float:
        """P(total > line) pour cette grandeur, sous la loi qui lui correspond.

        Deux lois cohabitent : la matrice jointe, seule a porter le facteur de
        Dixon-Coles, et la loi du total directe, seule a porter la dispersion et
        la correlation entre les deux equipes. Le choix est fait ici et nulle
        part ailleurs -- reparti entre l'echelle, les propositions et le seuil
        mis en avant, il finissait par diverger, et la meme ligne s'affichait a
        deux valeurs differentes selon l'endroit ou on la lisait.

        La correlation entre les deux cotes ne change pas les lois PAR EQUIPE,
        mais elle change celle du total : elle en deplace la variance sans en
        deplacer la moyenne. On l'absorbe en donnant au total la dispersion qui
        lui revient (`dispersion_du_total`), plutot que celle d'une equipe.
        C'est un calage sur les deux premiers moments -- exact pour la moyenne
        et la variance, approche pour le reste de la forme, ce qui suffit pour
        des seuils.
        """
        if phi is None:
            phi = self.dispersion
        phi_total = dispersion_du_total(lam_home, lam_away, phi, self.correlation)
        if phi_total != 1.0:
            return total_over_probability(lam_home, lam_away, line, phi_total)
        return over_probability(lam_home, lam_away, line, rho)

    def lignes_du_match(
        self,
        lam_home: float,
        lam_away: float,
        rho: float = RHO,
        phi_home: float | None = None,
        phi_away: float | None = None,
    ) -> tuple[tuple[float, ...], tuple[float, ...]]:
        """(lignes du total, lignes par equipe) qu'un bookmaker ouvrirait.

        La ligne principale est celle dont la probabilite de depassement est
        la plus proche de 50 % ; on garde `ecart` lignes de chaque cote, dans
        la gamme. Par equipe, une seule echelle pour les deux : de la ligne
        principale de la plus faible a celle de la plus forte, elargie de
        `ecart_equipe`.
        """
        if not self.gamme_total or not self.gamme_equipe:
            return self.seuils_total, self.seuils_equipe
        phi_home = self.dispersion if phi_home is None else phi_home
        phi_away = self.dispersion if phi_away is None else phi_away
        phi_total = _blend(phi_home, phi_away, lam_home, lam_away)

        def autour(gamme, ecart, principales):
            bas, haut = gamme
            toutes = [bas + k for k in range(int(round(haut - bas)) + 1)]
            a = max(bas, min(principales) - ecart)
            b = min(haut, max(principales) + ecart)
            return tuple(l for l in toutes if a - 1e-9 <= l <= b + 1e-9)

        def principale(gamme, p_plus):
            bas, haut = gamme
            toutes = [bas + k for k in range(int(round(haut - bas)) + 1)]
            return min(toutes, key=lambda l: abs(p_plus(l) - 0.5))

        total = autour(self.gamme_total, self.ecart_total, [principale(
            self.gamme_total,
            lambda l: self.probabilite_total(lam_home, lam_away, l, rho, phi_total))])
        equipe = autour(self.gamme_equipe, self.ecart_equipe, [
            principale(self.gamme_equipe, lambda l, lam=lam, phi=phi: team_over_probability(lam, l, phi))
            for lam, phi in ((lam_home, phi_home), (lam_away, phi_away))])
        return total, equipe

    def echelles(
        self,
        lam_home: float,
        lam_away: float,
        rho: float = RHO,
        phi_home: float | None = None,
        phi_away: float | None = None,
    ) -> dict[str, Any]:
        """Probabilite de depassement, seuil par seuil, par equipe puis au total.

        Le seuil unique mis en avant (2.5 buts, 9.5 corners) cache le reste de la
        distribution : deux matchs a 26 % de "plus de 2.5 buts" peuvent etre tres
        differents un seuil plus bas.
        """
        phi_home = self.dispersion if phi_home is None else phi_home
        phi_away = self.dispersion if phi_away is None else phi_away
        # Le total additionne deux lois : sa dispersion est celle des deux cotes,
        # ponderee par ce que chacun apporte a la somme.
        phi_total = _blend(phi_home, phi_away, lam_home, lam_away)
        seuils_total, seuils_equipe = self.lignes_du_match(
            lam_home, lam_away, rho, phi_home, phi_away)
        total = [
            self.probabilite_total(lam_home, lam_away, l, rho, phi_total)
            for l in seuils_total
        ]
        return {
            "seuils_equipe": list(seuils_equipe),
            "domicile": [
                team_over_probability(lam_home, l, phi_home) for l in seuils_equipe
            ],
            "exterieur": [
                team_over_probability(lam_away, l, phi_away) for l in seuils_equipe
            ],
            "seuils_total": list(seuils_total),
            "total": total,
            "dispersion": phi_total,
        }

    # --- Propositions ---------------------------------------------------------

    def candidats(
        self,
        teams: tuple[str, str],
        lam_home: float,
        lam_away: float,
        rho: float = RHO,
        phi_home: float | None = None,
        phi_away: float | None = None,
        partenaires: Sequence[dict[str, Any]] = (),
    ) -> list[dict[str, Any]]:
        """Toutes les propositions envisageables, avant selection.

        Separee de la selection pour que l'evaluation puisse mesurer la
        calibration sur **toute** l'echelle des probabilites, et pas seulement
        sur la tranche retenue (70-95 %) : c'est la seule facon de savoir si un
        30 % annonce vaut 30 %, alors qu'aucune fiche ne le montre jamais.

        Chaque proposition porte sa `famille`. Sans elle, un lecteur -- ou une
        mesure -- devrait la deviner en analysant le libelle, ce qui casse au
        premier nom d'equipe contenant « ou nul » ou « plus de ».

        `partenaires` sont les propositions qu'un autre modele greffe sur cette
        grandeur (l'issue sur les buts) ; elles s'intercalent entre les seuils et
        les marches propres a l'evenement.
        """
        home, away = teams
        noun = self.libelle.lower()
        candidates: list[dict[str, Any]] = []
        phi_home = self.dispersion if phi_home is None else phi_home
        phi_away = self.dispersion if phi_away is None else phi_away
        phi_total = _blend(phi_home, phi_away, lam_home, lam_away)

        def add(label: str, probability: float, family: str) -> None:
            candidates.append({"libelle": label, "p": probability, "famille": family})

        seuils_total, seuils_equipe = self.lignes_du_match(
            lam_home, lam_away, rho, phi_home, phi_away)
        for line in seuils_total:
            over = self.probabilite_total(lam_home, lam_away, line, rho, phi_total)
            add("Plus de %g %s au total" % (line, noun), over, "total")
            add("Moins de %g %s au total" % (line, noun), 1.0 - over, "total")

        for name, lam, phi in ((home, lam_home, phi_home), (away, lam_away, phi_away)):
            for line in seuils_equipe:
                over = team_over_probability(lam, line, phi)
                add("%s : plus de %g %s" % (name, line, noun), over, "equipe")
                add("%s : moins de %g %s" % (name, line, noun), 1.0 - over, "equipe")

        # Fourchettes : deux bornes valent souvent mieux qu'un seuil, et le calcul
        # se fait par difference de deux seuils, donc sous exactement la meme loi.
        for low, high in _ranges(seuils_total):
            probability = self.probabilite_total(
                lam_home, lam_away, low - 0.5, rho
            ) - self.probabilite_total(lam_home, lam_away, high + 0.5, rho)
            add("Entre %d et %d %s au total" % (low, high, noun), probability, "fourchette")

        candidates.extend(partenaires)
        candidates.extend(self.candidats_propres(teams, lam_home, lam_away, rho))
        return candidates

    def candidats_propres(
        self, teams: tuple[str, str], lam_home: float, lam_away: float, rho: float
    ) -> list[dict[str, Any]]:
        """Marches propres a l'evenement, au-dela des seuils et fourchettes."""
        return []

    # --- Seconde phase : prevoir ----------------------------------------------

    def ajuster(
        self, lam: tuple[float, float], apports: dict[str, Any] | None
    ) -> tuple[float, float, dict[str, Any] | None]:
        """Nombres attendus apres les apports exterieurs, et leur trace.

        Par defaut, aucun apport : les nombres ressortent tels quels.
        """
        return lam[0], lam[1], None

    def caler(
        self,
        lam: tuple[float, float],
        apports: dict[str, Any] | None,
        rho: float,
        phi_home: float,
        phi_away: float,
    ) -> tuple[float, float, dict[str, Any] | None]:
        """Dernier calage des nombres attendus, contexte compris, et sa trace.

        Appele APRES les corrections de contexte : ce qui est cale ici l'est sur
        les nombres definitifs. Par defaut, rien ne change.
        """
        return lam[0], lam[1], None

    def completer(
        self,
        entry: dict[str, Any],
        teams: tuple[str, str],
        lam_home: float,
        lam_away: float,
        rho: float,
        phi_home: float,
        phi_away: float,
    ) -> list[dict[str, Any]]:
        """Enrichit la fiche de la grandeur ; rend les propositions partenaires."""
        return []

    def prevoir(
        self,
        estimation: dict[str, Any],
        teams: tuple[str, str],
        baseline: dict[str, Any] | None,
        params: Params,
        correction: dict[str, float] | None = None,
        with_candidates: bool = False,
        apports: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fiche de la grandeur : nombres corriges, lois, propositions."""
        rho = self.rho(params)
        lam_home, lam_away, trace = self.ajuster(estimation["lambda"], apports)
        lam_home, lam_away = corriger((lam_home, lam_away), correction)

        home_weight, away_weight = estimation["effectif_efficace"]
        # Dispersions effectives : celle du processus, plus celle que
        # l'estimation ajoute quand lambda repose sur peu de matchs. C'est
        # l'effectif EFFICACE qui compte, et non le nombre de matchs : cinq
        # matchs dont deux presque oublies par la ponderation n'en valent pas
        # cinq. A `estimation_dispersion = 0`, les deux valent la constante et
        # rien ne change.
        base_phi = self.dispersion_pour(trace)
        phi_home = self.dispersion_effective(home_weight, params, base_phi)
        phi_away = self.dispersion_effective(away_weight, params, base_phi)
        lam_home, lam_away, calage = self.caler(
            (lam_home, lam_away), apports, rho, phi_home, phi_away
        )
        phi_total = _blend(phi_home, phi_away, lam_home, lam_away)

        entry: dict[str, Any] = {
            "cle": self.cle,
            "libelle": self.libelle,
            "lambda_domicile": round(lam_home, 2),
            "lambda_exterieur": round(lam_away, 2),
            "total_attendu": round(lam_home + lam_away, 2),
            "seuil": self.seuil,
            "p_plus_de_seuil": self.probabilite_total(
                lam_home, lam_away, self.seuil, rho, phi_total
            ),
            "matchs_utilises": estimation["matchs_utilises"],
            "matchs_corriges": estimation["matchs_corriges"],
            "effectif_efficace": (round(home_weight, 2), round(away_weight, 2)),
            "methode": estimation["methode"],
            "restreint_competition": estimation["restreint_competition"],
            "echelles": self.echelles(lam_home, lam_away, rho, phi_home, phi_away),
            # Les forces qui ont PRODUIT ce nombre attendu -- ou, quand un apport
            # exterieur les a remplacees, la trace de cette recombinaison : sans
            # elle, on ne pourrait pas dire apres coup d'ou viennent les nombres.
            "forces": trace
            or forces_des_equipes(baseline, teams, self.champ, params),
        }
        if correction:
            # Le nombre attendu avant contexte reste dans la fiche : sans lui,
            # la correction affichee ne serait pas verifiable.
            avant = estimation["lambda"]
            entry["contexte"] = {
                "correction": correction,
                "lambda_avant_contexte": (round(avant[0], 2), round(avant[1], 2)),
            }
        if calage:
            entry["marche"] = calage
        partenaires = self.completer(
            entry, teams, lam_home, lam_away, rho, phi_home, phi_away
        )
        candidates = self.candidats(
            teams, lam_home, lam_away, rho, phi_home, phi_away, partenaires
        )
        # Corners, tirs cadres, cartons : marches speciaux, plus margines que
        # le 1X2 et les buts (`bookmakers.MAJORATION_SPECIAUX`).
        special = self.cle != "buts"
        entry["offres"] = select_offers(candidates, special=special)
        entry["marches"] = market_board(candidates, entry["offres"], special=special)
        # Les candidates ne sont exposees qu'a la demande : une fiche n'a que
        # faire de soixante propositions dont elle n'en retient que trois, mais
        # l'evaluation en a besoin pour mesurer la calibration hors de la
        # tranche 70-95 %.
        if with_candidates:
            entry["candidats"] = candidates
        return entry



class ModeleParEquipe(ModeleEvenement):
    """Grandeur relevee dans les statistiques de chaque equipe (corners, tirs...).

    Le duel entre les deux cotes est un marche a part entiere (la "course aux
    corners"), et il n'a pas d'equivalent dans les seuils.
    """

    def candidats_propres(
        self, teams: tuple[str, str], lam_home: float, lam_away: float, rho: float
    ) -> list[dict[str, Any]]:
        home, away = teams
        noun = self.libelle.lower()
        candidates: list[dict[str, Any]] = []
        phi = self.dispersion
        size = max(_grid_size(lam_home, phi), _grid_size(lam_away, phi))
        home_law = count_distribution(lam_home, phi, size)
        away_law = count_distribution(lam_away, phi, size)
        mass = sum(home_law) * sum(away_law)
        if mass > 0:
            grid = [[h * a / mass for a in away_law] for h in home_law]
            for name, at_home in ((home, True), (away, False)):
                candidates.append({
                    "libelle": "Plus de %s pour %s" % (noun, name),
                    "p": _grid_probability(
                        grid, (lambda w: lambda h, a: h > a if w else a > h)(at_home)
                    ),
                    "famille": "duel",
                })
                candidates.append({
                    "libelle": "Autant ou plus de %s pour %s" % (noun, name),
                    "p": _grid_probability(
                        grid, (lambda w: lambda h, a: h >= a if w else a >= h)(at_home)
                    ),
                    "famille": "duel",
                })
        return candidates
