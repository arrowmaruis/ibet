"""Evaluation du modele de prevision sur des matchs deja joues.

Un modele qui n'a jamais ete confronte aux resultats n'est qu'une opinion mise
en forme. Ce module rejoue les matchs termines, compare les probabilites
annoncees a ce qui s'est reellement produit, et compare le tout a deux modeles
de reference qu'il faut battre pour valoir quelque chose.

Deux precautions rendent la mesure honnete :

  - **Coupure temporelle.** Le flux rend les derniers matchs d'une equipe *a ce
    jour*. Rejouer un match d'il y a cinq jours sans couper l'historique
    reviendrait a lui donner connaissance de ce qui s'est passe apres. La forme
    et la reference de championnat sont donc calculees avec `before` fixe au
    coup d'envoi du match evalue.
  - **Modeles de reference.** Un score isole ne dit rien. On mesure aussi :
      * `uniforme`  : 1/3 - 1/3 - 1/3, l'ignorance totale ;
      * `frequences`: les taux de victoire domicile / nul / exterieur observes
        dans la competition, sans rien savoir des equipes.
    Le modele doit faire mieux que le second, sinon toute la machinerie
    attaque/defense n'apporte rien.

Mesures utilisees, toutes standard pour des previsions probabilistes :

  - **Score de Brier** (Brier 1950), ici sa forme multiclasse : moyenne des
    carres des ecarts entre probabilite annoncee et issue reelle (0 ou 1).
    Plus bas est meilleur. Borne a 2 pour trois issues.
  - **Log-loss** (entropie croisee) : -log(probabilite accordee a l'issue
    survenue). Punit beaucoup plus durement une certitude erronee.
  - **Taux de reussite** : part des matchs ou l'issue la plus probable est
    survenue. Lisible, mais aveugle a la calibration.
  - **Erreur absolue moyenne sur le total de buts.**
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Sequence

from ibet.evaluation import verify
from ibet.prevision import predict

# Issue reelle d'un match, dans le meme vocabulaire que les probabilites.
OUTCOMES = ("domicile", "nul", "exterieur")


def actual_outcome(match: dict[str, Any]) -> str | None:
    home, away = match.get("score_domicile"), match.get("score_exterieur")
    if home is None or away is None:
        return None
    if home > away:
        return "domicile"
    if home == away:
        return "nul"
    return "exterieur"


def brier(probabilities: dict[str, float], outcome: str) -> float:
    """Score de Brier multiclasse : somme des carres des ecarts."""
    return sum(
        (probabilities.get(key, 0.0) - (1.0 if key == outcome else 0.0)) ** 2
        for key in OUTCOMES
    )


def log_loss(probabilities: dict[str, float], outcome: str) -> float:
    """-log(p) accorde a l'issue survenue, borne pour eviter l'infini."""
    return -math.log(max(probabilities.get(outcome, 0.0), 1e-9))


def frequency_reference(baseline: dict[str, Any] | None) -> dict[str, float]:
    """Taux d'issues de la competition, deduits des moyennes de buts.

    A defaut de compter les issues elles-memes (la reference ne retient que des
    totaux de buts), on passe par le meme modele de Poisson applique aux seules
    moyennes de la competition : c'est ce qu'un observateur saurait predire en
    connaissant le championnat mais aucune des deux equipes.
    """
    from ibet.prevision import predict

    if not baseline:
        return {"domicile": 1 / 3, "nul": 1 / 3, "exterieur": 1 / 3}
    return predict.outcome_probabilities(
        baseline["moyenne_domicile"], baseline["moyenne_exterieur"]
    )


class Evaluation:
    """Accumule les mesures d'un modele sur une serie de matchs."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.brier: list[float] = []
        self.log_loss: list[float] = []
        self.hits = 0
        self.count = 0

    def add(self, probabilities: dict[str, float], outcome: str) -> None:
        self.count += 1
        self.brier.append(brier(probabilities, outcome))
        self.log_loss.append(log_loss(probabilities, outcome))
        predicted = max(OUTCOMES, key=lambda key: probabilities.get(key, 0.0))
        if predicted == outcome:
            self.hits += 1

    def summary(self) -> dict[str, Any]:
        if not self.count:
            return {"nom": self.name, "matchs": 0}
        return {
            "nom": self.name,
            "matchs": self.count,
            "brier": sum(self.brier) / self.count,
            "log_loss": sum(self.log_loss) / self.count,
            "reussite": self.hits / self.count,
            # Erreur type de la moyenne : sans elle, impossible de dire si un
            # ecart de 0.03 entre deux modeles veut dire quelque chose.
            "brier_erreur_type": _standard_error(self.brier),
        }


def _standard_error(values: Sequence[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(variance / n)


def paired_comparison(
    left: Sequence[float], right: Sequence[float]
) -> dict[str, Any] | None:
    """Compare deux modeles sur les MEMES matchs.

    Comparer deux moyennes independantes gaspillerait l'information : les deux
    modeles voient exactement les memes rencontres, donc c'est la difference
    match par match qu'il faut resumer. Son erreur type est bien plus petite,
    et le rapport ecart / erreur type dit si la difference tient debout.
    """
    if len(left) != len(right) or len(left) < 2:
        return None
    diffs = [a - b for a, b in zip(left, right)]
    n = len(diffs)
    mean = sum(diffs) / n
    error = _standard_error(diffs)
    if not error:
        return None
    return {"ecart": mean, "erreur_type": error, "t": mean / error, "matchs": n}


class MetricCheck:
    """Confronte les valeurs attendues d'une grandeur a ce qui s'est produit.

    Le score de Brier ne dit rien des corners ni des cartons : il ne juge que
    l'issue. Sans cette mesure, les colonnes "corners" et "cartons" d'une fiche
    de prevision n'ont aucune garantie derriere elles -- ce qui etait le cas
    jusqu'ici, et qu'il fallait dire.
    """

    def __init__(self, label: str) -> None:
        self.label = label
        self.predicted: list[float] = []
        self.actual: list[float] = []

    def add(self, predicted: float, actual: float) -> None:
        self.predicted.append(predicted)
        self.actual.append(actual)

    def summary(self) -> dict[str, Any]:
        n = len(self.predicted)
        if not n:
            return {"grandeur": self.label, "matchs": 0}
        diffs = [p - a for p, a in zip(self.predicted, self.actual)]
        return {
            "grandeur": self.label,
            "matchs": n,
            # Biais : positif = le modele annonce trop. Une erreur absolue
            # moyenne peut etre acceptable tout en cachant un exces systematique.
            "biais": sum(diffs) / n,
            "biais_erreur_type": _standard_error(diffs),
            "mae": sum(abs(d) for d in diffs) / n,
            "prevu_moyen": sum(self.predicted) / n,
            "reel_moyen": sum(self.actual) / n,
        }


def grille_du_score(prediction: dict[str, Any]) -> list[list[float]] | None:
    """Loi jointe des scores telle que la fiche l'a produite, ou None.

    Reconstruite depuis les lambdas et le REGLAGE de la fiche, et non lue
    dedans : celle-ci ne porte que ses trois scores les plus probables, alors
    que juger une prevision de score demande la probabilite du score REEL, qui
    n'y figure presque jamais.

    Les dispersions effectives sont refaites a l'identique. Sans elles, regler
    `estimation_dispersion` ne se verrait pas : la grille serait rejouee en
    Poisson stricte alors que la fiche, elle, aurait ete elargie, et la mesure
    jugerait autre chose que ce qui a ete emis.
    """
    goals = next(
        (g for g in prediction.get("grandeurs") or [] if g.get("cle") == "buts"),
        None,
    )
    if not goals:
        return None
    lam_home = goals.get("lambda_domicile")
    lam_away = goals.get("lambda_exterieur")
    if lam_home is None or lam_away is None:
        return None
    reglage = prediction.get("reglage") or {}
    params = predict.DEFAULT_PARAMS._replace(
        **{
            cle: float(valeur)
            for cle, valeur in reglage.items()
            if cle in predict.DEFAULT_PARAMS._fields
            and isinstance(valeur, (int, float))
        }
    )
    effectifs = goals.get("effectif_efficace") or (0.0, 0.0)
    return predict.score_matrix(
        lam_home,
        lam_away,
        float(reglage.get("rho", 0.0) or 0.0),
        predict.effective_dispersion("buts", effectifs[0], params),
        predict.effective_dispersion("buts", effectifs[1], params),
    )


def perte_du_score(
    grille: list[list[float]] | None, reel: tuple[int, int]
) -> float | None:
    """Log-loss du score REEL sous cette grille, quel que soit son rang."""
    if not grille:
        return None
    return -math.log(ScoreCheck._probabilite(grille, *reel))


class ScoreCheck:
    """Juge la GRILLE DES SCORES, que rien d'autre dans ce module ne regarde.

    Le Brier et le log-loss jugent l'issue -- trois cases. `MetricCheck` juge le
    total de buts -- une somme. Ni l'un ni l'autre ne dit si « 1-1 a 12 % » vaut
    quelque chose, alors que c'est la ligne la plus lue d'une fiche et la plus
    facile a juger de memoire : le lecteur voit le score annonce, voit le score
    reel, et conclut. Sans mesure, ce jugement reste une impression.

    Trois lectures, et elles ne disent pas la meme chose :

      - **le taux de tete** : combien de fois le score le plus probable est le
        bon. C'est la lecture spontanee, et c'est la moins informative -- elle
        ignore completement ce que le modele avait annonce ;
      - **la couverture du top 3** : le reel figure-t-il dans les trois scores
        affiches ;
      - **le log-loss du score reel**, seule mesure honnete. Elle juge la
        probabilite donnee AU SCORE QUI S'EST PRODUIT, quel que soit son rang :
        un modele qui met 11 % sur le 2-1 qui arrive fait mieux qu'un modele qui
        y met 6 %, meme si aucun des deux ne l'avait en tete.

    Le plafond compte autant que la mesure, et il est bas. Un oracle connaissant
    le VRAI lambda de chaque match ne place le bon score en tete qu'environ
    12,5 % du temps, et ses trois premiers ne couvrent que 33 % des cas : le
    score exact n'est pas previsible au-dela, par personne. Un taux de tete de
    17 % n'est donc pas un mauvais resultat, c'est le maximum atteignable a du
    bruit d'echantillonnage pres -- et il faut le dire, sans quoi la mesure
    invite a « ameliorer » ce qui est deja au bout.

    La reference le rappelle a chaque fois : la meme grille construite sur les
    moyennes de la COMPETITION, sans rien savoir des deux equipes. Ce que le
    modele apporte est l'ecart entre les deux, et rien d'autre.
    """

    def __init__(self) -> None:
        self.rangs: Counter = Counter()
        self.pertes: list[float] = []
        self.pertes_reference: list[float] = []
        self.tete: list[float] = []
        self.matchs = 0

    @staticmethod
    def _probabilite(grille: list[list[float]], home: int, away: int) -> float:
        """P(score exact), ou la masse residuelle si le score sort de la grille.

        Un 7-3 depasse la matrice tronquee. Lui donner zero rendrait le log-loss
        infini sur un seul match et emporterait toute la mesure ; on lui donne
        donc le plancher, qui vaut ce que vaut la plus petite case retenue.
        """
        if 0 <= home < len(grille) and 0 <= away < len(grille[home]):
            return grille[home][away]
        return 1e-6

    def add(
        self,
        grille: list[list[float]],
        reference: list[list[float]] | None,
        reel: tuple[int, int],
    ) -> None:
        self.matchs += 1
        cellules = sorted(
            (
                (joint, home, away)
                for home, ligne in enumerate(grille)
                for away, joint in enumerate(ligne)
            ),
            reverse=True,
        )
        self.tete.append(cellules[0][0])
        rang = next(
            (
                position
                for position, (_, home, away) in enumerate(cellules, start=1)
                if (home, away) == reel
            ),
            None,
        )
        # Au-dela du troisieme, le rang exact n'apprend rien : c'est le
        # log-loss qui prend le relais.
        self.rangs[rang if rang and rang <= 3 else 0] += 1
        self.pertes.append(-math.log(self._probabilite(grille, *reel)))
        if reference:
            self.pertes_reference.append(
                -math.log(self._probabilite(reference, *reel))
            )

    def summary(self) -> dict[str, Any]:
        if not self.matchs:
            return {"matchs": 0}
        dans_top3 = sum(self.rangs[rang] for rang in (1, 2, 3))
        resume = {
            "matchs": self.matchs,
            "taux_tete": self.rangs[1] / self.matchs,
            # Ce que le modele PROMETTAIT en tete : le comparer au taux obtenu
            # est la seule facon de dire s'il ment, et c'est cette comparaison
            # qui manque a l'impression « il est nul sur les scores ».
            "tete_annoncee": sum(self.tete) / self.matchs,
            "couverture_top3": dans_top3 / self.matchs,
            "log_loss": sum(self.pertes) / len(self.pertes),
            "log_loss_erreur_type": _standard_error(self.pertes),
        }
        if self.pertes_reference:
            resume["log_loss_reference"] = sum(self.pertes_reference) / len(
                self.pertes_reference
            )
            resume["vs_reference"] = paired_comparison(
                self.pertes, self.pertes_reference
            )
        return resume


class Calibration:
    """Compare la probabilite annoncee a la frequence observee, par tranche.

    Une prevision peut avoir raison "en moyenne" et rester mal calibree : si
    tout ce qui est annonce a 80 % ne se produit qu'une fois sur deux, les
    pourcentages affiches induisent en erreur, meme quand l'ordre des
    propositions est bon.
    """

    def __init__(self, bands: int = 10) -> None:
        self.bands = bands
        self.hits: Counter = Counter()
        self.total: Counter = Counter()
        self.claimed: dict[int, float] = {}

    def add(self, probability: float, happened: bool) -> None:
        band = min(int(probability * self.bands), self.bands - 1)
        self.total[band] += 1
        self.hits[band] += 1 if happened else 0
        self.claimed[band] = self.claimed.get(band, 0.0) + probability

    def rows(self, minimum: int = 8) -> list[dict[str, Any]]:
        out = []
        for band in sorted(self.total):
            count = self.total[band]
            if count < minimum:
                continue
            out.append(
                {
                    "tranche": "%d-%d%%" % (band * 10, band * 10 + 10),
                    "matchs": count,
                    "annonce": self.claimed[band] / count,
                    "observe": self.hits[band] / count,
                }
            )
        return out


class OfferCheck:
    """Juge les propositions elles-memes -- ce que la fiche engage.

    Le score de Brier ne porte que sur l'issue, et `MetricCheck` sur les
    nombres attendus. Ni l'un ni l'autre ne dit si « Plus de 1.5 buts, 86 % »
    tient sa promesse. Or c'est la seule ligne que le lecteur voit et sur
    laquelle il decide : une proposition annoncee a 86 % doit se realiser 86
    fois sur 100, sinon le pourcentage ment, meme si le classement des matchs
    est bon.

    Les propositions sont ventilees par **famille** (total, equipe, issue,
    double chance, les deux marquent) parce qu'elles ne passent pas par le meme
    calcul : un biais sur les totaux n'implique rien sur les issues, et les
    confondre masquerait celle qui derape.
    """

    def __init__(self, label: str) -> None:
        self.label = label
        self.calibration = Calibration()
        self.pairs: list[tuple[float, bool]] = []

    def add(self, probability: float, happened: bool) -> None:
        self.calibration.add(probability, happened)
        self.pairs.append((probability, happened))

    @staticmethod
    def _aggregate(pairs: list[tuple[float, bool]]) -> dict[str, Any]:
        n = len(pairs)
        announced = sum(p for p, _ in pairs) / n
        observed = sum(1 for _, hit in pairs if hit) / n
        # Erreur type d'une proportion de Bernoulli : sert a savoir si l'ecart
        # entre annonce et observe merite qu'on y touche.
        error = (observed * (1 - observed) / n) ** 0.5
        return {
            "propositions": n,
            "annonce_moyen": announced,
            "observe": observed,
            # Positif = le modele promet plus qu'il ne tient.
            "ecart": announced - observed,
            "erreur_type": error,
            "significatif": abs(announced - observed) > 2 * error if error else False,
        }

    def summary(self) -> dict[str, Any]:
        if not self.pairs:
            return {"famille": self.label, "propositions": 0}

        # Chaque proposition est enumeree avec son complementaire ("plus de
        # 2.5" et "moins de 2.5") : sur l'ensemble, annonce et observe valent
        # mecaniquement 50 %, quelle que soit la qualite du modele. Le resume
        # ne porte donc que sur la face affirmative de chaque paire -- celle ou
        # le modele penche --, seule a poser une question a laquelle la mesure
        # puisse repondre. La calibration par tranche, elle, reste complete.
        leaning = [pair for pair in self.pairs if pair[0] >= 0.5]
        summary = {"famille": self.label, "calibration": self.calibration.rows(minimum=20)}
        summary.update(self._aggregate(leaning or self.pairs))
        return summary


#: Une proposition a seuil, et rien d'autre : "plus de 2.5", "moins de 9.5".
#: Un test par sous-chaine ne suffit pas -- "Victoire de l'une ou l'autre (PAS
#: DE nul)" et "Pas de match nul" contiennent "s de " et se faisaient compter
#: comme des totaux. Ce sont des propositions d'issue : elles ne passent pas par
#: le partage entre les deux equipes, et les melanger a la colonne Total
#: brouillait precisement celle qui porte la signature des cartons. Le nombre
#: qui suit est donc exige.
_SEUIL = re.compile(r"\b(?:plus|moins) de \d", re.IGNORECASE)


def desequilibre(grandeur: dict[str, Any]) -> float | None:
    """|lam_dom - lam_ext| / total : desequilibre attendu, sans echelle.

    Sans echelle pour que les tranches aient le meme sens sur des buts (2.7
    attendus au total) et sur des corners (9.5) : un ecart d'un demi-evenement
    ne veut pas dire la meme chose dans les deux cas.
    """
    lam_home = grandeur.get("lambda_domicile")
    lam_away = grandeur.get("lambda_exterieur")
    if lam_home is None or lam_away is None:
        return None
    total = lam_home + lam_away
    return abs(lam_home - lam_away) / total if total > 0 else None


def cote_visee(
    libelle: str, teams: tuple[str, str], grandeur: dict[str, Any]
) -> str:
    """Quel cote une proposition engage : "favori", "outsider" ou "total".

    Le favori d'une GRANDEUR n'est pas celui du match : sur les corners, c'est
    l'equipe dont on attend le plus de corners. C'est bien ce qu'il faut ici,
    l'hypothese portant sur le desequilibre de la grandeur mesuree.

    Rend "" pour une proposition qui n'engage ni un cote ni le total (issue,
    double chance, les deux marquent) : ces familles ne passent pas par le
    partage entre les deux equipes et n'ont rien a dire sur la question.
    """
    sujet, separateur, _ = libelle.partition(" : ")
    if not separateur or sujet not in teams:
        # Sans sujet nomme, la proposition porte sur le total, mais seulement si
        # elle porte un seuil : "Match nul" n'est pas un total.
        return "total" if _SEUIL.search(libelle) else ""
    lam_home = grandeur.get("lambda_domicile")
    lam_away = grandeur.get("lambda_exterieur")
    if lam_home is None or lam_away is None:
        return ""
    fort = teams[0] if lam_home >= lam_away else teams[1]
    return "favori" if sujet == fort else "outsider"


class CalibrationConditionnelle:
    """Calibration des propositions VENTILEE selon le desequilibre de l'affiche.

    `OfferCheck` demande : une proposition annoncee a 80 % se realise-t-elle 80
    fois sur 100 ? Elle repond sur la moyenne de tous les matchs, et une moyenne
    juste peut recouvrir deux erreurs egales et opposees.

    Le modele estime les quatre grandeurs avec la MEME structure : attaque x
    defense x moyenne de la competition, chaque force agissant separement. C'est
    le modele de Maher, etabli pour les buts. Rien ne garantit qu'il vaille pour
    les trois autres, et la raison de s'en mefier est la meme dans les trois cas
    -- la production ne depend pas que des deux equipes, elle depend aussi de
    l'ETAT du match, qui n'est pas observable au coup d'envoi mais qui decoule du
    desequilibre attendu, que le modele calcule deja.

    L'interet de ventiler les quatre ensemble est qu'elles ne predisent PAS la
    meme chose. Chacune a sa signature, et c'est la concordance qui vaut preuve,
    pas un ecart isole :

      - **Buts** : Maher tient, c'est son terrain d'origine. Rien n'est attendu
        nulle part. Cette ligne est le TEMOIN du tableau : si elle s'allume, ce
        n'est pas un effet de jeu qui a ete trouve, c'est la mesure qui est
        fausse -- et les autres lignes ne veulent alors plus rien dire.

      - **Corners** : l'equipe menee pousse et prend des corners en fin de
        rencontre, celle qui mene largement gere et en prend moins. Ce que l'une
        prend en plus, l'autre le prend en moins : le TOTAL est a peu pres
        conserve, et l'effet se voit sur le PARTAGE. Signature attendue :
        asymetrie croissante avec le desequilibre, colonne Total plate.

      - **Tirs cadres** : meme mecanisme -- une equipe menee tire davantage --
        donc meme signature que les corners, vraisemblablement plus faible : un
        tir part de partout sur le terrain, la ou un corner suppose d'avoir
        installe le jeu dans le camp adverse. Sa dispersion mesuree (1.15) est
        d'ailleurs entre celle des buts (1.00) et celle des corners (1.37).

      - **Cartons jaunes** : signature INVERSE, et c'est ce qui rend le tableau
        interessant. Un match serre se tend et se hache ; un match plie a la
        demi-heure s'apaise. L'effet ne deplace pas les cartons d'un camp a
        l'autre, il en change le NOMBRE : il doit se voir sur la colonne Total,
        qui decroit quand le desequilibre grandit, et pas sur l'asymetrie.

        Une reserve propre a cette grandeur : le premier responsable du nombre de
        cartons n'est ni l'une ni l'autre equipe, c'est l'arbitre. Le critere 12
        le mesure et reste a poids zero, faute d'une source qui dise QUI avait ete
        designe avant le match. Un effet trouve ici sur les cartons peut donc
        n'etre qu'un effet d'arbitre mal reparti entre les tranches, et devra
        etre confirme a arbitre comparable avant qu'on en tire quoi que ce soit.

    La grandeur mesuree pour le partage est :

        asymetrie = ecart(favori) - ecart(outsider)

    nulle si la structure multiplicative suffit, non nulle et croissante avec le
    desequilibre s'il lui manque un terme d'interaction.

    C'est un TEST, pas une correction : il ne change aucune prevision, il dit
    seulement s'il y a quelque chose a corriger, et sur quelle grandeur. Un
    ecart mesure ici serait la premiere raison chiffree de donner a une grandeur
    autre chose que la structure des buts.

    Les bornes des tranches ne sont pas fixees d'avance mais tirees des donnees,
    par terciles : des seuils choisis sur une intuition d'ordre de grandeur
    produiraient des tranches vides ou desequilibrees selon la grandeur -- le
    desequilibre attendu des corners est bien plus resserre que celui des buts --
    et il n'y a aucune raison de les figer.
    """

    #: Cotes ventiles. "total" ne devrait rien montrer si l'hypothese tient.
    COTES = ("favori", "outsider", "total")

    def __init__(self, label: str, tranches: int = 3) -> None:
        self.label = label
        self.tranches = tranches
        # (match, desequilibre, cote, probabilite annoncee, realisee). Le match
        # est garde parce qu'il est l'unite de tirage : voir `_ecart`.
        self.observations: list[tuple[str, float, str, float, bool]] = []

    def add(
        self,
        match: str,
        ecart_attendu: float,
        cote: str,
        probability: float,
        happened: bool,
    ) -> None:
        if cote not in self.COTES:
            return
        # Meme regle que `OfferCheck` : chaque proposition etant enumeree avec
        # son complementaire, on ne retient que la face ou le modele penche.
        # Sans cela annonce et observe valent 50 % par construction et la mesure
        # ne peut rien dire.
        if probability < 0.5:
            return
        self.observations.append(
            (match, ecart_attendu, cote, probability, happened)
        )

    def _bornes(self) -> list[float]:
        """Bornes des terciles du desequilibre, tirees des observations."""
        valeurs = sorted(o[1] for o in self.observations)
        if len(valeurs) < self.tranches:
            return []
        return [
            valeurs[len(valeurs) * i // self.tranches]
            for i in range(1, self.tranches)
        ]

    @staticmethod
    def _ecart(
        observations: list[tuple[str, float, bool]]
    ) -> dict[str, Any] | None:
        """Annonce - observe, avec une erreur type GROUPEE PAR MATCH.

        Le detail compte, et il a produit un faux positif avant d'etre vu. Un
        meme match fournit une dizaine de propositions -- "plus de 0.5", "plus
        de 1.5", "plus de 2.5"... -- toutes tirees du MEME lambda et du MEME
        resultat. Elles ne sont pas independantes : si le match finit 4-0, elles
        se realisent ou echouent ensemble.

        L'erreur type d'une proportion de Bernoulli, `racine(p(1-p)/n)`, suppose
        n tirages independants. Appliquee ici avec n = nombre de PROPOSITIONS,
        elle divise par la racine d'un effectif que l'echantillon n'a pas, et
        annonce une precision qui n'existe pas : dix-sept matchs y passaient
        pour trois cents observations, et une tranche du temoin -- les buts, ou
        rien n'est attendu -- ressortait a 18 points d'ecart avec une etoile.

        On estime donc la variance ENTRE MATCHS et non entre propositions
        (estimateur groupe, dit « cluster-robust » : Liang et Zeger, 1986). Le
        match est l'unite de tirage, et c'est son compte qui gouverne la
        precision annoncee.
        """
        n = len(observations)
        if not n:
            return None
        annonce = sum(p for _, p, _ in observations) / n
        observe = sum(1 for _, _, hit in observations if hit) / n

        # Somme des residus par match : c'est le match qui est tire au sort,
        # pas la proposition.
        ecart = annonce - observe
        par_match: dict[str, float] = {}
        for cle, p, hit in observations:
            par_match[cle] = par_match.get(cle, 0.0) + (p - (1.0 if hit else 0.0))

        matchs = len(par_match)
        if matchs > 1:
            # Variance de la moyenne d'une somme groupee. Le facteur
            # matchs/(matchs-1) est la correction usuelle pour petit nombre de
            # groupes -- et ici le nombre de groupes EST petit.
            centre = [total - ecart * n / matchs for total in par_match.values()]
            variance = sum(c * c for c in centre) * matchs / (matchs - 1)
            erreur = (variance ** 0.5) / n
        else:
            erreur = None

        return {
            "propositions": n,
            # Le nombre de matchs est rendu et affiche : c'est lui qui dit ce
            # que la mesure vaut, et le cacher derriere un compte de
            # propositions dix fois plus grand serait trompeur.
            "matchs": matchs,
            "annonce": annonce,
            "observe": observe,
            # Positif = le modele promet plus qu'il ne tient, comme partout
            # ailleurs dans ce module.
            "ecart": ecart,
            "erreur_type": erreur,
        }

    def summary(self, minimum: int = 20) -> dict[str, Any]:
        """Une ligne par tranche de desequilibre, et l'asymetrie de chacune.

        `minimum` compte des MATCHS, et non des propositions. La distinction
        n'est pas cosmetique : un seul match fournit une dizaine de
        propositions, si bien qu'un seuil pose sur celles-ci etait franchi par
        dix-sept matchs et laissait passer des ecarts que rien ne soutenait.
        C'est le match qui est tire au sort, donc lui qu'il faut compter.
        """
        bornes = self._bornes()
        if not bornes:
            return {"grandeur": self.label, "tranches": []}

        def rang(valeur: float) -> int:
            return sum(1 for borne in bornes if valeur >= borne)

        lignes = []
        for index in range(self.tranches):
            dedans = [o for o in self.observations if rang(o[1]) == index]
            if not dedans:
                continue
            ecarts = [o[1] for o in dedans]
            ligne: dict[str, Any] = {
                "tranche": index + 1,
                "desequilibre_moyen": sum(ecarts) / len(ecarts),
                "desequilibre_min": min(ecarts),
                "desequilibre_max": max(ecarts),
            }
            for cote in self.COTES:
                mesure = self._ecart(
                    [(m, p, hit) for m, _, c, p, hit in dedans if c == cote]
                )
                # Deux matchs suffisent a produire une erreur type groupee, et
                # elle est alors absurdement petite : la variance entre deux
                # groupes ne mesure rien. Le seuil `minimum` l'ecarte, mais un
                # appelant qui l'abaisse doit rester protege -- une case a deux
                # matchs a produit une etoile qui ne voulait rien dire.
                if mesure and mesure["matchs"] >= max(minimum, 5):
                    ligne[cote] = mesure

            # L'asymetrie n'a de sens que si les deux cotes sont mesures. Son
            # erreur type est celle d'une difference de deux proportions
            # independantes : les deux equipes d'un meme match le sont
            # suffisamment pour ce qu'on en fait ici.
            favori, outsider = ligne.get("favori"), ligne.get("outsider")
            if favori and outsider:
                asymetrie = favori["ecart"] - outsider["ecart"]
                erreur = (
                    favori["erreur_type"] ** 2 + outsider["erreur_type"] ** 2
                ) ** 0.5
                ligne["asymetrie"] = {
                    "valeur": asymetrie,
                    "erreur_type": erreur,
                    "t": asymetrie / erreur if erreur else 0.0,
                    # Deux erreurs types : la meme barre que partout ailleurs
                    # dans ce module. En deca, on ne conclut pas.
                    "significatif": abs(asymetrie) > 2 * erreur if erreur else False,
                }
            # Une tranche dont aucun cote n'atteint le seuil n'apprend rien :
            # l'afficher remplirait le tableau de tirets et donnerait a croire
            # que la mesure a ete faite.
            if any(ligne.get(cote) for cote in self.COTES):
                lignes.append(ligne)
        return {"grandeur": self.label, "bornes": bornes, "tranches": lignes}


def tune(
    matches: Sequence[dict[str, Any]],
    build_with,
    candidates: Sequence[Any],
) -> list[dict[str, Any]]:
    """Compare des reglages sur les memes matchs et les classe.

    `build_with(match, params)` rend la prevision calculee avec ce reglage, ou
    None. Le critere est la log-vraisemblance (log-loss) : c'est la mesure que
    Dixon et Coles minimisent, et celle qui punit le plus nettement une
    certitude erronee.

    Le classement est rendu tel quel, avec l'erreur type de chaque score et
    l'ecart appariee au reglage neutre. C'est a l'appelant de decider si un
    gain merite d'etre adopte : sur quelques centaines de matchs, la plupart
    des ecarts entre reglages voisins ne sortent pas du bruit.
    """
    scores: dict[Any, list[float]] = {}
    briers: dict[Any, list[float]] = {}
    # Le log-loss du SCORE REEL, en plus de celui de l'issue. Certains reglages
    # -- `estimation_dispersion`, `rho` -- agissent d'abord sur la grille des
    # scores, et les classer sur la seule issue revenait a les juger sur ce
    # qu'ils ne touchent qu'indirectement.
    pertes_score: dict[Any, list[float]] = {}

    for match in matches:
        outcome = actual_outcome(match)
        if outcome is None:
            continue
        # Un match ne compte que s'il est evaluable pour TOUS les reglages,
        # sinon on comparerait des moyennes calculees sur des matchs differents.
        row: dict[Any, tuple[float, float]] = {}
        for params in candidates:
            try:
                prediction = build_with(match, params)
            except Exception:  # noqa: BLE001
                prediction = None
            if not prediction:
                break
            goals = next(
                (g for g in prediction["grandeurs"] if g["cle"] == "buts"), None
            )
            if not goals:
                break
            probabilities = goals["resultat"]
            row[params] = (
                log_loss(probabilities, outcome),
                brier(probabilities, outcome),
                perte_du_score(
                    grille_du_score(prediction),
                    (match["score_domicile"], match["score_exterieur"]),
                )
                if match.get("score_domicile") is not None
                else None,
            )
        if len(row) != len(candidates):
            continue
        for params, (loss, score, perte) in row.items():
            scores.setdefault(params, []).append(loss)
            briers.setdefault(params, []).append(score)
            if perte is not None:
                pertes_score.setdefault(params, []).append(perte)

    neutral = candidates[0]
    results = []
    for params in candidates:
        values = scores.get(params) or []
        if not values:
            continue
        ligne = {
            "params": params,
            "matchs": len(values),
            "log_loss": sum(values) / len(values),
            "log_loss_erreur_type": _standard_error(values),
            "brier": sum(briers[params]) / len(briers[params]),
            "vs_neutre": paired_comparison(values, scores[neutral]),
        }
        pertes = pertes_score.get(params) or []
        if pertes and len(pertes) == len(pertes_score.get(neutral) or []):
            ligne["score_log_loss"] = sum(pertes) / len(pertes)
            ligne["vs_neutre_score"] = paired_comparison(
                pertes, pertes_score[neutral]
            )
        results.append(ligne)
    results.sort(key=lambda r: r["log_loss"])
    return results


def tune_offers(
    matches: Sequence[dict[str, Any]],
    build_with,
    candidates: Sequence[Any],
    actual_metrics,
) -> list[dict[str, Any]]:
    """Compare des reglages sur la vraisemblance des PROPOSITIONS.

    `tune` note l'issue du match, donc les buts seuls. Un reglage qui porte sur
    les corners ou les cartons y serait invisible : il faut noter ce que ces
    grandeurs produisent reellement, c'est-a-dire les propositions.

    Le score est la log-vraisemblance binaire moyenne, -log p si la proposition
    s'est realisee, -log(1 - p) sinon. Elle punit la sur-confiance exactement
    comme il faut ici : annoncer 90 % et se tromper coute cher, annoncer 75 %
    et se tromper coute peu.

    Toutes les propositions candidates sont notees, pas seulement les retenues :
    la selection change avec le reglage, et comparer des listes differentes ne
    comparerait plus rien.
    """
    scores: dict[Any, list[float]] = {}

    for match in matches:
        observed = actual_metrics(match)
        if not observed:
            continue
        teams = (match["domicile"], match["exterieur"])

        # Un match ne compte que s'il est evaluable pour TOUS les reglages.
        row: dict[Any, list[float]] = {}
        for params in candidates:
            try:
                prediction = build_with(match, params)
            except Exception:  # noqa: BLE001
                prediction = None
            if not prediction:
                break
            losses: list[float] = []
            for metric in prediction["grandeurs"]:
                values = observed.get(metric["libelle"])
                if not values:
                    continue
                for offer in metric.get("candidats") or []:
                    verdict = verify.check_offer(offer["libelle"], teams, values)
                    if verdict is None:
                        continue
                    probability = min(max(offer["p"], 1e-9), 1 - 1e-9)
                    losses.append(
                        -math.log(probability if verdict else 1 - probability)
                    )
            if not losses:
                break
            row[params] = losses
        if len(row) != len(candidates):
            continue
        for params, losses in row.items():
            scores.setdefault(params, []).extend(losses)

    neutral = candidates[0]
    results = []
    for params in candidates:
        values = scores.get(params) or []
        if not values:
            continue
        results.append(
            {
                "params": params,
                "propositions": len(values),
                "log_loss": sum(values) / len(values),
                "vs_neutre": paired_comparison(values, scores[neutral]),
            }
        )
    results.sort(key=lambda r: r["log_loss"])
    return results


def run(
    matches: Sequence[dict[str, Any]],
    build_prediction,
    baseline_for,
    actual_metrics=None,
) -> dict[str, Any]:
    """Rejoue les matchs termines et compare le modele a ses references.

    `build_prediction(match)` doit rendre la prevision calculee avec coupure
    temporelle, ou None si elle est impossible. `baseline_for(match)` rend la
    reference de championnat, ou None.

    `actual_metrics(match)` est facultatif : il doit rendre les valeurs reelles
    par grandeur, sous la forme {libelle: (domicile, exterieur)}. Sans lui, seule
    l'issue est jugee -- et les colonnes "corners", "tirs cadres" et "cartons"
    d'une fiche restent sans garantie derriere elles.
    """
    model = Evaluation("modele")
    uniform = Evaluation("uniforme")
    frequency = Evaluation("frequences de la competition")
    # Calibration : une prevision peut classer correctement et rester menteuse
    # sur ses pourcentages. Alimentee par les trois issues de chaque match, ce
    # qui est la lecture multiclasse usuelle du diagramme de fiabilite.
    calibration = Calibration()
    checks: dict[str, MetricCheck] = {}
    # Propositions : celles qu'une fiche retiendrait vraiment, et toutes les
    # candidates. Les premieres disent ce que vaut le produit livre ; les
    # secondes couvrent toute l'echelle des probabilites, ce que la tranche
    # 70-95 % des fiches ne permet jamais de mesurer.
    offers_kept = OfferCheck("propositions retenues")
    offers_all = OfferCheck("toutes propositions")
    families: dict[str, OfferCheck] = {}
    # Calibration ventilee par desequilibre de l'affiche, une par grandeur.
    # Elle ne juge aucun reglage : elle cherche un terme qui manquerait au
    # modele, et que la calibration moyenne ne peut pas voir.
    conditionnelles: dict[str, CalibrationConditionnelle] = {}

    # La grille des scores : la ligne la plus lue d'une fiche, et la seule que
    # rien ne jugeait.
    scores = ScoreCheck()
    goal_errors: list[float] = []
    details: list[dict[str, Any]] = []
    # Les exclusions sont ventilees par cause : un match ecarte faute
    # d'historique est une limite du modele, un match ecarte par une coupure
    # reseau est un incident. Les confondre rend le resultat ininterpretable --
    # c'est ce qui a fait passer 119 matchs sur 120 pour "sans historique".
    reasons: Counter = Counter()

    for match in matches:
        outcome = actual_outcome(match)
        if outcome is None:
            reasons["score absent"] += 1
            continue
        try:
            prediction = build_prediction(match)
        except Exception as exc:  # noqa: BLE001 - la cause est rapportee
            reasons["erreur : %s" % type(exc).__name__] += 1
            continue
        if not prediction:
            reasons["historique insuffisant ou source indisponible"] += 1
            continue
        goals = next(
            (g for g in prediction["grandeurs"] if g["cle"] == "buts"), None
        )
        if not goals:
            reasons["aucune prevision de buts"] += 1
            continue

        probabilities = goals["resultat"]
        model.add(probabilities, outcome)
        uniform.add({key: 1 / 3 for key in OUTCOMES}, outcome)
        frequency.add(frequency_reference(baseline_for(match)), outcome)
        for key in OUTCOMES:
            calibration.add(probabilities.get(key, 0.0), key == outcome)

        real_total = match["score_domicile"] + match["score_exterieur"]
        goal_errors.append(abs(goals["total_attendu"] - real_total))

        reference = None
        repere = baseline_for(match) or {}
        moyenne_domicile = repere.get("moyenne_domicile")
        moyenne_exterieur = repere.get("moyenne_exterieur")
        if moyenne_domicile and moyenne_exterieur:
            # La reference reste POISSONNIENNE : elle represente ce qu'on
            # saurait sans rien connaitre des deux equipes, donc sans
            # incertitude d'estimation a elargir.
            reference = predict.score_matrix(
                moyenne_domicile,
                moyenne_exterieur,
                float((prediction.get("reglage") or {}).get("rho", 0.0) or 0.0),
            )
        grille = grille_du_score(prediction)
        if grille:
            scores.add(
                grille,
                reference,
                (match["score_domicile"], match["score_exterieur"]),
            )

        observed = actual_metrics(match) if actual_metrics else None
        teams = (match["domicile"], match["exterieur"])
        for metric in prediction["grandeurs"] if observed else []:
            values = observed.get(metric["libelle"])
            if not values:
                continue
            check = checks.setdefault(
                metric["libelle"], MetricCheck(metric["libelle"])
            )
            check.add(metric["total_attendu"], values[0] + values[1])
            ecart_attendu = desequilibre(metric)

            # Les propositions sont tranchees par le meme code que celui qui
            # verifie les fiches emises (`verify.check_offer`) : ce qui est
            # mesure ici est exactement ce qui sera compte apres coup.
            for offer in metric.get("offres") or []:
                verdict = verify.check_offer(offer["libelle"], teams, values)
                if verdict is None:
                    continue
                offers_kept.add(offer["p"], verdict)
            for candidate in metric.get("candidats") or []:
                verdict = verify.check_offer(candidate["libelle"], teams, values)
                if verdict is None:
                    continue
                offers_all.add(candidate["p"], verdict)
                family = families.setdefault(
                    candidate["famille"], OfferCheck(candidate["famille"])
                )
                family.add(candidate["p"], verdict)
                # Les candidates, et non les retenues : elles couvrent toute
                # l'echelle des probabilites et les deux cotes de chaque
                # affiche, ce qui est exactement ce que la ventilation demande.
                if ecart_attendu is not None:
                    conditionnelles.setdefault(
                        metric["libelle"],
                        CalibrationConditionnelle(metric["libelle"]),
                    ).add(
                        match.get("match_id") or "%s - %s" % teams,
                        ecart_attendu,
                        cote_visee(candidate["libelle"], teams, metric),
                        candidate["p"],
                        verdict,
                    )

        details.append(
            {
                "match": "%s - %s" % (match["domicile"], match["exterieur"]),
                "score": "%s-%s" % (match["score_domicile"], match["score_exterieur"]),
                "issue": outcome,
                "p_issue": probabilities[outcome],
                "attendu": goals["total_attendu"],
                "reel": real_total,
            }
        )

    return {
        "modeles": [model.summary(), frequency.summary(), uniform.summary()],
        # Signe : negatif = le modele fait mieux que la reference.
        "vs_frequences": paired_comparison(model.brier, frequency.brier),
        "vs_uniforme": paired_comparison(model.brier, uniform.brier),
        "scores": scores.summary(),
        "erreur_buts": (
            sum(goal_errors) / len(goal_errors) if goal_errors else None
        ),
        "matchs_evalues": model.count,
        "matchs_ignores": sum(reasons.values()),
        "causes_exclusion": dict(reasons),
        "calibration": calibration.rows(),
        "calibration_conditionnelle": [
            c.summary() for c in conditionnelles.values()
        ],
        "grandeurs": [check.summary() for check in checks.values()],
        "propositions": [
            offers_kept.summary(),
            offers_all.summary(),
            *[family.summary() for family in families.values()],
        ],
        "details": details,
    }
