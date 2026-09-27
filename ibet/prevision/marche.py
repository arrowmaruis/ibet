"""Confrontation des previsions au marche : valeur, selection, et bilan.

Le modele est bien calibre -- la mesure des fiches emises le montre : 75,7 %
observe pour 75,4 % annonce, aucun ecart au-dela de deux erreurs types. Mais
etre calibre ne fait pas gagner d'argent. Un modele parfaitement calibre qui
joue tous les matchs perd exactement la marge de l'operateur, a chaque coup.

Ce qui fait gagner, c'est de ne jouer QUE la ou l'on diverge du marche et ou
l'on a raison. Ce module ne rend donc aucune prevision plus precise : il en
choisit, et il mesure si ce choix paie.

Trois etages, dans cet ordre, parce que le troisieme conditionne les deux
premiers :

  - `valeur` : l'esperance de gain par euro engage, `p x cote - 1`. C'est la
    seule lecture qui tienne compte de ce qu'on touche reellement ; un ecart de
    probabilite au marche, lui, ne dit rien du rendement.

  - `selection` : les propositions du jour dont la valeur passe un seuil, sans
    celles dont l'ecart est demesure. Un ecart enorme au marche n'est presque
    jamais une occasion : c'est le signe que le modele est mal informe -- le
    marche integre l'effectif, la nouvelle du matin et l'argent de gens qui ont
    tort a leurs frais. C'est la meme reserve que le critere 13, appliquee ici
    a une decision et non a un affichage.

  - `bilan` : sur les fiches deja tranchees, est-ce que la valeur annoncee
    s'est traduite en rendement ? Tant que cette mesure n'existe pas, jouer sur
    la valeur est une croyance. Elle est ici la reponse a une question qu'aucune
    autre mesure du projet ne pose : la calibration dit si les pourcentages sont
    sinceres, le Brier si le classement est bon ; ni l'un ni l'autre ne dit si
    le modele bat le MARCHE, qui est pourtant le seul adversaire.

Une limite qu'il faut enoncer plutot que contourner : la base ne releve que les
cotes 1X2. La valeur n'est donc calculable que sur les propositions d'issue --
victoires et match nul. Les totaux, les lignes par equipe et les doubles chances
representent l'essentiel des propositions emises et restent hors de portee tant
qu'aucune source de cotes ne les couvre. Le bilan le dit a chaque appel plutot
que de laisser croire qu'il juge l'ensemble.
"""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

from ibet.stockage import store

# Seuil de valeur au-dela duquel une proposition est retenue. Zero serait le
# seuil naturel -- toute esperance positive vaut d'etre jouee -- mais la valeur
# est calculee a partir d'une probabilite estimee, elle-meme bruitee. Un seuil
# strictement positif est la marge qui absorbe ce bruit : sous 5 %, l'esperance
# annoncee tient dans l'erreur d'estimation du modele et ne vaut pas mieux que
# zero.
VALEUR_MIN = 0.05

# Au-dela, l'ecart au marche cesse d'etre une occasion pour devenir un aveu.
# Meme valeur que `context.ECART_SUSPECT`, dupliquee ici en une constante pour
# que ce module reste sans dependance vers le contexte (qui, lui, lit le
# reseau) ; le commentaire du critere 13 en porte la justification.
VALEUR_DEMESUREE = 0.25

# Ecart de PROBABILITE au-dela duquel la proposition est ecartee, marge retiree.
#
# `VALEUR_DEMESUREE` porte sur l'esperance `p x cote - 1`, qui melange deux
# choses : de combien le modele diverge du marche, et a quel prix. Sur une
# longue cote, un desaccord minuscule produit une esperance enorme -- 21 % au
# lieu de 2.7 % sur une cote a 35.00 donne +638 % d'esperance. Le garde-fou ne
# se declenchait donc pas la ou il fallait.
#
# L'ecart de probabilite, lui, ne depend pas du prix. Il dit directement ce
# qu'on pretend : « je sais quelque chose que le marche ignore, et cela vaut
# tant de points de probabilite ». Au-dela de quinze points sur une issue, ce
# n'est plus un desaccord, c'est une erreur d'un des deux -- et la mesure des
# fiches deja tranchees dit lequel : sur 14 paris de valeur pris, 14 perdus.
ECART_DEMESURE = 0.15

# Libelles des propositions que les cotes 1X2 permettent de valoriser. Les
# doubles chances en sont exclues : leur cote n'est pas celle d'une issue simple
# et ne se deduit pas des trois autres sans hypothese sur la marge.
_NUL = "Match nul"
_VICTOIRE = "Victoire "


def _horodatage(brut: str) -> str:
    """Date et heure comparables, quelle que soit la forme d'origine.

    Les deux sources n'ecrivent pas pareil -- "2026-01-02T09:00+01:00" pour un
    releve de cotes, "2026-01-02 20:00" pour un coup d'envoi -- et une
    comparaison de chaines sur ces formes-la est fausse. On ne garde que les
    seize premiers caracteres, separateur normalise : la minute suffit
    largement pour distinguer un releve d'avant-match d'un releve d'apres.

    Le fuseau est ignore, faute d'etre porte par les deux formes. C'est sans
    consequence ici : les releves sont ecrits au moment de l'emission, donc des
    heures avant le coup d'envoi, et non a la minute pres.
    """
    return brut.replace("T", " ")[:16]


def valeur(probabilite: float, cote: float) -> float | None:
    """Esperance de gain par euro engage : `p x cote - 1`.

    Positive = le pari rapporte en moyenne. Rend None sur une cote inexploitable
    (inferieure ou egale a 1 : un tel prix ne rembourse meme pas la mise).
    """
    if not cote or cote <= 1.0:
        return None
    return probabilite * cote - 1.0



# ---------------------------------------------------------------------------
# Couche 2 : la marge de l'operateur
# ---------------------------------------------------------------------------
#
# Une cote publiee n'est pas une probabilite. Si les probabilites reelles sont
# 50/50, les cotes justes seraient 2.00 et 2.00 ; l'operateur publie 1.90 et
# 1.90. La somme des inverses depasse alors 1 -- cet excedent est sa marge
# (overround, ou vig), typiquement 5 a 8 % sur un 1X2 europeen.
#
# Comparer une probabilite du modele a `1 / cote` revient donc a la comparer a
# une quantite gonflee de la marge, et a conclure trop souvent que le modele
# voit une occasion. Il faut d'abord retirer la marge.

#: Marge au-dela de laquelle un releve n'est plus une reference de probabilite.
#: Un 1X2 a 15 % de marge ne dit pas ou est le prix juste : il dit seulement que
#: l'operateur se protege. On le garde pour miser, jamais pour estimer.
MARGE_MAX_REFERENCE = 0.12

#: Marge SOUS laquelle le releve n'est pas un livre, mais un assemblage.
#: Aucun operateur ne publie un 1X2 a marge nulle ou negative : il perdrait de
#: l'argent a coup sur. Une telle marge signale qu'on a pris le MEILLEUR prix
#: de chaque issue chez des operateurs differents -- ce que fait
#: `api_client._meilleures_cotes`, et qui est le bon calcul pour miser. Les
#: trois prix viennent alors de trois livres qui ne sont d'accord sur rien, et
#: la probabilite qu'on en tirerait n'est celle de personne.
#:
#: Mesure sur les releves deja en base : marge moyenne 1.73 %, minimum -0.93 %.
#: Le minimum negatif etablit que des lignes composites y figurent deja.
MARGE_MIN_REFERENCE = 0.005

#: Iterations de la recherche par dichotomie. 60 pas ramenent l'intervalle de
#: depart sous 1e-18, bien au-dela de la precision d'une cote a deux decimales.
_PAS_DICHOTOMIE = 60


def overround(cotes: dict[str, float]) -> float | None:
    """Marge de l'operateur : somme des inverses, moins 1.

    0.06 = 6 % de marge. Rend None si une cote est inexploitable -- une seule
    issue manquante et la somme ne veut plus rien dire.
    """
    if not cotes or any(not c or c <= 1.0 for c in cotes.values()):
        return None
    return sum(1.0 / c for c in cotes.values()) - 1.0


def _proportionnelle(brutes: dict[str, float]) -> dict[str, float]:
    total = sum(brutes.values())
    return {k: v / total for k, v in brutes.items()}


def _puissance(brutes: dict[str, float]) -> dict[str, float]:
    """Retire la marge en elevant chaque probabilite brute a une puissance.

    On cherche k tel que la somme des `p^k` vaille 1. Comme chaque p est sous 1
    et que leur somme depasse 1, un k superieur a 1 existe toujours.

    Contrairement a la normalisation proportionnelle, cette methode retire
    DAVANTAGE de marge aux gros outsiders qu'aux favoris. C'est ce que le biais
    favori-outsider impose : les operateurs chargent la marge sur les longues
    cotes, parce que c'est la que le public mise mal.
    """
    bas, haut = 1.0, 20.0
    for _ in range(_PAS_DICHOTOMIE):
        milieu = (bas + haut) / 2
        if sum(p ** milieu for p in brutes.values()) > 1.0:
            bas = milieu
        else:
            haut = milieu
    k = (bas + haut) / 2
    tirees = {nom: p ** k for nom, p in brutes.items()}
    # La dichotomie s'arrete a la precision machine, pas exactement a 1 :
    # on renormalise pour que la somme soit exacte.
    return _proportionnelle(tirees)


def _shin(brutes: dict[str, float]) -> dict[str, float]:
    """Modele de Shin (1993), "Optimal betting odds against insider traders",
    Economic Journal 103.

    L'operateur affronte une proportion `z` de parieurs mieux informes que lui ;
    il eleve ses cotes pour s'en proteger, et il le fait d'autant plus que
    l'issue est improbable. Retirer la marge revient alors a resoudre, pour la
    somme des probabilites brutes `Pi` :

        p_i = [ sqrt(z^2 + 4 (1 - z) pi_i^2 / Pi) - z ] / (2 (1 - z))

    et a chercher le `z` qui fait que les `p_i` somment a 1.

    C'est la methode de reference de la litterature sur l'efficience des
    marches de paris. Elle corrige le meme biais que la methode par puissance,
    en le rattachant a une cause plutot qu'a un exposant ajuste.
    """
    somme = sum(brutes.values())

    def _probabilites(z: float) -> dict[str, float]:
        if z <= 0.0:
            return _proportionnelle(brutes)
        return {
            nom: (
                (z * z + 4 * (1 - z) * p * p / somme) ** 0.5 - z
            ) / (2 * (1 - z))
            for nom, p in brutes.items()
        }

    bas, haut = 0.0, 0.99
    for _ in range(_PAS_DICHOTOMIE):
        milieu = (bas + haut) / 2
        if sum(_probabilites(milieu).values()) > 1.0:
            bas = milieu
        else:
            haut = milieu
    return _proportionnelle(_probabilites((bas + haut) / 2))


#: Methodes de retrait de la marge, de la plus naive a la plus argumentee.
METHODES_MARGE = {
    "brute": None,               # aucun retrait : somme > 1
    "proportionnelle": _proportionnelle,
    "puissance": _puissance,
    "shin": _shin,
}

#: Methode retenue par defaut. Shin plutot que la normalisation
#: proportionnelle : celle-ci repartit la marge uniformement, ce qui surestime
#: les outsiders -- exactement la ou le modele croyait voir de la valeur.
METHODE_MARGE = "shin"


def probabilites_implicites(
    cotes: dict[str, float], methode: str = METHODE_MARGE
) -> dict[str, float]:
    """Probabilites du marche, marge retiree.

    Rend {} si le releve est inexploitable. La somme vaut 1 pour toutes les
    methodes sauf "brute", qui rend les inverses tels quels -- utile pour lire
    la marge, jamais pour comparer au modele.
    """
    if not cotes or any(not c or c <= 1.0 for c in cotes.values()):
        return {}
    brutes = {nom: 1.0 / cote for nom, cote in cotes.items()}
    retrait = METHODES_MARGE.get(methode, _shin)
    return brutes if retrait is None else retrait(brutes)


# ---------------------------------------------------------------------------
# Couche 3 : quel prix fait reference
# ---------------------------------------------------------------------------
#
# Une cote publiee bouge ensuite selon les mises recues : si trop d'argent
# arrive sur une issue, l'operateur la baisse pour equilibrer son livre. Tous
# les operateurs ne se valent donc pas comme estimateur de probabilite.
#
# Deux familles font exception et servent de reference dans toute la
# litterature : les operateurs a faible marge qui acceptent les gros paris
# (Pinnacle), et les bourses d'echange ou les parieurs se confrontent
# directement sans intermediaire (Betfair, Smarkets, Matchbook). Leur prix de
# cloture est ce qu'on a de plus proche du vrai.

#: Operateurs dont le prix sert de reference, du plus fiable au moins.
OPERATEURS_REFERENCE = (
    "pinnacle",
    "betfair_ex_eu",
    "betfair",
    "smarkets",
    "matchbook",
)


def est_reference(operateur: str) -> bool:
    """L'operateur fait-il partie de ceux dont le prix sert de reference ?"""
    nom = (operateur or "").strip().lower().replace(" ", "_")
    return any(nom.startswith(cle) for cle in OPERATEURS_REFERENCE)


def rang_operateur(operateur: str) -> int:
    """Rang de fiabilite : 0 = le plus fiable, grand = quelconque."""
    nom = (operateur or "").strip().lower().replace(" ", "_")
    for rang, cle in enumerate(OPERATEURS_REFERENCE):
        if nom.startswith(cle):
            return rang
    return len(OPERATEURS_REFERENCE)


def prix_de_reference(
    releves: Sequence[dict[str, Any]], methode: str = METHODE_MARGE
) -> dict[str, Any]:
    """Le releve qui sert de reference de probabilite, parmi plusieurs.

    L'ordre de preference : un operateur de reference d'abord, puis la marge la
    plus faible, puis le releve le plus tardif. Un prix de cloture incorpore
    tout ce que le marche a appris depuis l'ouverture -- compositions, nouvelle
    du matin, argent informe -- et c'est ce qui en fait la meilleure estimation
    disponible.

    Chaque releve est un dict {"operateur", "releve_le", "cotes"}. Rend {} si
    aucun n'est exploitable.

    N'utilise JAMAIS le maximum par issue tous operateurs confondus. Ce maximum
    est le bon prix pour MISER -- c'est ce qu'un parieur obtient -- mais il ne
    provient d'aucun livre equilibre : prendre le meilleur de N operateurs sur
    chaque issue gonfle les trois et ecrase la marge, parfois sous zero. La
    probabilite qu'on en tirerait ne serait celle de personne.
    """
    candidats = []
    for releve in releves:
        cotes = releve.get("cotes") or {}
        marge = overround(cotes)
        if marge is None or not MARGE_MIN_REFERENCE <= marge <= MARGE_MAX_REFERENCE:
            continue
        candidats.append((
            rang_operateur(releve.get("operateur", "")),
            marge,
            releve.get("releve_le") or "",
            releve,
        ))
    if not candidats:
        return {}
    # Rang croissant, puis marge croissante, puis releve le plus tardif.
    candidats.sort(key=lambda c: (c[0], c[1], _inverse(c[2])))
    rang, marge, _, retenu = candidats[0]
    return {
        "operateur": retenu.get("operateur", ""),
        "releve_le": retenu.get("releve_le", ""),
        "cotes": retenu.get("cotes") or {},
        "marge": marge,
        "reference": rang < len(OPERATEURS_REFERENCE),
        "probabilites": probabilites_implicites(retenu.get("cotes") or {}, methode),
    }


def _inverse(texte: str) -> tuple:
    """Cle de tri qui inverse l'ordre d'une chaine (plus tardif d'abord)."""
    return tuple(-ord(c) for c in texte)


# ---------------------------------------------------------------------------
# Valeur a la cloture (CLV)
# ---------------------------------------------------------------------------

def valeur_a_la_cloture(cote_prise: float, cote_cloture: float) -> float | None:
    """Ecart entre le prix obtenu et le prix de cloture, en part de mise.

    Positive = on a pris un meilleur prix que celui vers lequel le marche a
    converge. C'est la mesure que les parieurs professionnels suivent, et elle
    vaut mieux que le rendement sur un petit echantillon : le rendement depend
    du resultat de quelques matchs, la valeur a la cloture depend seulement du
    prix, qu'on observe a chaque pari. Elle donne donc un signal sur des
    dizaines de paris la ou le rendement en demande des milliers.

    Un modele qui bat regulierement la cloture gagne a la longue, meme quand la
    serie en cours est perdante. Un modele qui la perd regulierement perd, meme
    quand la serie en cours est gagnante.
    """
    if not cote_prise or not cote_cloture or cote_cloture <= 1.0:
        return None
    return cote_prise / cote_cloture - 1.0


# ---------------------------------------------------------------------------
# Melange du modele et du marche
# ---------------------------------------------------------------------------

def melanger(
    modele: dict[str, float], marche: dict[str, float], poids_modele: float
) -> dict[str, float]:
    """Combinaison lineaire des deux sources, renormalisee.

    `poids_modele` a 1 rend le modele seul, a 0 le marche seul. Le melange
    lineaire ("linear opinion pool") plutot que geometrique : il ne peut pas
    produire une probabilite nulle quand l'une des deux sources l'annonce, ce
    qui compte ici parce que le modele annonce parfois des valeurs tres basses
    sur des issues que le marche juge plausibles.

    Melanger n'est pas un aveu de faiblesse : c'est la reponse correcte quand
    deux estimateurs ont chacun de l'information que l'autre n'a pas. Le marche
    connait l'effectif et la nouvelle du matin ; le modele connait le detail des
    comptages. Le poids ne se devine pas -- il se mesure (`poids_optimal`).
    """
    if not marche:
        return dict(modele)
    if not modele:
        return dict(marche)
    poids = min(1.0, max(0.0, poids_modele))
    melange = {
        cle: poids * modele.get(cle, 0.0) + (1 - poids) * marche.get(cle, 0.0)
        for cle in set(modele) | set(marche)
    }
    total = sum(melange.values())
    return {k: v / total for k, v in melange.items()} if total > 0 else melange


def poids_optimal(
    observations: Sequence[tuple[dict[str, float], dict[str, float], str]],
    pas: float = 0.05,
) -> dict[str, Any]:
    """Poids du modele qui minimise le score de Brier du melange.

    Chaque observation est (probabilites du modele, probabilites du marche,
    issue survenue). On balaie les poids de 0 a 1 et on garde le meilleur, avec
    les scores des deux extremes pour que l'appelant voie ce que le melange
    apporte -- ou n'apporte pas.

    Le poids n'a de sens que mesure sur des matchs deja joues, et il ne vaut que
    pour le perimetre sur lequel il a ete mesure : un poids etabli sur des
    matchs de championnat ne dit rien d'une finale de coupe.
    """
    if not observations:
        return {}

    def brier(poids: float) -> float:
        total = 0.0
        for modele, marche, issue in observations:
            melange = melanger(modele, marche, poids)
            total += sum(
                (melange.get(cle, 0.0) - (1.0 if cle == issue else 0.0)) ** 2
                for cle in set(melange)
            )
        return total / len(observations)

    grille = []
    poids = 0.0
    while poids <= 1.0 + 1e-9:
        grille.append((round(poids, 4), brier(poids)))
        poids += pas
    meilleur = min(grille, key=lambda row: row[1])
    return {
        "poids": meilleur[0],
        "brier": meilleur[1],
        "brier_modele_seul": brier(1.0),
        "brier_marche_seul": brier(0.0),
        "matchs": len(observations),
        "grille": grille,
    }


def valeur_apres_marge(
    probabilite: float, cote: float, cotes_du_marche: dict[str, float] | None = None
) -> dict[str, Any]:
    """Valeur d'un pari, avec l'ecart au marche mesure marge retiree.

    `valeur` repond a « combien ce pari rapporte-t-il en moyenne si le modele a
    raison » : elle se calcule sur la cote publiee, marge comprise, parce que
    c'est cette cote-la qu'on encaisse. C'est juste, et insuffisant pour
    decider : une valeur positive peut venir d'un modele qui a raison comme d'un
    modele mal informe.

    L'ecart au marche, lui, doit se mesurer APRES retrait de la marge -- sinon
    on compte la marge de l'operateur comme un desaccord avec lui, et tout
    parait etre une occasion. C'est ce qui manquait : compare a `1 / cote`, un
    modele est en desaccord avec le marche sur chaque issue de chaque match,
    par construction.
    """
    esperance = valeur(probabilite, cote)
    sortie: dict[str, Any] = {"valeur": esperance, "ecart": None, "marge": None}
    if not cotes_du_marche:
        return sortie
    marge = overround(cotes_du_marche)
    implicites = probabilites_implicites(cotes_du_marche)
    sortie["marge"] = marge
    # L'issue concernee est celle dont la cote correspond a celle du pari.
    for nom, valeur_cote in cotes_du_marche.items():
        if abs(valeur_cote - cote) < 1e-9 and nom in implicites:
            sortie["ecart"] = probabilite - implicites[nom]
            sortie["implicite"] = implicites[nom]
            break
    return sortie

def cle_de_marche(pari: str, equipes: tuple[str, str]) -> str:
    """Cle de cote correspondant a une proposition, ou "" si elle n'en a pas.

    Seules les trois issues simples se valorisent avec un releve 1X2. Tout le
    reste rend "" -- et il vaut mieux ne rien dire que valoriser une ligne de
    corners avec la cote d'une victoire.
    """
    if pari == _NUL:
        return "nul"
    if pari.startswith(_VICTOIRE):
        sujet = pari[len(_VICTOIRE):]
        if sujet == equipes[0]:
            return "domicile"
        if sujet == equipes[1]:
            return "exterieur"
    return ""


def _equipes(libelle: str) -> tuple[str, str]:
    """(domicile, exterieur) a partir du libelle "A - B" d'une fiche."""
    domicile, separateur, exterieur = libelle.partition(" - ")
    return (domicile.strip(), exterieur.strip()) if separateur else ("", "")


def offres_valorisees(
    libelle: str,
    offres: Sequence[dict[str, Any]],
    cotes: dict[str, float] | None,
) -> list[dict[str, Any]]:
    """Les propositions d'une fiche auxquelles le marche donne un prix.

    `offres` porte les champs de la table : `pari`, `probabilite`, et `verifie`
    quand la fiche a ete tranchee. Les propositions sans cote correspondante
    sont simplement absentes du resultat.
    """
    if not cotes:
        return []
    equipes = _equipes(libelle)
    # Probabilites du marche, marge retiree : c'est a elles que le modele doit
    # etre compare. Face a `1 / cote`, il est en desaccord avec l'operateur sur
    # chaque issue de chaque match, par construction -- la marge EST ce
    # desaccord.
    marge = overround(cotes)
    implicites = probabilites_implicites(cotes)
    valorisees = []
    for offre in offres:
        cle = cle_de_marche(offre["pari"], equipes)
        if not cle or cle not in cotes:
            continue
        esperance = valeur(offre["probabilite"], float(cotes[cle]))
        if esperance is None:
            continue
        implicite = implicites.get(cle)
        valorisees.append(
            {
                "pari": offre["pari"],
                "probabilite": offre["probabilite"],
                "cote": float(cotes[cle]),
                "valeur": esperance,
                # L'esperance se calcule sur la cote PUBLIEE, marge comprise :
                # c'est elle qu'on encaisse. L'ecart, lui, se mesure sur la
                # probabilite marge retiree : c'est lui qui dit si l'on sait
                # quelque chose.
                "implicite": implicite,
                "ecart": (
                    offre["probabilite"] - implicite if implicite is not None else None
                ),
                "marge": marge,
                "demesure": (
                    implicite is not None
                    and offre["probabilite"] - implicite > ECART_DEMESURE
                ),
                "verifie": offre.get("verifie"),
            }
        )
    return valorisees


#: Marque de la methode de REPLI, celle qu'emploie `predict` quand aucune
#: reference de competition n'a pu etre etablie : les moyennes brutes des deux
#: equipes, sans correction du niveau des adversaires.
#:
#: Elle signale une prevision beaucoup moins fiable que les autres, et la
#: distinction devient critique des qu'on parie. Un match de Coupe d'Europe
#: oppose deux equipes de championnats differents : sans reference, le modele
#: compare la moyenne de buts d'un club grec a celle d'un club autrichien comme
#: si les deux championnats se valaient. Le modele n'a AUCUNE notion de force
#: relative des competitions -- il normalise chaque equipe dans la sienne, puis
#: multiplie. L'ecart au marche qui en resulte est enorme, et c'est le modele
#: qui a tort.
_METHODE_REPLI = "moyenne production / concession"

#: Libelles rendus pour les trois issues, dans l'ordre des cles de cotes.
_LIBELLE_ISSUE = {
    "domicile": "Victoire %s",
    "nul": "Match nul",
    "exterieur": "Victoire %s",
}


def issue_reelle(resultat: dict[str, Any] | None) -> str | None:
    """Quelle issue s'est produite, d'apres le score enregistre. None si illisible."""
    brut = (resultat or {}).get("score")
    if not isinstance(brut, str):
        return None
    domicile, separateur, exterieur = brut.partition(" - ")
    if not separateur:
        return None
    try:
        gauche, droite = float(domicile), float(exterieur)
    except ValueError:
        return None
    if gauche > droite:
        return "domicile"
    return "exterieur" if droite > gauche else "nul"


def paris_d_issue(
    fiche: dict[str, Any], cotes: dict[str, float] | None
) -> list[dict[str, Any]]:
    """Les trois issues d'une fiche, valorisees aux cotes fournies.

    C'est ici, et non dans les propositions retenues, que se trouve la matiere
    du bilan. La raison tient a un desaccord entre ce qu'une fiche MET EN AVANT
    et ce qu'un marche COTE.

    `select_offers` retient les propositions les plus probables au-dessus de
    60 %. Or une victoire simple depasse rarement 60 % sur une affiche
    equilibree : ce sont les totaux larges et les doubles chances qui montent
    si haut. Une fiche typique retient donc « moins de 4.5 buts au total » et
    « Blackburn ou nul », et pas une seule issue seche -- alors que la base ne
    releve, elle, que des cotes 1X2. Les deux ensembles ne se rencontraient
    jamais, et le bilan serait reste vide quel que soit le nombre de fiches
    emises, sans que rien n'indique pourquoi.

    Les probabilites d'issue, elles, sont dans CHAQUE fiche -- le bloc `issue`
    de la grandeur Buts -- qu'une proposition d'issue ait ete retenue ou non.
    Les valoriser toutes les trois donne trois paris par match au lieu de zero,
    et c'est exactement ce que le marche cote.

    Un effet de bord qui n'en est pas un : les trois paris d'un meme match sont
    MUTUELLEMENT EXCLUSIFS. Le groupement par match de `_rendement` cesse d'etre
    une precaution theorique et devient la seule facon correcte de calculer
    l'erreur type.
    """
    if not cotes:
        return []
    buts = next(
        (
            g
            for g in fiche.get("grandeurs") or []
            if g.get("grandeur") == "Buts" or g.get("cle") == "buts"
        ),
        None,
    )
    probabilites = (buts or {}).get("issue") or (buts or {}).get("resultat") or {}
    if not probabilites:
        return []

    # Une prevision de repli n'est pas une prevision de meme qualite, et rien
    # ne le disait une fois la proposition sortie de sa fiche.
    sans_reference = _METHODE_REPLI in ((buts or {}).get("methode") or "")
    equipes = _equipes(fiche.get("match") or fiche.get("libelle") or "")
    survenue = issue_reelle(fiche.get("resultat_reel"))
    paris = []
    for cle, gabarit in _LIBELLE_ISSUE.items():
        probabilite = probabilites.get(cle)
        cote = cotes.get(cle)
        if probabilite is None or not cote:
            continue
        esperance = valeur(float(probabilite), float(cote))
        if esperance is None:
            continue
        nom = equipes[0] if cle == "domicile" else equipes[1]
        paris.append(
            {
                "pari": gabarit % nom if "%s" in gabarit else gabarit,
                "issue": cle,
                "probabilite": float(probabilite),
                "cote": float(cote),
                "valeur": esperance,
                # None tant que le match n'est pas tranche : une prevision en
                # attente n'est pas un echec, et le bilan ne compte qu'elle.
                "verifie": None if survenue is None else cle == survenue,
                "match": fiche.get("match") or fiche.get("libelle") or "",
                "match_id": fiche.get("match_id", ""),
                "sans_reference": sans_reference,
            }
        )
    return paris


#: Marche des cotes indexees par LIBELLE de proposition, par opposition au 1X2
#: dont les cles sont les trois issues. La colonne `marche` de la table existait
#: deja et n'avait jamais servi qu'a "1x2".
MARCHE_LIBELLES = "libelles"


#: Marches de l'agregateur que le modele sait valoriser, et leur cout.
#:
#: Le cout se lit dans la documentation : `[nombre de marches] x [nombre de
#: regions]`. Deux familles a distinguer, et la difference est economique :
#:
#:   - les marches VEDETTES (`h2h`, `totals`, `spreads`) passent par l'appel
#:     GROUPE, une requete pour toute une competition. Deux marches coutent deux
#:     credits, quel que soit le nombre de matchs ;
#:   - les autres exigent un appel PAR MATCH. Six marches sur un match coutent
#:     six credits -- pour dix matchs, soixante. L'appel groupe les refuse
#:     explicitement (HTTP 422, et l'erreur est gratuite).
#:
#: D'ou deux niveaux dans le projet : le groupe par defaut, l'etendu sur
#: demande. Bruler le quota mensuel en une soiree sans l'avoir voulu serait le
#: genre de mauvaise surprise qu'un outil ne doit pas reserver.
MARCHES_GROUPES = "h2h,totals"

MARCHES_ETENDUS = (
    "team_totals,btts,double_chance,odd_even,alternate_totals,"
    "alternate_totals_corners,alternate_team_totals_corners"
)


def _traduire_marche(
    marche: dict[str, Any], equipes: tuple[str, str]
) -> dict[str, float]:
    """Un marche de l'agregateur -> libelles exacts de la fiche.

    Chaque regle est une correspondance CERTAINE, jamais une approximation. Un
    marche non reconnu rend un dictionnaire vide : ne rien dire coute une cote,
    se tromper de marche valorise un pari avec le prix d'un autre.
    """
    from ibet.sources import api_client

    cle = marche.get("key", "")
    rendu: dict[str, float] = {}

    def equipe_de(nom: str) -> str:
        """Nom tel que la FICHE l'ecrit, ou "" si le club n'est pas reconnu."""
        for candidat in equipes:
            if candidat and api_client.memes_equipes(nom, candidat):
                return candidat
        return ""

    for issue in marche.get("outcomes") or []:
        prix = issue.get("price")
        if not prix or float(prix) <= 1.0:
            continue
        prix = float(prix)
        nom = issue.get("name", "")
        point = issue.get("point")
        sens = {"over": "plus", "under": "moins"}.get(nom.lower(), "")

        if cle in ("totals", "alternate_totals") and point is not None and sens:
            rendu["%s de %g buts au total" % (sens.capitalize(), float(point))] = prix

        elif cle == "alternate_totals_corners" and point is not None and sens:
            rendu["%s de %g corners au total" % (sens.capitalize(), float(point))] = prix

        elif cle in ("team_totals", "alternate_team_totals_corners") and sens:
            equipe = equipe_de(issue.get("description", ""))
            if equipe and point is not None:
                grandeur = "corners" if "corners" in cle else "buts"
                rendu["%s : %s de %g %s" % (equipe, sens, float(point), grandeur)] = prix

        elif cle == "btts":
            if nom.lower() == "yes":
                rendu["Les deux equipes marquent"] = prix
            elif nom.lower() == "no":
                rendu["Une equipe au moins ne marque pas"] = prix

        elif cle == "odd_even":
            if nom.lower() == "odd":
                rendu["Nombre total de buts impair"] = prix
            elif nom.lower() == "even":
                rendu["Nombre total de buts pair"] = prix

        elif cle == "double_chance":
            # « Aston Villa or Draw » -> « Aston Villa ou nul » ; « A or B »
            # (les deux equipes) -> « Pas de match nul ».
            gauche, _, droite = nom.partition(" or ")
            if droite.strip().lower() == "draw":
                equipe = equipe_de(gauche)
                if equipe:
                    rendu["%s ou nul" % equipe] = prix
            elif equipe_de(gauche) and equipe_de(droite):
                rendu["Pas de match nul"] = prix
    return rendu


def libelles_depuis_agregateur(
    fiche: dict[str, Any], evenement: dict[str, Any]
) -> tuple[dict[str, float], dict[str, float]]:
    """Traduit les cotes d'un evenement agrege vers le vocabulaire de la fiche.

    Rend `(cotes_issues, cotes_libelles)`, prets pour `paris_du_match`.

    La traduction est le maillon le plus dangereux de la chaine : une regle
    fausse valorise un pari avec le prix d'un autre, et rien ne le signale. On
    ne traduit donc que ce dont la correspondance est certaine :

      - les trois issues, reconnues par le NOM d'equipe de l'agregateur
        rapproche de celui de la fiche (`api_client.memes_equipes`) et par
        « Draw » pour le nul ;
      - les totaux de BUTS, seuls totaux que l'agregateur publie : « Over 2.5 »
        devient « Plus de 2.5 buts au total », qui est le libelle exact
        qu'`offer_candidates` produit.

    Tout le reste est laisse de cote. Les corners, les tirs et les cartons n'ont
    pas de marche chez l'agregateur, et les lignes par equipe non plus : les
    inventer par approximation serait pire que de s'en passer.
    """
    from ibet.sources import api_client

    equipes = _equipes(fiche.get("match") or fiche.get("libelle") or "")
    cotes = evenement.get("cotes") or {}

    issues: dict[str, float] = {}
    for nom, prix in (cotes.get("issues") or {}).items():
        if nom.lower() in ("draw", "nul", "tie"):
            issues["nul"] = float(prix)
        elif equipes[0] and api_client.memes_equipes(nom, equipes[0]):
            issues["domicile"] = float(prix)
        elif equipes[1] and api_client.memes_equipes(nom, equipes[1]):
            issues["exterieur"] = float(prix)

    # Une issue isolee n'est pas exploitable : sans les trois, la marge de
    # l'operateur ne peut pas etre retiree et l'on ne saurait meme pas dire si
    # le rapprochement a porte sur le bon match.
    if len(issues) < 3:
        issues = {}

    libelles: dict[str, float] = {}
    for reference, prix in (cotes.get("totaux") or {}).items():
        sens, _, ligne = reference.partition("|")
        if not ligne:
            continue
        mot = {"over": "Plus", "under": "Moins"}.get(sens.lower())
        if not mot:
            continue
        # %g pour ecrire 2.5 et non 2.50 : c'est la forme qu'emploie
        # `offer_candidates`, et le rapprochement se fait sur le libelle exact.
        libelles["%s de %g buts au total" % (mot, float(ligne))] = float(prix)
    return issues, libelles


def propositions_des_echelles(fiche: dict[str, Any]) -> dict[str, float]:
    """Toutes les lignes des echelles d'une fiche, libelle -> probabilite.

    C'est ce qui ouvre la valorisation au-dela des trois propositions retenues,
    et le blocage etait la : `select_offers` ne garde que ce qui depasse 60 %,
    donc « Moins de 5.5 buts au total », tandis qu'un operateur cote la ligne
    d'equilibre, « Plus de 2.5 buts ». Les deux ensembles ne se rencontraient
    jamais et l'agregateur ne servait a rien.

    Or la fiche porte les ECHELLES COMPLETES -- chaque seuil avec sa
    probabilite, par equipe et au total. Toutes ces lignes sont donc deja
    calculees et engagees ; il suffit de les nommer comme `offer_candidates` les
    nomme pour qu'une cote puisse s'y rapporter. Aucune probabilite n'est
    recalculee ici : on relit la fiche telle qu'elle a ete emise.

    Les deux faces sont rendues. « Plus de 2.5 » et « Moins de 2.5 » sont deux
    lectures de la MEME probabilite (l'une vaut p, l'autre 1 - p), mais un
    operateur cote les deux et leur valeur n'est pas la meme : c'est le prix qui
    decide, pas la probabilite.
    """
    lignes: dict[str, float] = {}
    for grandeur in fiche.get("grandeurs") or []:
        # `noun` tel que `predict.offer_candidates` le construit : le libelle de
        # la grandeur en minuscules. Le rapprochement se faisant sur le libelle
        # exact, la moindre divergence rendrait ces lignes invalorisables.
        nom = (grandeur.get("grandeur") or grandeur.get("libelle") or "").lower()
        if not nom:
            continue

        total = grandeur.get("echelle_total") or {}
        seuils = total.get("seuils") or []
        probabilites = total.get("probabilites") or []
        for seuil, probabilite in zip(seuils, probabilites):
            lignes["Plus de %g %s au total" % (seuil, nom)] = float(probabilite)
            lignes["Moins de %g %s au total" % (seuil, nom)] = 1.0 - float(probabilite)

        equipes = grandeur.get("echelle_par_equipe") or {}
        seuils_equipe = equipes.get("seuils") or []
        for equipe, valeurs in equipes.items():
            if equipe == "seuils" or not isinstance(valeurs, list):
                continue
            for seuil, probabilite in zip(seuils_equipe, valeurs):
                lignes["%s : plus de %g %s" % (equipe, seuil, nom)] = float(probabilite)
                lignes["%s : moins de %g %s" % (equipe, seuil, nom)] = (
                    1.0 - float(probabilite)
                )
    return lignes


def paris_du_match(
    fiche: dict[str, Any],
    cotes_issues: dict[str, float] | None = None,
    cotes_libelles: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """TOUTES les propositions d'une fiche auxquelles un prix est attache.

    Deux sources de prix, et elles ne se recouvrent pas :

      - `cotes_issues` porte les trois issues, indexees par "domicile", "nul",
        "exterieur". C'est le seul marche que la source collecte toute seule :
        la page du jour de BetExplorer le rend cote serveur, alors qu'aucune
        page par match ne rend les autres -- ils sont charges en JavaScript, et
        les lire demanderait d'executer la page. Le projet ne le fait pas.
      - `cotes_libelles` associe une cote au LIBELLE EXACT d'une proposition
        ("Plus de 2.5 buts au total", "Arsenal : plus de 3.5 corners"). C'est
        ce qui ouvre le systeme a tous les marches : totaux, lignes par equipe,
        fourchettes. Ces cotes sont saisies -- par l'API, par la ligne de
        commande -- faute d'une source qui les publie lisiblement.

    Le rapprochement se fait sur le libelle exact, sans interpretation. Deviner
    qu'une cote « O/U 2.5 » correspond a « Plus de 2.5 buts au total » exigerait
    de traduire un vocabulaire d'operateur vers celui du modele, et une erreur
    de traduction valoriserait un pari avec le prix d'un autre : mieux vaut ne
    rien dire que se tromper de marche.
    """
    paris = list(paris_d_issue(fiche, cotes_issues))
    if not cotes_libelles:
        return paris

    survenue = fiche.get("resultat_reel")
    equipes = _equipes(fiche.get("match") or fiche.get("libelle") or "")
    repli = _METHODE_REPLI in (
        (
            next(
                (
                    g
                    for g in fiche.get("grandeurs") or []
                    if g.get("grandeur") == "Buts" or g.get("cle") == "buts"
                ),
                {},
            )
        ).get("methode")
        or ""
    )

    # Les propositions RETENUES d'abord -- elles portent leur verdict quand la
    # fiche a ete tranchee --, puis toutes les lignes des echelles, qui ouvrent
    # la valorisation aux seuils que l'operateur cote reellement.
    connues: dict[str, dict[str, Any]] = {}
    for grandeur in fiche.get("grandeurs") or []:
        for offre in grandeur.get("offres") or []:
            libelle = offre.get("pari") or offre.get("libelle") or ""
            if libelle:
                connues[libelle] = {
                    "probabilite": offre.get("probabilite", offre.get("p")),
                    "grandeur": grandeur.get("grandeur")
                    or grandeur.get("libelle", ""),
                    "verifie": offre.get("verifie"),
                }
    for libelle, probabilite in propositions_des_echelles(fiche).items():
        # Une ligne deja retenue garde son entree : elle porte le verdict du
        # verificateur, que l'echelle ne connait pas.
        connues.setdefault(libelle, {"probabilite": probabilite,
                                     "grandeur": "", "verifie": None})

    for grandeur in [None]:
        for libelle, detail in connues.items():
            cote = cotes_libelles.get(libelle)
            probabilite = detail["probabilite"]
            if not cote or probabilite is None:
                continue
            offre = detail
            esperance = valeur(float(probabilite), float(cote))
            if esperance is None:
                continue
            paris.append(
                {
                    "pari": libelle,
                    "grandeur": detail.get("grandeur", ""),
                    "probabilite": float(probabilite),
                    "cote": float(cote),
                    "valeur": esperance,
                    # `verifie` est deja porte par la proposition quand la fiche
                    # a ete tranchee : on ne le recalcule pas, c'est le
                    # verificateur qui fait foi.
                    "verifie": offre.get("verifie") if survenue else None,
                    "match": fiche.get("match") or fiche.get("libelle") or "",
                    "match_id": fiche.get("match_id", ""),
                    "sans_reference": repli,
                }
            )
    return paris


def meilleure_option(
    paris: Sequence[dict[str, Any]], plafond: float = VALEUR_DEMESUREE
) -> dict[str, Any] | None:
    """La proposition la plus rentable d'un match, ou None si aucune ne l'est.

    Les ecarts demesures sont ecartes AVANT le classement, et non signales comme
    ailleurs : ici on ne montre qu'une ligne par match, et laisser un +129 %
    prendre la place reviendrait a recommander precisement ce que le garde-fou
    dit de ne pas jouer. Si toutes les options d'un match sont demesurees, le
    match ne sort pas -- ce qui est la bonne reponse : le modele y est mal
    informe, il n'a rien a proposer.
    """
    raisonnables = [
        pari for pari in paris if pari["valeur"] <= plafond and pari["valeur"] > 0
    ]
    return max(raisonnables, key=lambda pari: pari["valeur"], default=None)


#: « plus de 9.5 », « moins de 2.5 » : le sens et le seuil, qu'on retire pour ne
#: garder que la grandeur engagee.
_SEUIL_CHIFFRE = re.compile(r"\b(?:plus|moins) de \d+(?:[.,]\d+)?\s*", re.IGNORECASE)


#: Types de conseil, tels qu'une interface les propose a filtrer. La cle est
#: stable (elle voyage dans une URL), le libelle est pour l'oeil.
#:
#: Le filtre porte sur ce que le conseil ENGAGE, pas sur sa forme : un parieur
#: qui ne veut pas de lignes par equipe ne veut pas non plus des corners par
#: equipe. C'est pourquoi grandeur et portee sont deux axes distincts plutot
#: qu'une liste plate de marches.
TYPES_CONSEIL = {
    "buts": "Buts",
    "corners": "Corners",
    "tirs": "Tirs cadres",
    "cartons": "Cartons jaunes",
    "issue": "Issue du match",
    "autre": "Autres marches",
}

#: Portee d'un conseil : sur le total des deux equipes, sur une seule, ou sur
#: l'issue. Un meme parieur joue souvent les totaux et jamais les lignes par
#: equipe -- ce sont deux facons de lire un match, pas deux niveaux de risque.
PORTEES_CONSEIL = {"total": "Au total", "equipe": "Par equipe", "issue": "Issue"}


def type_de_conseil(pari: dict[str, Any]) -> tuple[str, str]:
    """(type, portee) d'une option : ce qu'elle engage, et sur qui.

    Lu du LIBELLE et non d'un champ, parce que les options viennent de deux
    sources qui ne renseignent pas les memes cles -- les issues sont fabriquees
    a partir du bloc `issue` de la fiche, les autres viennent des echelles.
    Le libelle, lui, est le meme des deux cotes et sert deja de cle de
    rapprochement avec les cotes.
    """
    libelle = (pari.get("pari") or "").lower()
    if pari.get("issue") or libelle.startswith("victoire ") or libelle == "match nul":
        return "issue", "issue"
    if libelle.endswith(" ou nul") or libelle == "pas de match nul":
        return "issue", "issue"

    if "corner" in libelle:
        type_ = "corners"
    elif "tirs cadres" in libelle:
        type_ = "tirs"
    elif "carton" in libelle:
        type_ = "cartons"
    elif "but" in libelle or "marquent" in libelle or "marque pas" in libelle:
        type_ = "buts"
    else:
        type_ = "autre"

    portee = "equipe" if " : " in (pari.get("pari") or "") else "total"
    return type_, portee


def filtrer_conseils(
    paris: Sequence[dict[str, Any]],
    types: Sequence[str] = (),
    portees: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Ne garde que les options d'un type et d'une portee voulus.

    Deux listes vides ne filtrent rien : c'est le defaut, et il vaut mieux que
    l'inverse -- un filtre vide qui ne rendrait rien laisserait croire qu'il n'y
    a pas de conseil alors qu'on n'a rien demande.

    Chaque option rendue porte son type et sa portee, pour qu'une interface
    puisse les afficher sans les recalculer -- et surtout sans risquer de les
    calculer autrement que le serveur.
    """
    voulus = {t.strip().lower() for t in types if t.strip()}
    portees_voulues = {p.strip().lower() for p in portees if p.strip()}
    rendu = []
    for pari in paris:
        type_, portee = type_de_conseil(pari)
        if voulus and type_ not in voulus:
            continue
        if portees_voulues and portee not in portees_voulues:
            continue
        rendu.append(dict(pari, type=type_, portee=portee))
    return rendu


def _famille_du_pari(pari: dict[str, Any]) -> str:
    """Famille grossiere d'une option, pour eviter d'en retenir deux jumelles.

    « Plus de 1.5 buts au total » et « Plus de 2.5 buts au total » sont deux
    seuils de la MEME echelle : les proposer ensemble donnerait l'illusion de
    deux choix la ou il n'y en a qu'un, et un parieur qui les combinerait
    parierait deux fois sur la meme chose.

    La famille est donc (grandeur, camp engage) : le total des buts, les buts de
    l'equipe A, les corners au total... Deux options d'une meme famille ne sont
    pas retenues ensemble.
    """
    libelle = pari.get("pari", "")
    if pari.get("issue"):
        return "issue"
    sujet, separateur, reste = libelle.partition(" : ")
    # Le SEUIL est retire dans les deux cas : c'est lui qui distinguait « Plus
    # de 9.5 corners au total » de « Plus de 10.5 corners au total », alors que
    # ce sont deux lectures de la meme echelle. Les proposer ensemble donnait
    # l'illusion de deux choix, et un parieur qui les combinerait parierait deux
    # fois sur la meme chose.
    sans_seuil = _SEUIL_CHIFFRE.sub("", reste if separateur else libelle).strip()
    return "%s|%s" % (sujet, sans_seuil) if separateur else sans_seuil or libelle


def meilleures_options(
    paris: Sequence[dict[str, Any]],
    combien: int = 2,
    plafond: float = VALEUR_DEMESUREE,
) -> list[dict[str, Any]]:
    """Les `combien` options les plus rentables d'un match, sans doublon.

    Deux regles, et aucune n'est cosmetique :

      - les ecarts DEMESURES sont ecartes avant le classement. Au-dela de 25 %
        d'esperance, ce n'est presque jamais une occasion mais le signe que le
        modele est mal informe ; les laisser prendre une place ici reviendrait a
        recommander exactement ce que le garde-fou refuse ;
      - une seule option par FAMILLE. Sans cette regle, les deux meilleures
        etaient presque toujours deux seuils voisins de la meme echelle -- deux
        facons de dire la meme chose, et aucun choix reel. C'est la meme raison
        qui a fait naitre `OFFER_PER_FAMILY` dans le modele.

    Rend moins que `combien` quand le match n'a pas de quoi : mieux vaut une
    option qu'une seconde qu'on ne pense pas.
    """
    retenues: list[dict[str, Any]] = []
    familles: set[str] = set()
    for pari in sorted(paris, key=lambda p: p["valeur"], reverse=True):
        if pari["valeur"] <= 0 or pari["valeur"] > plafond:
            continue
        famille = _famille_du_pari(pari)
        if famille in familles:
            continue
        familles.add(famille)
        retenues.append(dict(pari, famille=famille))
        if len(retenues) >= combien:
            break
    return retenues


def conseils_du_modele(
    fiche: dict[str, Any],
    types: Sequence[str] = (),
    portees: Sequence[str] = (),
    combien: int = 1,
    minimum: float = 0.60,
) -> list[dict[str, Any]]:
    """Les propositions dont le modele est le plus sur, sans aucune cote.

    Critere different de `meilleures_options`, et la difference est le fond du
    sujet. Avec une cote, on classe sur l'ESPERANCE (`p x cote - 1`) : ce qui
    compte est l'ecart au prix, pas la confiance. Sans cote, il ne reste que la
    confiance -- on rend ce que le modele juge le plus probable.

    Les deux repondent a des questions distinctes, et confondre l'une pour
    l'autre coute cher : une proposition a 90 % est une excellente prevision et
    presque toujours un mauvais pari, parce qu'un operateur la cote autour de
    1,10 quand il en faudrait 1,11 pour ne rien perdre. Ce classement-ci ne dit
    donc PAS ou parier ; il dit ce que le modele affirme avec le plus
    d'assurance.

    `minimum` reprend le seuil du modele : sous 60 %, annoncer une proposition
    revient a annoncer un tirage a pile ou face. Le plafond de 95 % est repris
    pour la raison inverse -- « moins de 5.5 buts » a 99 % est vraie par
    construction et n'apprend rien.

    Une seule proposition par FAMILLE, comme ailleurs : sans cette regle, les
    trois meilleures seraient trois seuils voisins de la meme echelle.
    """
    lignes = propositions_des_echelles(fiche)
    if not lignes:
        return []

    # Les issues aussi : elles ne sont pas dans les echelles, mais un conseil
    # « victoire de A » est le plus lisible qui soit.
    buts = next(
        (
            g
            for g in fiche.get("grandeurs") or []
            if g.get("grandeur") == "Buts" or g.get("cle") == "buts"
        ),
        {},
    )
    equipes = _equipes(fiche.get("match") or fiche.get("libelle") or "")
    for cle, probabilite in (buts.get("issue") or {}).items():
        gabarit = _LIBELLE_ISSUE.get(cle)
        if not gabarit:
            continue
        nom = equipes[0] if cle == "domicile" else equipes[1]
        lignes[gabarit % nom if "%s" in gabarit else gabarit] = float(probabilite)

    candidats = [
        {
            "pari": libelle,
            "probabilite": probabilite,
            "match": fiche.get("match") or fiche.get("libelle", ""),
            "match_id": fiche.get("match_id", ""),
        }
        for libelle, probabilite in lignes.items()
        if minimum <= probabilite <= 0.95
    ]
    candidats = filtrer_conseils(candidats, types, portees)

    retenus: list[dict[str, Any]] = []
    familles: set[str] = set()
    for candidat in sorted(
        candidats, key=lambda c: c["probabilite"], reverse=True
    ):
        famille = _famille_du_pari(candidat)
        if famille in familles:
            continue
        familles.add(famille)
        retenus.append(dict(candidat, famille=famille))
        if len(retenus) >= combien:
            break
    return retenus


def composer(
    fiches: Sequence[dict[str, Any]],
    combien: int = 2,
    valeur_min: float = VALEUR_MIN,
    plafond: float = VALEUR_DEMESUREE,
    types: Sequence[str] = (),
    portees: Sequence[str] = (),
) -> dict[str, Any]:
    """Un bulletin : les meilleures options de chaque match retenu.

    `fiches` sont celles que l'appelant a CHOISIES -- le composeur ne decide pas
    des matchs, il decide des options. Chacune porte ses cotes, comme pour
    `par_match`.

    Le total du bulletin est calcule et rendu, mais avec une reserve qui en
    conditionne la lecture : **deux options d'un meme match ne sont pas
    independantes**. « Plus de 2.5 buts » et « Victoire de A » se realisent
    ensemble bien plus souvent que le produit de leurs probabilites ne le dit --
    un match ouvert favorise les deux. Multiplier leurs probabilites SOUS-ESTIME
    donc les chances du combine, et multiplier leurs cotes surestime ce qu'un
    operateur accepterait d'en payer : la plupart refusent purement et
    simplement de combiner deux selections du meme match, ou appliquent leur
    propre correlation.

    Le total n'est donc rendu que pour un bulletin d'une option PAR MATCH, ou
    l'independance est defendable. Au-dela, le champ dit pourquoi il est absent
    plutot que de rendre un nombre faux.
    """
    lignes: list[dict[str, Any]] = []
    for fiche in fiches:
        paris = paris_du_match(
            fiche, fiche.get("cotes"), fiche.get("cotes_libelles")
        )
        # Le filtre s'applique AVANT le choix : demander « seulement les
        # corners » doit rendre le meilleur conseil sur les corners, et non
        # retirer le meilleur conseil du match quand il portait sur autre chose.
        examinees = len(paris)
        paris = filtrer_conseils(paris, types, portees)
        if not paris:
            continue
        options = [
            option
            for option in meilleures_options(paris, combien, plafond)
            if option["valeur"] >= valeur_min
        ]
        if options:
            lignes.append(
                {
                    "match": fiche.get("match") or fiche.get("libelle", ""),
                    "match_id": fiche.get("match_id", ""),
                    "coup_denvoi": fiche.get("coup_denvoi_local", ""),
                    "options_examinees": examinees,
                    "options_retenues_par_filtre": len(paris),
                    "options": options,
                }
            )
    lignes.sort(
        key=lambda ligne: ligne["options"][0]["valeur"], reverse=True
    )

    # Le combine d'UNE option par match : le seul dont le produit ait un sens.
    simples = [ligne["options"][0] for ligne in lignes]
    cumul: dict[str, Any] = {"selections": len(simples)}
    if simples:
        cote = 1.0
        probabilite = 1.0
        for option in simples:
            cote *= option["cote"]
            probabilite *= option["probabilite"]
        cumul.update(
            {
                "cote_combinee": cote,
                "probabilite_combinee": probabilite,
                "valeur_combinee": probabilite * cote - 1.0,
                # Une seule selection ratee fait tomber tout le bulletin : a dix
                # selections a 60 %, il se realise six fois sur mille. La cote
                # monte vite, la probabilite s'effondre plus vite encore.
                "avertissement": (
                    "Le combine ne paie que si TOUTES les selections passent. "
                    "Chaque ajout multiplie la cote mais divise les chances, et "
                    "l'esperance d'un combine n'est meilleure que celle de ses "
                    "parties que si chacune est deja positive -- la marge de "
                    "l'operateur, elle, se cumule a chaque etage."
                ),
            }
        )
    return {
        "matchs": lignes,
        "combine_simple": cumul,
        "note_correlation": (
            "Les options d'un MEME match ne sont pas independantes : elles "
            "engagent le meme resultat, et leurs probabilites ne se multiplient "
            "pas. Aucun total n'est calcule sur elles, et la plupart des "
            "operateurs refusent d'ailleurs de les combiner. Le combine rendu "
            "ne prend que la MEILLEURE option de chaque match."
        ),
    }


def par_match(
    fiches: Sequence[dict[str, Any]],
    valeur_min: float = VALEUR_MIN,
    plafond: float = VALEUR_DEMESUREE,
) -> list[dict[str, Any]]:
    """Une ligne par match : l'option la plus favorable, les meilleures devant.

    C'est la lecture qu'un parieur cherche, et elle differe de `selection` : la
    seconde classe TOUTES les propositions de toutes les fiches, la premiere
    n'en garde qu'une par rencontre. Un match dont trois options sortent n'a pas
    trois fois plus de valeur qu'un autre -- il a une meilleure option, et les
    deux suivantes engagent le meme resultat.

    Chaque ligne porte `options_examinees` : sans lui, une ligne a +6 % ne dirait
    pas si elle a ete choisie parmi trois propositions ou parmi trente, alors
    que c'est ce qui separe une occasion d'un maximum de bruit.
    """
    lignes = []
    for fiche in fiches:
        paris = paris_du_match(
            fiche, fiche.get("cotes"), fiche.get("cotes_libelles")
        )
        if not paris:
            continue
        meilleure = meilleure_option(paris, plafond)
        if not meilleure or meilleure["valeur"] < valeur_min:
            continue
        lignes.append(dict(meilleure, options_examinees=len(paris)))
    lignes.sort(key=lambda ligne: ligne["valeur"], reverse=True)
    return lignes


def selection(
    fiches: Sequence[dict[str, Any]],
    valeur_min: float = VALEUR_MIN,
    plafond: float = VALEUR_DEMESUREE,
) -> list[dict[str, Any]]:
    """Les paris du jour qui valent d'etre joues, les meilleurs devant.

    `fiches` est une liste de fiches telles que `store` les rend, chacune
    completee d'une cle `cotes`. Les trois ISSUES de chaque fiche sont
    valorisees -- et non ses propositions retenues, pour la raison exposee dans
    `paris_d_issue` : celles-ci sont des totaux et des doubles chances que rien
    ne cote ici, si bien que la selection ne rendait jamais rien.

    Le plafond ecarte les ecarts demesures : ils ne sont pas retires en silence
    mais rendus a part, parce qu'un ecart de 40 % au marche est une information
    -- sur le modele, pas sur le match.
    """
    retenues, ecartees = [], []
    for fiche in fiches:
        for pari in paris_d_issue(fiche, fiche.get("cotes")):
            if pari["valeur"] < valeur_min:
                continue
            (ecartees if pari["valeur"] > plafond else retenues).append(pari)
    retenues.sort(key=lambda ligne: ligne["valeur"], reverse=True)
    ecartees.sort(key=lambda ligne: ligne["valeur"], reverse=True)
    return retenues + [dict(ligne, demesure=True) for ligne in ecartees]


def _rendement(paris: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Rendement d'une mise plate de un euro par pari, et son erreur type.

    Mise plate, et non proportionnelle a la valeur : un rendement calcule sur
    des mises variables melange la qualite des paris et celle du dosage, et on
    ne cherche ici a mesurer que la premiere.

    L'erreur type est indispensable et non decorative. Le gain d'un pari a cote
    3 vaut +2 ou -1 : sa variance est enorme, et sur deux cents paris un
    rendement de +8 % ne se distingue pas de zero. Sans cette colonne, le
    tableau inviterait a conclure de ce qui n'est que du bruit.
    """
    n = len(paris)
    if not n:
        return {"paris": 0}
    gains = [
        (pari["cote"] - 1.0) if pari["verifie"] else -1.0
        for pari in paris
    ]
    moyenne = sum(gains) / n

    # Erreur type GROUPEE PAR MATCH, comme dans `backtest`. Un match peut
    # fournir plusieurs paris -- "Victoire A", "Match nul", "Victoire B" -- et
    # ils sont MUTUELLEMENT EXCLUSIFS : un seul peut gagner. Les traiter comme
    # des tirages independants annonce une precision que l'echantillon n'a pas,
    # et c'est le match qui est tire au sort, pas le pari.
    #
    # Aujourd'hui les fiches ne retiennent qu'une proposition d'issue par match
    # et le groupement ne change donc rien. Il est ecrit quand meme : la
    # correction ne doit pas dependre d'un etat de la base qu'un seuil different
    # ferait changer sans prevenir.
    par_match: dict[str, float] = {}
    for pari, gain in zip(paris, gains):
        cle = pari.get("match_id") or pari.get("match") or ""
        par_match[cle] = par_match.get(cle, 0.0) + (gain - moyenne)
    matchs = len(par_match)
    if matchs > 1:
        variance = sum(c * c for c in par_match.values()) * matchs / (matchs - 1)
        erreur = (variance ** 0.5) / n
    else:
        erreur = None
    return {
        "paris": n,
        # Le compte de matchs, seul denominateur honnete quand plusieurs paris
        # d'un meme match entrent dans la meme tranche.
        "matchs": matchs,
        "valeur_moyenne": sum(p["valeur"] for p in paris) / n,
        "probabilite_moyenne": sum(p["probabilite"] for p in paris) / n,
        "reussite": sum(1 for p in paris if p["verifie"]) / n,
        "cote_moyenne": sum(p["cote"] for p in paris) / n,
        "rendement": moyenne,
        "rendement_erreur_type": erreur,
        "significatif": (
            abs(moyenne) > 2 * erreur if erreur else False
        ),
    }


#: Tranches de valeur annoncee. La premiere est negative a dessein : ce sont les
#: paris que le modele juge PERDANTS. S'ils rendent moins que les autres, la
#: valeur trie quelque chose de reel ; s'ils rendent autant, elle ne trie rien,
#: et c'est ce resultat-la qu'il faut pouvoir lire.
TRANCHES = (
    (-9.0, 0.0, "valeur negative"),
    (0.0, 0.05, "0 a 5 %"),
    (0.05, 0.15, "5 a 15 %"),
    (0.15, 0.25, "15 a 25 %"),
    (0.25, 9.0, "au-dela de 25 %"),
)


def bilan(limite: int = 2000) -> dict[str, Any]:
    """La valeur annoncee s'est-elle traduite en rendement ? Sur la base.

    Ne retient que les propositions TRANCHEES et pour lesquelles un releve de
    cotes existe, pris avant le coup d'envoi. Les autres ne sont pas des echecs,
    elles ne sont pas encore des mesures : le compte des unes et des autres est
    rendu, sans quoi un tableau vide se lirait comme un mauvais resultat.

    La lecture attendue, si l'ecart au marche vaut quelque chose : le rendement
    monte avec la tranche de valeur. S'il est plat, le modele diverge du marche
    sans le battre -- et la selection ne sert a rien, ce qui est un resultat
    utile et se dit tel quel.
    """
    with store.connect() as connection:
        lignes = connection.execute(
            """
            SELECT match_id, libelle, coup_denvoi_local, payload
              FROM predictions
             WHERE resultat_reel IS NOT NULL
             ORDER BY coup_denvoi_local
             LIMIT ?
            """,
            (limite,),
        ).fetchall()

        # Un seul passage sur les cotes : le dernier releve ANTERIEUR au coup
        # d'envoi. Prendre le dernier tout court laisserait entrer un releve
        # d'apres-match, qui connait le resultat.
        releves = connection.execute(
            "SELECT match_id, releve_le, valeurs FROM cotes"
            " WHERE marche = '1x2' ORDER BY releve_le"
        ).fetchall()

    coup_denvoi = {ligne["match_id"]: ligne["coup_denvoi_local"] for ligne in lignes}
    cotes: dict[str, dict[str, float]] = {}
    for releve in releves:
        limite_horaire = coup_denvoi.get(releve["match_id"])
        # Les deux horodatages n'ont pas le meme format : les cotes sont
        # horodatees en ISO ("2026-01-02T09:00+01:00"), le coup d'envoi en heure
        # locale lisible ("2026-01-02 20:00"). Les comparer tels quels donnait
        # un resultat faux et silencieux : le "T" de l'ISO vaut 0x54, l'espace
        # 0x20, si bien que TOUT releve d'avant-match paraissait posterieur au
        # coup d'envoi et se trouvait ecarte -- le bilan restait vide sans dire
        # pourquoi. On ramene donc les deux a la meme forme avant de comparer.
        # Sans coup d'envoi connu, on REFUSE le releve au lieu de l'accepter.
        # Le schema autorise un `coup_denvoi_local` vide, et la version
        # precedente sautait alors le filtre : une cote relevee APRES le match,
        # qui en connait donc le resultat, entrait dans le bilan et le rendait
        # flatteur. Perdre quelques paris mesurables vaut mieux qu'en mesurer un
        # seul avec une cote impossible a obtenir avant le coup d'envoi.
        if not limite_horaire:
            continue
        if _horodatage(releve["releve_le"]) > _horodatage(limite_horaire):
            continue
        cotes[releve["match_id"]] = json.loads(releve["valeurs"])

    paris: list[dict[str, Any]] = []
    avec_cotes = 0
    for ligne in lignes:
        fiche = json.loads(ligne["payload"])
        fiche.setdefault("match_id", ligne["match_id"])
        prix = cotes.get(ligne["match_id"])
        if prix:
            avec_cotes += 1
        trouves = [
            pari
            for pari in paris_d_issue(fiche, prix)
            # Un pari dont l'issue n'a pas pu etre lue n'est pas un echec : il
            # n'est simplement pas encore une mesure.
            if pari["verifie"] is not None
        ]
        paris.extend(trouves)

    tranches = []
    for bas, haut, libelle in TRANCHES:
        dedans = [p for p in paris if bas <= p["valeur"] < haut]
        if dedans:
            tranches.append(dict(_rendement(dedans), tranche=libelle))

    return {
        "offres_tranchees": len(lignes),
        "matchs_avec_cotes": avec_cotes,
        "matchs_tranches": len(lignes),
        "paris_valorises": len(paris),
        "ensemble": _rendement(paris),
        "tranches": tranches,
        # Ce que la mesure ne couvre PAS, dit a chaque appel : les cotes de la
        # base sont 1X2, donc totaux, lignes par equipe et doubles chances --
        # l'essentiel des propositions emises -- restent hors de portee.
        # Les fiches tranchees pour lesquelles aucune cote d'avant-match
        # n'a ete relevee : elles ne sont pas mesurables, et le dire evite de
        # prendre un bilan maigre pour un mauvais bilan.
        "hors_portee": len(lignes) - avec_cotes,
    }
