"""Les quatorze criteres de decision, autour du modele statistique.

`predict.py` repond a une question etroite : combien de buts, de corners, de
tirs et de cartons attendre, etant donne ce que les deux equipes ont produit et
concede. C'est un modele de comptage, et il ne sait rien de ce qui entoure le
match -- qui arbitre, sous quelle pluie, apres combien de jours de repos, avec
quel enjeu.

Ce module rassemble ce contexte. Il ne remplace pas le modele : il lui fournit
des **corrections mesurees et bornees**, et fournit au lecteur ce que le modele
ne dit pas.

Trois principes, tous consequences de la meme exigence :

  1. **Chaque critere est independant et faillible.** Une competition sans
     classement, un arbitre pas encore designe, une ville introuvable : chacun
     de ces cas se declare `disponible: False` et n'empeche ni les treize
     autres, ni la prevision. Un critere absent vaut neutre, jamais zero.

  2. **Aucune correction n'est appliquee sans etre affichee.** Chaque critere
     porte son `effet` -- le multiplicateur exact qu'il applique a chaque
     grandeur, et de quel cote. Une correction invisible serait indefendable :
     on ne saurait pas, apres coup, ce qui a produit la probabilite annoncee.

  3. **Les corrections sont bornees.** Quatorze criteres qui se multiplient
     peuvent, chacun raisonnable, composer un facteur absurde. Le produit est
     donc plafonne par grandeur (`PLAFOND`), et chaque critere porte un poids
     reglable dans `Poids` -- comme `predict.Params` pour le modele, et pour la
     meme raison : `backtest.tune` doit pouvoir les comparer sur les memes
     matchs.

Ce que chaque critere sait faire, et avec quelles donnees :

     #   Critere                    Source                        Effet
     1   Style de jeu               statistiques de forme         corners, cartons, buts
     2   Forme et rang              historique + classement       buts
     3   Systeme et effectif        sportsgambler + compositions  buts
     4   Adversaires comparables    forces de la competition      ponderation de l'historique
     5   Confrontations directes    historique des rencontres     buts
     6   Enjeu de la competition    nom de la phase + classement  buts, cartons
     7   Domicile / exterieur       moyennes de la competition    deja dans le modele
     8   Fatigue et calendrier      dates des matchs              buts, cartons
     9   Motivation                 rang, revanche, cartons H2H   cartons
    10   Meteo                      Open-Meteo                    buts, corners, cartons
    11   Statistiques avancees      xG de chaque match            buts
    12   Discipline de l'arbitre    arbitre + ses matchs          cartons
    13   Cotes du marche            betexplorer (~30 operateurs)  aucun (ecart affiche)
    14   Taille de l'echantillon    tout ce qui precede           score de confiance

Les criteres 7 et 14 ne corrigent rien : le premier est deja dans le modele
(moyennes a domicile et a l'exterieur de la competition, plus `Params.home_edge`
laisse a zero faute de gain mesurable), le second qualifie la prevision au lieu
de la deplacer. Ils figurent quand meme dans la liste : un critere qu'on a
regarde et juge deja couvert est une information, et l'omettre laisserait croire
qu'il a ete oublie.

Le critere 13 ne s'aligne jamais sur le marche : il en mesure l'ECART, ce qui
n'est pas la meme chose. S'y aligner reviendrait a recopier le marche en croyant
le prevoir. Ses cotes viennent de BetExplorer, qui partage les identifiants de
match de Flashscore -- lequel n'en publie pas sur son edition francaise.

Enfin, les poids par defaut sortent d'une mesure et non d'une intuition : trois
criteres sur onze ameliorent la prevision et sont actifs, quatre la degradent et
restent a zero, quatre ne sont pas mesurables a posteriori. Le detail est dans le
README, section « Ce que les quatorze criteres ont donne ». Un critere a zero est
calcule et affiche : il informe le lecteur sans deplacer aucune probabilite.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any, NamedTuple, Sequence

import api_client


# ---------------------------------------------------------------------------
# Reglages
# ---------------------------------------------------------------------------

class Poids(NamedTuple):
    """Poids de chaque critere, entre 0 (neutre) et 1 (effet plein).

    Groupes comme `predict.Params`, et pour la meme raison : `backtest.tune` et
    `backtest.tune_offers` doivent pouvoir les faire varier sur les memes matchs
    et les classer. Ils sont enregistres dans chaque fiche -- une prevision qui
    ne dit pas quels criteres l'ont deplacee n'est pas verifiable.

    **Les valeurs par defaut sortent d'une mesure, pas d'une intuition** : 80
    matchs de huit grands championnats, historique coupe au coup d'envoi,
    classement et arbitre coupes (voir README, « Ce que les quatorze criteres
    ont donne »). Trois criteres ameliorent la prevision et sont actifs ; quatre
    la degradent et restent a zero, mecanisme en place ; quatre ne sont pas
    mesurables a posteriori, faute d'une source qui dise ce qu'elle disait avant
    le match.

    Un critere a zero est calcule et AFFICHE mais ne deplace rien. C'est le
    reglage de ceux dont la mesure ne soutient pas l'effet, et cela reste la
    seule facon honnete de les garder sous la main : le code est ecrit et teste,
    et une mesure ulterieure pourra les reprendre.
    """

    # --- Retenus : mesures, et gagnants sur les deux mesures ---------------
    # Le choc des styles, de loin le plus utile (t = -7,2 sur les propositions).
    style: float = 1.0
    # Les buts attendus, melanges au quart. A 0,50 et 0,75 le melange ameliore
    # encore les propositions mais degrade l'issue : 0,25 est la seule valeur
    # qui gagne sur les deux.
    xg: float = 0.25
    # Le repos entre deux matchs. Petit mais net (t = -3,8).
    fatigue: float = 1.0

    # --- Ecartes : mesures, et perdants ------------------------------------
    # La ressemblance des adversaires degrade, et d'autant plus qu'on lui donne
    # de poids -- une pente, pas du bruit.
    similarite: float = 0.0
    # Les confrontations directes ameliorent l'issue (t = -1,0, du bruit) mais
    # degradent nettement les propositions (t = +4,1, et +5,3 a poids plein).
    # La seconde mesure porte sur 12 898 propositions contre 80 matchs : c'est
    # elle qui tranche.
    confrontations: float = 0.0
    # La meteo degrade, et de facon tres nette (t = +6,1). Le raisonnement tient
    # -- la pluie gene le jeu court -- mais les seuils retenus ne retrouvent pas
    # cet effet dans les comptages.
    meteo: float = 0.0
    # L'ecart entre forme recente et forme d'ensemble degrade legerement les
    # deux mesures.
    forme: float = 0.0

    # --- Non mesurables a posteriori ---------------------------------------
    # Ces quatre-la ne peuvent pas etre evalues sur des matchs passes : leurs
    # sources ne publient que l'ETAT COURANT (la liste des blesses
    # d'aujourd'hui, le classement d'aujourd'hui) ou publient apres coup
    # (l'arbitre). Les mesurer en retrospectif reviendrait a donner au modele ce
    # que personne n'avait avant le match.
    #
    # Ils restent donc a zero. Ce n'est pas un jugement sur leur pertinence,
    # c'est l'absence de mesure -- et la campagne vient justement de montrer que
    # quatre criteres parfaitement defendables sur le papier degradent la
    # prevision. Les activer sans mesure serait parier.
    #
    # Ils deviendront mesurables PROSPECTIVEMENT : chaque fiche emise enregistre
    # desormais son contexte tel qu'il etait au moment de l'emission, donc la
    # liste des absents et l'arbitre de ce jour-la. Quelques dizaines de fiches
    # verifiees suffiront. En attendant, `POIDS_CONTEXTE` permet de les essayer.
    effectif: float = 0.0
    # L'arbitre est desormais DANS le modele des cartons (2.0.0, profil tire des
    # feuilles de match archivees). Le critere 12 reste affiche, mais le
    # remonter ici compterait l'arbitre deux fois : il doit rester a zero.
    arbitre: float = 0.0
    motivation: float = 0.0
    # Aucun match couperet dans l'echantillon : le critere n'a rien deplace, et
    # son ecart mesure est exactement nul. Ce n'est pas un resultat.
    enjeu: float = 0.0


def _poids_du_reglage(defaut: Poids) -> Poids:
    """Poids surcharges par la variable d'environnement `POIDS_CONTEXTE`.

    Format : `style=1,xg=0.25,effectif=0.5`. Sert a essayer un critere laisse a
    zero sans toucher au code -- en particulier les quatre qui ne sont pas
    mesurables a posteriori et que seul un usage reel pourra departager.

    Une cle inconnue ou une valeur illisible est ignoree en silence : un
    reglage mal ecrit dans un `.env` ne doit pas empecher une prevision de
    sortir, et le reglage effectivement employe figure de toute facon dans
    chaque fiche.
    """
    brut = os.getenv("POIDS_CONTEXTE", "").strip()
    if not brut:
        return defaut
    valeurs = defaut._asdict()
    for morceau in brut.split(","):
        cle, _, valeur = morceau.partition("=")
        cle = cle.strip()
        if cle not in valeurs:
            continue
        try:
            valeurs[cle] = max(0.0, min(1.0, float(valeur.strip())))
        except ValueError:
            continue
    return Poids(**valeurs)


DEFAULT_POIDS = _poids_du_reglage(Poids())


# Plafond du produit de toutes les corrections, par grandeur. Quatorze facteurs
# chacun a 3 % composent 1.5 s'ils vont tous dans le meme sens : le plafond est
# ce qui empeche un empilement de petites corrections defendables de produire un
# nombre attendu que rien ne justifie.
#
# Plus large sur les cartons : c'est la grandeur ou le contexte pese le plus
# (arbitre, enjeu, meteo) et ou la moyenne est la plus basse, donc ou un ecart
# de 15 % reste un demi-carton.
PLAFOND = {
    "buts": 1.12,
    "corners": 1.15,
    "tirs_cadres": 1.12,
    "cartons_jaunes": 1.25,
}

# Nombre de matchs fictifs au niveau moyen ajoute a chaque estimation de
# contexte. Meme role que `predict.SHRINKAGE` : une tendance tiree de trois
# matchs ne doit pas peser autant qu'une tendance tiree de vingt.
LISSAGE = 6.0

# Profondeur des « derniers matchs » quand un critere oppose la forme recente a
# la forme d'ensemble (critere 2).
FENETRE_RECENTE = 5

# Confrontations directes retenues, les plus recentes d'abord.
#
# Le flux en rend l'historique COMPLET : quarante-neuf rencontres pour un derby
# de Manchester, dont les plus anciennes datent d'un demi-siecle. Les moyenner
# toutes reviendrait a laisser des equipes qui n'existent plus sous cette forme
# peser sur un match d'aujourd'hui -- et, par la taille de l'echantillon, a leur
# donner presque tout le poids que le lissage accorde.
H2H_RECENTES = 10

# Confrontations dont on va chercher les statistiques detaillees (criteres 5 et
# 9). Chacune coute une requete la premiere fois, puis rien : un match termine
# ne change plus.
H2H_STATS_MAX = 6

# Matchs interroges pour etablir le profil d'un arbitre (critere 12). Le profil
# n'est utilise qu'au-dela de ARBITRE_MIN rencontres : sous ce seuil, la
# moyenne de cartons d'un arbitre est celle des equipes qu'il a arbitrees.
ARBITRE_MAX = 24
ARBITRE_MIN = 4

# Repos en deca duquel une equipe est consideree en enchainement (jours).
REPOS_COURT = 4
# Fenetre sur laquelle on compte les matchs joues, pour la charge (jours).
FENETRE_CHARGE = 21


# Catalogue des quatorze criteres : ce qu'ils regardent, avec quelles donnees,
# et sur quoi ils agissent. Une seule source, lue par l'API (`GET /api/criteres`),
# par l'affichage et par la documentation -- trois listes a maintenir en
# parallele auraient diverge des le premier critere ajoute.
#
# `grandeurs` est la liste de ce qu'un critere peut deplacer. Vide = il informe
# sans corriger, ce qui est le cas des criteres 7, 13 et 14, et du 4 (qui agit
# avant la moyenne, pas sur son resultat).
CATALOGUE = [
    {
        "numero": 1, "cle": "style_de_jeu", "poids": "style",
        "libelle": "Style de jeu de chaque equipe",
        "description": "Profil tactique (possession, bloc bas, contre-attaque) "
                       "tire des statistiques de match, et ce que le CHOC des "
                       "deux styles ajoute a la somme de leurs moyennes.",
        "source": "statistiques de match (possession, tirs, fautes, passes longues)",
        "grandeurs": ["corners", "cartons_jaunes", "buts"],
    },
    {
        "numero": 2, "cle": "forme_et_rang", "poids": "forme",
        "libelle": "Forme recente et rang de l'adversaire",
        "description": "Ecart entre les cinq derniers matchs et l'ensemble de "
                       "l'historique. Le rang est affiche mais ne corrige rien : "
                       "il redit le niveau, que le modele connait deja.",
        "source": "historique des deux equipes + classement",
        "grandeurs": ["buts"],
    },
    {
        "numero": 3, "cle": "systeme_et_effectif", "poids": "effectif",
        "libelle": "Systeme de jeu et effectif disponible",
        "description": "Blessures et suspensions, ponderees par poste : une "
                       "absence en attaque retire des buts a son equipe, une "
                       "absence en defense en donne a l'adversaire. Le systeme "
                       "vient de la composition annoncee, sinon des derniers "
                       "matchs joues.",
        "source": "sportsgambler (blessures, suspensions, postes) + "
                  "compositions Flashscore",
        "grandeurs": ["buts"],
    },
    {
        "numero": 4, "cle": "styles_similaires", "poids": "similarite",
        "libelle": "Confrontations entre equipes de style similaire",
        "description": "Chaque match d'historique est pondere par la "
                       "ressemblance entre l'adversaire de ce jour-la et celui "
                       "d'aujourd'hui. Agit AVANT la moyenne, pas sur son "
                       "resultat.",
        "source": "forces d'attaque et de defense de la competition",
        "grandeurs": [],
    },
    {
        "numero": 5, "cle": "confrontations_directes", "poids": "confrontations",
        "libelle": "Confrontations directes (head-to-head)",
        "description": "Buts, corners et cartons des dernieres rencontres entre "
                       "ces deux equipes, melanges au nombre attendu selon leur "
                       "nombre.",
        "source": "historique des rencontres + leurs fiches statistiques",
        "grandeurs": ["buts"],
    },
    {
        "numero": 6, "cle": "enjeu_competition", "poids": "enjeu",
        "libelle": "Contexte de la competition et enjeu",
        "description": "Phase de la competition (championnat, match couperet) et "
                       "situation de chaque equipe au classement.",
        "source": "nom de la phase + classement",
        "grandeurs": ["buts", "cartons_jaunes"],
    },
    {
        "numero": 7, "cle": "domicile_exterieur", "poids": None,
        "libelle": "Facteur domicile / exterieur",
        "description": "Deja applique par le modele, qui normalise chaque cote "
                       "par la moyenne correspondante de la competition. "
                       "L'avantage propre a chaque equipe a ete mesure puis "
                       "desactive (Params.home_edge).",
        "source": "historique + moyennes de la competition",
        "grandeurs": [],
    },
    {
        "numero": 8, "cle": "fatigue_calendrier", "poids": "fatigue",
        "libelle": "Fatigue et calendrier",
        "description": "Jours de repos, densite du calendrier recent, et "
                       "prochaine echeance -- que seul le classement publie.",
        "source": "dates des matchs joues + prochaine journee",
        "grandeurs": ["buts"],
    },
    {
        "numero": 9, "cle": "motivation", "poids": "motivation",
        "libelle": "Motivation et enjeux extra-sportifs",
        "description": "Tension mesuree plutot que supposee : cartons des "
                       "confrontations passees compares a la competition, ecart "
                       "de rang, revanche. Aucune liste de derbys ecrite a la "
                       "main.",
        "source": "confrontations directes + classement",
        "grandeurs": ["cartons_jaunes"],
    },
    {
        "numero": 10, "cle": "meteo", "poids": "meteo",
        "libelle": "Meteo et etat du terrain",
        "description": "Pluie, vent et temperature a l'heure du coup d'envoi, "
                       "sur la ville du stade.",
        "source": "Open-Meteo (gratuit, sans cle)",
        "grandeurs": ["buts", "corners", "cartons_jaunes"],
    },
    {
        "numero": 11, "cle": "statistiques_avancees", "poids": "xg",
        "libelle": "Statistiques avancees (xG, xGA)",
        "description": "Les buts attendus passent par le meme modele que les "
                       "buts -- meme reference, memes forces -- et les deux "
                       "estimations sont melangees.",
        "source": "xG de chaque match de l'historique",
        "grandeurs": ["buts"],
    },
    {
        "numero": 12, "cle": "arbitre", "poids": "arbitre",
        "libelle": "Discipline de l'arbitre",
        "description": "Cartons par match de l'arbitre designe, reconstitues "
                       "dans les matchs des deux equipes. L'arbitre n'est "
                       "souvent publie que le jour du match.",
        "source": "designation + fiches des matchs arbitres",
        "grandeurs": ["cartons_jaunes"],
    },
    {
        "numero": 13, "cle": "cotes_marche", "poids": None,
        "libelle": "Cotes du marche et mouvements de lignes",
        "description": "Ecart au consensus du marche, valeur esperee de chaque "
                       "pari aux meilleures cotes offertes, et derive entre "
                       "deux releves. Ne corrige rien : s'aligner sur le marche "
                       "reviendrait a le recopier en croyant le prevoir.",
        "source": "betexplorer.com (une trentaine d'operateurs), memes "
                  "identifiants de match que Flashscore",
        "grandeurs": [],
    },
    {
        "numero": 14, "cle": "taille_echantillon", "poids": None,
        "libelle": "Taille de l'echantillon statistique",
        "description": "Score de confiance par grandeur, tire de l'effectif "
                       "efficace et de la part des criteres renseignes. Qualifie "
                       "la prevision au lieu de la deplacer.",
        "source": "tout ce qui precede",
        "grandeurs": [],
    },
]


class Critere(NamedTuple):
    """Un critere renseigne : ce qu'il a vu, ce qu'il en conclut, ce qu'il fait.

    `effet` est un dictionnaire `grandeur -> (facteur domicile, facteur
    exterieur)`. Un critere qui ne corrige rien rend un dictionnaire vide -- ce
    qui n'est pas la meme chose qu'un critere indisponible, et l'affichage
    distingue les deux.
    """

    numero: int
    cle: str
    libelle: str
    disponible: bool | str
    source: str
    valeur: dict[str, Any]
    resume: str
    effet: dict[str, tuple[float, float]]
    echantillon: dict[str, Any] = {}

    def as_dict(self) -> dict[str, Any]:
        data = self._asdict()
        data["effet"] = {
            key: {"domicile": round(pair[0], 4), "exterieur": round(pair[1], 4)}
            for key, pair in self.effet.items()
        }
        return data


def _absent(numero: int, cle: str, libelle: str, source: str, raison: str) -> Critere:
    """Critere qu'on a cherche a renseigner et qui n'a pas pu l'etre.

    Le distinguer d'un critere neutre n'est pas une coquetterie : « l'arbitre
    n'est pas encore designe » et « l'arbitre distribue autant de cartons que la
    moyenne » menent au meme multiplicateur et ne disent pas la meme chose au
    lecteur.
    """
    return Critere(numero, cle, libelle, False, source, {}, raison, {})


# ---------------------------------------------------------------------------
# Outils communs
# ---------------------------------------------------------------------------

def _both(key: str, factor: float) -> dict[str, tuple[float, float]]:
    """Correction identique des deux cotes : elle porte sur le match, pas sur
    une equipe (meteo, enjeu, arbitre)."""
    return {key: (factor, factor)}


def _lisse(value: float, sample: float, target: float = 1.0, k: float = LISSAGE) -> float:
    """Ramene un rapport vers `target` selon la taille de l'echantillon.

    Meme estimateur que `predict._shrink`, repris ici plutot qu'importe : les
    deux modules doivent pouvoir evoluer separement, et la formule tient en une
    ligne.
    """
    if sample <= 0:
        return target
    return (sample * value + k * target) / (sample + k)


def _borne(factor: float, ampleur: float) -> float:
    """Contient un facteur dans [1 - ampleur, 1 + ampleur]."""
    return max(1.0 - ampleur, min(1.0 + ampleur, factor))


def _moyenne(values: Sequence[float]) -> float | None:
    usable = [v for v in values if v is not None]
    return sum(usable) / len(usable) if usable else None


def _stat_moyenne(entries: list[dict[str, Any]], field: str, sense: str = "pour") -> float | None:
    """Moyenne par match d'une statistique, du point de vue de l'equipe suivie."""
    values = [
        (entry.get("stats") or {}).get(sense, {}).get(field)
        for entry in entries
    ]
    return _moyenne([v for v in values if v is not None])


def _jours(depuis: str, jusqu_a: str) -> float | None:
    """Ecart en jours entre deux horodatages ISO, ou None si l'un manque."""
    if not depuis or not jusqu_a:
        return None
    try:
        start = datetime.fromisoformat(depuis)
        end = datetime.fromisoformat(jusqu_a)
    except (TypeError, ValueError):
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return (end - start).total_seconds() / 86400.0


def _officiels(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Matchs officiels : un amical de pre-saison ne dit rien du contexte."""
    return [e for e in entries if not e.get("amical")]


# ---------------------------------------------------------------------------
# Critere 1 : style de jeu
# ---------------------------------------------------------------------------

# Seuils de possession qui separent les trois familles de profils. Ils ne sont
# pas arbitraires : 47 % et 53 % encadrent la moitie centrale des equipes de
# championnat, et une equipe qui sort de cette bande le fait par choix tactique
# et non par accident d'echantillon.
POSSESSION_BASSE = 47.0
POSSESSION_HAUTE = 53.0


def _profil(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Profil tactique d'une equipe, lu dans ses statistiques de match.

    Toutes les grandeurs sont des moyennes par match du point de vue de
    l'equipe. Elles ne sont PAS normalisees par la competition : le style est
    une maniere de jouer, pas un niveau, et deux equipes de deuxieme division
    peuvent avoir le meme style que deux equipes de premiere.
    """
    possession = _stat_moyenne(entries, "possession")
    tirs = _stat_moyenne(entries, "tirs_total")
    cadres = _stat_moyenne(entries, "tirs_cadres")
    corners = _stat_moyenne(entries, "corners")
    fautes = _stat_moyenne(entries, "fautes")
    longues = _stat_moyenne(entries, "passes_longues")
    surface = _stat_moyenne(entries, "touches_surface")
    duels = _stat_moyenne(entries, "duels")
    xg = _stat_moyenne(entries, "xg")

    profil = "indetermine"
    if possession is not None:
        if possession >= POSSESSION_HAUTE:
            profil = "possession"
        elif possession <= POSSESSION_BASSE:
            # Un bloc bas qui tire peu subit ; un bloc bas qui tire autant que
            # les autres attaque en transition. Les deux ont peu le ballon et
            # n'appellent pas les memes marches.
            profil = "contre-attaque" if (tirs or 0) >= 11 else "bloc bas"
        else:
            profil = "equilibre"

    return {
        "profil": profil,
        "possession": possession,
        "tirs": tirs,
        "tirs_cadres": cadres,
        "corners": corners,
        "fautes": fautes,
        "passes_longues": longues,
        "touches_surface": surface,
        "duels": duels,
        "xg": xg,
        "matchs": sum(1 for e in entries if e.get("stats")),
    }


def _critere_style(
    home: dict[str, Any], away: dict[str, Any], poids: float
) -> Critere:
    """Ce que le CHOC des deux styles ajoute a la somme de leurs moyennes.

    Le niveau de chaque equipe est deja dans le modele : ses corners moyens sont
    ses corners moyens. Ce que le modele ignore, c'est l'INTERACTION -- une
    equipe de possession face a un bloc bas obtient plus de corners que la
    moyenne des deux ne le laisse croire, parce que l'adversaire se replie et
    degage. C'est cet ecart-la, et lui seul, que ce critere corrige.
    """
    libelle = "Style de jeu de chaque equipe"
    source = "statistiques de match (Flashscore)"
    if home["possession"] is None or away["possession"] is None:
        return _absent(
            1, "style_de_jeu", libelle, source,
            "Possession non publiee pour cette competition : le profil tactique "
            "ne peut pas etre etabli.",
        )

    ecart = (home["possession"] - away["possession"]) / 100.0
    intensite = _moyenne([home["fautes"], away["fautes"]])

    effet: dict[str, tuple[float, float]] = {}
    if poids > 0:
        # Corners : le cote qui domine le ballon en obtient davantage, l'autre
        # moins. L'effet est antisymetrique -- ce qu'un cote gagne, l'autre le
        # perd -- pour ne pas gonfler le total, qui lui est deja bien estime.
        gain = _borne(1.0 + 0.45 * poids * ecart, 0.12)
        effet["corners"] = (gain, _borne(2.0 - gain, 0.12))
        # Cartons : deux equipes qui font beaucoup de fautes en produisent plus
        # que la somme de leurs moyennes, parce que les fautes s'appellent.
        if intensite is not None and intensite > 13.0:
            facteur = _borne(1.0 + 0.04 * poids * (intensite - 13.0), 0.15)
            effet["cartons_jaunes"] = (facteur, facteur)
        # Deux blocs bas : le match se ferme, et aucune des deux moyennes prise
        # separement ne le dit.
        if home["profil"] in ("bloc bas",) and away["profil"] in ("bloc bas",):
            ferme = _borne(1.0 - 0.05 * poids, 0.08)
            effet["buts"] = (ferme, ferme)

    return Critere(
        numero=1,
        cle="style_de_jeu",
        libelle=libelle,
        disponible=True,
        source=source,
        valeur={"domicile": home, "exterieur": away},
        resume="%s (%.0f %% de possession) contre %s (%.0f %%)."
        % (home["profil"], home["possession"], away["profil"], away["possession"]),
        effet=effet,
        echantillon={"domicile": home["matchs"], "exterieur": away["matchs"]},
    )


# ---------------------------------------------------------------------------
# Critere 2 : forme recente et rang de l'adversaire
# ---------------------------------------------------------------------------

_POINTS = {"V": 3.0, "N": 1.0, "D": 0.0}


def _ordinal(rang: int | None) -> str:
    """Rang en francais : 1er, 2e, 3e..."""
    if not rang:
        return "?"
    return "1er" if rang == 1 else "%de" % rang


def _points_par_match(entries: list[dict[str, Any]]) -> float | None:
    values = [_POINTS[e["resultat"]] for e in entries if e.get("resultat") in _POINTS]
    return sum(values) / len(values) if values else None


def _rang(table: dict[str, Any], team: str) -> dict[str, Any] | None:
    for row in (table or {}).get("lignes") or []:
        if row["equipe"] == team:
            return row
    return None


def _critere_forme(
    home_entries: list[dict[str, Any]],
    away_entries: list[dict[str, Any]],
    table: dict[str, Any],
    teams: tuple[str, str],
    poids: float,
) -> Critere:
    """Ce que la forme des dernieres semaines ajoute a la moyenne d'ensemble.

    Attention au double comptage : le NIVEAU d'une equipe est deja dans le
    modele, qui moyenne tout son historique, et son rang au classement n'est
    qu'une autre facon de dire ce meme niveau. Corriger sur le rang reviendrait
    a compter deux fois la meme information.

    Ce que le modele ne voit pas, c'est l'ECART entre les cinq derniers matchs
    et l'ensemble : une equipe a 2,4 points par match sur la saison mais 0,6 sur
    le dernier mois n'est pas l'equipe que sa moyenne decrit. C'est cet ecart
    seul qui corrige, et le rang n'est affiche que comme reperage.
    """
    libelle = "Forme recente et rang de l'adversaire"
    source = "historique des deux equipes" + (" + classement" if table else "")

    valeur: dict[str, Any] = {}
    ecarts: list[float | None] = []
    for side, entries, team in (
        ("domicile", home_entries, teams[0]),
        ("exterieur", away_entries, teams[1]),
    ):
        recent = _points_par_match(entries[:FENETRE_RECENTE])
        ensemble = _points_par_match(entries)
        row = _rang(table, team)
        valeur[side] = {
            "points_par_match_recent": recent,
            "points_par_match_ensemble": ensemble,
            "matchs_recents": min(FENETRE_RECENTE, len(entries)),
            "serie": "".join(e.get("resultat", "?") for e in entries[:FENETRE_RECENTE]),
            "rang": (row or {}).get("rang"),
            "equipes_classees": (table or {}).get("equipes"),
            "points": (row or {}).get("points"),
        }
        ecarts.append(
            None if recent is None or ensemble is None else recent - ensemble
        )

    if ecarts[0] is None or ecarts[1] is None:
        return _absent(
            2, "forme_et_rang", libelle, source,
            "Historique trop court pour opposer la forme recente a la forme "
            "d'ensemble.",
        )

    effet: dict[str, tuple[float, float]] = {}
    if poids > 0:
        # Un point par match d'ecart sur cinq matchs est un ecart considerable ;
        # il ne doit pas valoir plus de quelques pour cent sur le nombre de buts
        # attendu, car cinq matchs restent cinq matchs.
        facteurs = tuple(
            _borne(1.0 + 0.06 * poids * (ecart / 1.0), 0.10) for ecart in ecarts
        )
        effet["buts"] = facteurs

    rangs = [valeur["domicile"]["rang"], valeur["exterieur"]["rang"]]
    situation = (
        "%s contre %s au classement. " % (_ordinal(rangs[0]), _ordinal(rangs[1]))
        if all(rangs)
        else ""
    )
    return Critere(
        numero=2,
        cle="forme_et_rang",
        libelle=libelle,
        disponible=True,
        source=source,
        valeur=valeur,
        resume=(
            situation
            + "Forme des %d derniers matchs : %s (%+.2f point par match) contre "
            "%s (%+.2f)."
            % (
                FENETRE_RECENTE,
                valeur["domicile"]["serie"] or "-",
                ecarts[0],
                valeur["exterieur"]["serie"] or "-",
                ecarts[1],
            )
        ),
        effet=effet,
        echantillon={
            "domicile": len(home_entries),
            "exterieur": len(away_entries),
            "classement": bool(table),
        },
    )


# ---------------------------------------------------------------------------
# Critere 3 : systeme de jeu et effectif disponible
# ---------------------------------------------------------------------------

# Ce qu'une absence retire, selon le poste. Un attaquant absent pese sur ce que
# son equipe marque ; un defenseur ou un gardien absent pese sur ce qu'elle
# encaisse. Un milieu fait les deux, a moitie -- il participe a la creation
# comme a la couverture.
POIDS_POSTE = {
    "attaquant": {"offensif": 1.0, "defensif": 0.0},
    "milieu": {"offensif": 0.5, "defensif": 0.5},
    "defenseur": {"offensif": 0.0, "defensif": 1.0},
    "gardien": {"offensif": 0.0, "defensif": 1.0},
}

# Un joueur douteux joue une fois sur deux : le compter comme absent
# surestimerait l'affaiblissement de son equipe, l'ignorer le sous-estimerait.
POIDS_MOTIF = {"blesse": 1.0, "suspendu": 1.0, "incertain": 0.5, "indisponible": 0.75}

# Plafond du nombre d'absents pris en compte, par cote. Au-dela, la liste
# melange les titulaires du week-end et les blesses de longue duree, que la
# source ne distingue pas.
ABSENTS_MAX = 3.0


def _poids_absences(absents: list[dict[str, str]]) -> dict[str, float]:
    """Ce que les absences retirent, en equivalents-titulaires par secteur."""
    total = {"offensif": 0.0, "defensif": 0.0}
    for absent in absents:
        motif = POIDS_MOTIF.get(absent.get("motif", ""), 0.75)
        poste = POIDS_POSTE.get(absent.get("poste", ""))
        if poste is None:
            # Poste inconnu : reparti a parts egales plutot qu'ignore. Ne pas
            # savoir ou joue un absent n'est pas une raison de le compter pour
            # rien.
            poste = {"offensif": 0.5, "defensif": 0.5}
        for secteur, part in poste.items():
            total[secteur] += motif * part
    return {
        secteur: min(ABSENTS_MAX, valeur) for secteur, valeur in total.items()
    }


# Lignes du terrain, deduites de la place dans le dispositif. Le decoupage
# exact depend du systeme ; celui-ci suffit a presenter un onze de facon
# lisible, ce qui est tout ce qu'on lui demande.
LIGNES = (
    (1, 1, "gardien"),
    (2, 5, "defense"),
    (6, 8, "milieu"),
    (9, 11, "attaque"),
)


def _ligne(place: int | None) -> str:
    if place is None:
        return "indetermine"
    for debut, fin, nom in LIGNES:
        if debut <= place <= fin:
            return nom
    return "attaque"


def _nom_et_initiale(nom: str) -> tuple[str, str]:
    """(nom de famille, initiale du prenom) a partir d'une ecriture quelconque.

    Les deux sources n'ecrivent pas les joueurs pareil : Flashscore rend
    « Cunha M. » (nom puis initiale), la source des absences « Matheus Cunha »
    (prenom puis nom). On ramene les deux a la meme paire.

    Les particules restent au nom : « de Ligt M. » et « Matthijs de Ligt »
    donnent tous deux ("de ligt", "m"), et les separer casserait le
    rapprochement. Un nom d'un seul mot ("Rodri") n'a pas d'initiale, et le
    rapprochement se fera alors sur le seul nom.
    """
    mots = _sans_accents(nom).replace(".", " ").split()
    if not mots:
        return "", ""
    if len(mots) > 1 and len(mots[-1]) == 1:
        return " ".join(mots[:-1]), mots[-1]      # "Cunha M."
    if len(mots) > 1:
        return " ".join(mots[1:]), mots[0][0]     # "Matheus Cunha"
    return mots[0], ""


def _meme_joueur(gauche: str, droite: str) -> bool:
    """Deux ecritures designent-elles le meme joueur ?

    Le nom de famille doit correspondre -- en entier, ou par son dernier mot,
    ce qui rattrape « Andrey Santos » ecrit sans initiale d'un cote. Mais le nom
    seul ne suffit pas : deux freres, ou deux homonymes d'un meme effectif, s'y
    confondraient, et une seule blessure les ecarterait tous les deux du onze.
    Quand les deux ecritures portent une initiale de prenom, elle doit donc
    concorder aussi.

    En cas de doute -- surnom d'un cote, etat civil de l'autre -- on ne
    rapproche pas. Un joueur maintenu a tort dans un onze probable se voit ;
    un joueur ecarte a tort n'apparait nulle part.
    """
    nom_a, initiale_a = _nom_et_initiale(gauche)
    nom_b, initiale_b = _nom_et_initiale(droite)
    if not nom_a or not nom_b:
        return False

    accorde = nom_a == nom_b
    if not accorde:
        queue_a, queue_b = nom_a.split()[-1], nom_b.split()[-1]
        accorde = len(queue_a) >= 4 and queue_a == queue_b
    if not accorde:
        return False
    if initiale_a and initiale_b:
        return initiale_a == initiale_b
    return True


def _equipe_probable(
    historique: dict[str, Any],
    absents: list[dict[str, str]],
    systeme_annonce: str = "",
) -> dict[str, Any]:
    """Composition la plus probable : les titulaires recents, moins les absents.

    Aucune source gratuite ne publie de composition probable. Mais la
    composition REELLE des matchs precedents en dit l'essentiel : un joueur qui
    a commence les cinq derniers commencera vraisemblablement le sixieme. Le
    onze est donc reconstitue place par place -- pour chacune des onze places du
    dispositif, le joueur qui l'a le plus souvent occupee et qui est disponible.

    C'est une deduction, pas une annonce, et la fiche le dit : chaque joueur
    porte le nombre de matchs ou il a commence, et les absents ecartes sont
    listes a part. Un onze bati sur deux matchs et un onze bati sur cinq ne se
    valent pas, et rien ne doit laisser croire le contraire.
    """
    joueurs = historique.get("joueurs") or {}
    if not joueurs:
        return {}

    def _est_absent(nom: str) -> dict[str, str] | None:
        return next(
            (a for a in absents if _meme_joueur(nom, a.get("joueur", ""))), None
        )

    disponibles: list[tuple[str, dict[str, Any]]] = []
    ecartes: list[dict[str, Any]] = []
    for nom, fiche in joueurs.items():
        absent = _est_absent(nom)
        # Un joueur douteux reste dans le onze : il joue une fois sur deux, et
        # l'ecarter serait aussi faux que l'ignorer. Il est signale.
        if absent and absent.get("motif") != "incertain":
            ecartes.append(dict(fiche, joueur=nom, motif=absent.get("motif", "")))
            continue
        disponibles.append((nom, dict(fiche, incertain=bool(absent))))

    # Place par place : celui qui l'occupe le plus souvent, le plus titularise
    # departageant les ex aequo.
    onze: list[dict[str, Any]] = []
    pris: set[str] = set()
    for place in range(1, 12):
        candidats = [
            (nom, f) for nom, f in disponibles
            if f.get("place") == place and nom not in pris
        ]
        if not candidats:
            continue
        nom, fiche = max(candidats, key=lambda c: c[1]["titularisations"])
        pris.add(nom)
        onze.append(dict(fiche, joueur=nom, place=place, ligne=_ligne(place)))

    # Places restees vides (blessure du titulaire, place jamais occupee dans
    # l'echantillon) : completees par les plus titularises encore disponibles.
    if len(onze) < 11:
        reste = sorted(
            ((n, f) for n, f in disponibles if n not in pris),
            key=lambda c: -c[1]["titularisations"],
        )
        for nom, fiche in reste[: 11 - len(onze)]:
            onze.append(
                dict(fiche, joueur=nom, place=fiche.get("place"),
                     ligne=_ligne(fiche.get("place")), remplace_un_absent=True)
            )

    onze.sort(key=lambda j: (j["place"] is None, j["place"] or 99))
    return {
        "systeme": systeme_annonce or historique.get("systeme", ""),
        "systeme_annonce": bool(systeme_annonce),
        "matchs_couverts": historique.get("matchs_couverts", 0),
        "onze": onze,
        "ecartes": sorted(ecartes, key=lambda j: -j["titularisations"]),
    }


def _composition(
    annoncee: dict[str, Any],
    historique: dict[str, Any],
    absents: list[dict[str, str]],
) -> dict[str, Any]:
    """Composition d'une equipe, sous une forme toujours complete.

    La composition publiee par la source l'emporte : a une heure du coup
    d'envoi, elle fait foi, et une deduction n'a plus lieu d'etre. A defaut, le
    onze est reconstitue des titularisations recentes. Et si ni l'une ni
    l'autre n'existe, la forme est rendue quand meme, vide -- un appelant ne
    doit pas avoir a se garder de champs manquants pour afficher « rien ».
    """
    vide = {
        "systeme": "",
        "annoncee": False,
        "matchs_couverts": None,
        "onze": [],
        "ecartes": [],
    }
    if annoncee.get("onze"):
        return dict(
            vide,
            systeme=annoncee.get("systeme", ""),
            annoncee=True,
            onze=[
                dict(joueur, ligne=_ligne(joueur.get("place")))
                for joueur in annoncee["onze"]
            ],
        )
    if historique:
        return dict(vide, **_equipe_probable(historique, absents))
    return vide


def _critere_effectif(
    compositions: dict[str, Any],
    historiques: dict[str, Any],
    absences: dict[str, Any],
    poids: float,
) -> Critere:
    """Systeme de jeu et joueurs absents.

    Trois sources se completent, et aucune ne suffit :

      - **Flashscore** publie la composition -- donc le systeme reellement
        aligne et les absents -- environ une heure avant le coup d'envoi. Une
        prevision emise la veille ne peut pas les connaitre : c'est le cas
        NORMAL, pas une panne.
      - **Sportsgambler** publie blessures et suspensions en continu, avec le
        motif et le poste. C'est la seule des trois disponible plusieurs jours
        a l'avance, donc la seule qui serve vraiment a une prevision.
      - Les **derniers matchs joues** donnent qui a reellement commence, et a
        quelle place. C'est de la que sort la **composition probable** : aucune
        source gratuite n'en publie, mais un joueur qui a commence les cinq
        derniers matchs commencera vraisemblablement le sixieme.

    Aucune correction n'est appliquee sur le seul systeme : un 4-4-2 n'est pas
    plus offensif qu'un 4-3-3, et ce que le dispositif produit est deja dans les
    comptages du modele. Ce sont les ABSENCES qui deplacent quelque chose, et
    elles sont ponderees par poste : une absence en attaque retire des buts a
    son equipe, une absence en defense en donne a l'adversaire.
    """
    libelle = "Systeme de jeu et effectif disponible"
    annoncees = bool(compositions)
    rapprochees = bool(absences) and all(
        (absences.get(cote) or {}).get("rapprochee") for cote in ("domicile", "exterieur")
    )

    valeur: dict[str, Any] = {}
    for cote in ("domicile", "exterieur"):
        liste = (absences.get(cote) or {}).get("absents") or []
        annoncee = compositions.get(cote) or {}
        historique = historiques.get(cote) or {}
        valeur[cote] = {
            "systeme_annonce": annoncee.get("systeme", ""),
            "systeme_habituel": historique.get("systeme", ""),
            "absents": liste,
            "absents_composition": annoncee.get("absents", []),
            "manque": _poids_absences(liste) if liste else {"offensif": 0.0, "defensif": 0.0},
            # La composition ANNONCEE quand elle existe -- elle fait foi -- et
            # sinon celle qu'on deduit des titularisations recentes. Les deux
            # sont distinguees par `annoncee`, parce qu'une annonce et une
            # deduction ne s'engagent pas de la meme facon.
            # La composition ANNONCEE quand elle existe -- elle fait foi -- et
            # sinon celle qu'on deduit des titularisations recentes.
            #
            # La forme est TOUJOURS complete, meme quand il n'y a rien a
            # montrer : un onze vide et un onze absent se lisent pareil pour un
            # lecteur, mais pas pour le code qui l'affiche, et une reponse a
            # moitie formee obligerait chaque appelant a se garder de ses
            # propres champs manquants.
            "composition_probable": _composition(annoncee, historique, liste),
        }

    if not annoncees and not rapprochees:
        habituels = " ; ".join(
            "%s : %s" % (cote, valeur[cote]["systeme_habituel"] or "inconnu")
            for cote in ("domicile", "exterieur")
        )
        return Critere(
            numero=3,
            cle="systeme_et_effectif",
            libelle=libelle,
            disponible="partiel",
            source="derniers matchs joues (Flashscore)",
            valeur=valeur,
            resume=(
                "Ni composition publiee (elle l'est environ une heure avant le "
                "coup d'envoi) ni liste d'absents pour cette competition. "
                "Systemes recents -- %s." % habituels
            ),
            effet={},
            echantillon={"compositions": False, "absences": False},
        )

    effet: dict[str, tuple[float, float]] = {}
    if poids > 0 and rapprochees:
        manque = {cote: valeur[cote]["manque"] for cote in ("domicile", "exterieur")}
        # Deux effets de sens contraire, appliques ensemble : ce qu'une equipe
        # perd en attaque diminue SES buts, ce qu'elle perd en defense augmente
        # ceux de l'adversaire.
        facteurs = []
        for cote, autre in (("domicile", "exterieur"), ("exterieur", "domicile")):
            facteur = 1.0 - 0.03 * poids * manque[cote]["offensif"]
            facteur *= 1.0 + 0.02 * poids * manque[autre]["defensif"]
            facteurs.append(_borne(facteur, 0.10))
        if any(abs(f - 1.0) > 1e-9 for f in facteurs):
            effet["buts"] = tuple(facteurs)

    sources = []
    if rapprochees:
        sources.append("blessures et suspensions (sportsgambler)")
    if annoncees:
        sources.append("compositions annoncees (Flashscore)")

    comptes = {
        cote: len(valeur[cote]["absents"]) or len(valeur[cote]["absents_composition"])
        for cote in ("domicile", "exterieur")
    }

    def _systeme(cote: str) -> str:
        """Systeme a afficher : celui annonce, sinon le plus employe recemment."""
        return (
            valeur[cote]["systeme_annonce"]
            or valeur[cote]["systeme_habituel"]
            or "systeme inconnu"
        )
    return Critere(
        numero=3,
        cle="systeme_et_effectif",
        libelle=libelle,
        disponible=True if rapprochees else "partiel",
        source=" + ".join(sources),
        valeur=valeur,
        resume="%s contre %s ; %d et %d absent(s)."
        % (
            _systeme("domicile"), _systeme("exterieur"),
            comptes["domicile"], comptes["exterieur"],
        ),
        effet=effet,
        echantillon={"compositions": annoncees, "absences": rapprochees},
    )


# ---------------------------------------------------------------------------
# Critere 4 : confrontations avec des equipes de style comparable
# ---------------------------------------------------------------------------

# Grandeurs qui composent le vecteur de profil d'une equipe. Elles viennent des
# forces deja calculees par `league_baseline` : ce critere ne coute donc aucune
# requete supplementaire.
PROFIL_CHAMPS = ("buts", "corners", "tirs_cadres", "cartons_jaunes")

# Largeur de la similarite. A distance SIGMA du profil vise, un match pese
# encore 60 % ; a deux SIGMA, 14 %. Assez large pour qu'un historique de dix
# matchs garde du poids partout, assez etroit pour que la ponderation dise
# quelque chose.
SIGMA = 0.45


def _vecteur(baseline: dict[str, Any] | None, team: str) -> dict[str, float]:
    """Profil d'une equipe : ses forces d'attaque et de defense par grandeur.

    Un vecteur vide signifie « equipe inconnue de la reference » -- un club de
    coupe venu d'une autre division, par exemple. La similarite le traite alors
    comme un adversaire moyen plutot que de l'ecarter : ne pas savoir n'est pas
    une raison d'effacer un match.
    """
    if not baseline:
        return {}
    vector: dict[str, float] = {}
    rating = (baseline.get("forces") or {}).get(team)
    if rating:
        vector["buts_attaque"] = float(rating.get("attaque", 1.0))
        vector["buts_defense"] = float(rating.get("defense", 1.0))
    detail = (baseline.get("forces_stats") or {}).get(team) or {}
    for field in PROFIL_CHAMPS:
        if field == "buts" or field not in detail:
            continue
        vector["%s_attaque" % field] = float(detail[field].get("attaque", 1.0))
        vector["%s_defense" % field] = float(detail[field].get("defense", 1.0))
    return vector


def _similarite(a: dict[str, float], b: dict[str, float]) -> float:
    """Proximite de deux profils, entre 0 et 1. 1 = profils identiques.

    Distance euclidienne sur les seules composantes connues des deux cotes,
    ramenee a un poids par une gaussienne. Sans composante commune, on rend 1 :
    un adversaire dont on ne sait rien ne doit ni etre privilegie ni etre
    ecarte, seulement compte comme les autres.
    """
    commun = [key for key in a if key in b]
    if not commun:
        return 1.0
    distance = math.sqrt(sum((a[key] - b[key]) ** 2 for key in commun) / len(commun))
    return math.exp(-((distance / SIGMA) ** 2) / 2)


def _critere_similarite(
    home_entries: list[dict[str, Any]],
    away_entries: list[dict[str, Any]],
    baseline: dict[str, Any] | None,
    teams: tuple[str, str],
    poids: float,
) -> tuple[Critere, dict[str, float]]:
    """Repondere l'historique selon la ressemblance des adversaires affrontes.

    Une equipe qui vient de jouer trois blocs bas et qui affronte une equipe de
    possession a une moyenne de corners qui ne dit rien du match a venir. Plutot
    que d'ajouter une correction par-dessus le modele, ce critere agit LA OU LE
    BIAIS NAIT : dans la moyenne elle-meme. Chaque match d'historique recoit un
    poids proportionnel a la ressemblance entre l'adversaire de ce jour-la et
    l'adversaire d'aujourd'hui.

    C'est la meme mecanique que la ponderation par anciennete de Dixon et Coles,
    appliquee a une autre dimension : la ou elle demande « ce match est-il
    recent ? », celle-ci demande « ce match ressemble-t-il a celui qui vient ? ».

    Rend le critere ET la table des poids, que `predict` applique a chaque
    entree d'historique. A poids nul, tous les poids valent 1 et l'historique
    est moyenne comme avant.
    """
    libelle = "Confrontations entre equipes de style similaire"
    source = "forces de la competition"
    if not baseline:
        return (
            _absent(
                4, "styles_similaires", libelle, source,
                "Pas de reference de competition : les profils des adversaires "
                "passes ne peuvent pas etre etablis.",
            ),
            {},
        )

    cibles = {"domicile": _vecteur(baseline, teams[1]),
              "exterieur": _vecteur(baseline, teams[0])}
    poids_entrees: dict[str, float] = {}
    detail: dict[str, Any] = {}

    for side, entries in (("domicile", home_entries), ("exterieur", away_entries)):
        cible = cibles[side]
        proches: list[dict[str, Any]] = []
        for entry in entries:
            score = _similarite(_vecteur(baseline, entry.get("adversaire", "")), cible)
            # A poids nul le facteur vaut 1 partout : le critere est calcule et
            # affiche, mais l'historique est moyenne comme avant.
            poids_entrees[entry.get("match_id", "")] = 1.0 - poids * (1.0 - score)
            if score >= 0.7:
                proches.append(entry)
        detail[side] = {
            "adversaire_vise": teams[1] if side == "domicile" else teams[0],
            "profil_connu": bool(cible),
            "matchs_comparables": len(proches),
            "buts_marques": _moyenne([e.get("buts_pour") for e in proches]),
            "buts_encaisses": _moyenne([e.get("buts_contre") for e in proches]),
            "corners": _stat_moyenne(proches, "corners"),
            "cartons_jaunes": _stat_moyenne(proches, "cartons_jaunes"),
        }

    connus = detail["domicile"]["profil_connu"] and detail["exterieur"]["profil_connu"]
    if not connus:
        return (
            _absent(
                4, "styles_similaires", libelle, source,
                "L'une des deux equipes n'a pas de force etablie dans la "
                "reference : la ressemblance des adversaires ne peut pas etre "
                "mesuree.",
            ),
            {},
        )

    return (
        Critere(
            numero=4,
            cle="styles_similaires",
            libelle=libelle,
            disponible=True,
            source=source,
            valeur=detail,
            resume=(
                "%d et %d matchs d'historique contre un adversaire au profil "
                "comparable ; l'historique est repondere en consequence."
                % (
                    detail["domicile"]["matchs_comparables"],
                    detail["exterieur"]["matchs_comparables"],
                )
            ),
            # La correction ne passe pas par un multiplicateur : elle est deja
            # dans les poids rendus, appliques avant la moyenne.
            effet={},
            echantillon={
                "domicile": detail["domicile"]["matchs_comparables"],
                "exterieur": detail["exterieur"]["matchs_comparables"],
            },
        ),
        poids_entrees,
    )


# ---------------------------------------------------------------------------
# Critere 5 : confrontations directes
# ---------------------------------------------------------------------------

def _h2h_stats(meetings: list[dict[str, Any]], use_cache: bool) -> dict[str, Any]:
    """Statistiques detaillees des dernieres confrontations directes.

    Le flux des confrontations ne donne que les scores. Corners et cartons
    demandent la fiche de chaque rencontre -- une requete chacune, gardees
    trente jours puisqu'un match termine ne change plus. Le plafond limite ce
    que coute une prevision.
    """
    corners: list[float] = []
    cartons: list[float] = []
    couverts = 0
    for meeting in meetings[:H2H_STATS_MAX]:
        match_id = meeting.get("match_id") or ""
        if not match_id:
            continue
        try:
            stats = api_client.get_stats(
                {"provider": "flashscore", "match_id": match_id}, use_cache=use_cache
            )
        except api_client.ApiError:
            continue
        couverts += 1
        for field, sink in (("corners", corners), ("cartons_jaunes", cartons)):
            home = api_client.stat_number(stats["domicile"].get(field))
            away = api_client.stat_number(stats["exterieur"].get(field))
            if home is not None and away is not None:
                sink.append(home + away)
    return {
        "matchs_couverts": couverts,
        "corners_total": _moyenne(corners),
        "cartons_total": _moyenne(cartons),
    }


def _critere_confrontations(
    meetings: list[dict[str, Any]],
    lam_reference: tuple[float, float],
    detail: dict[str, Any],
    poids: float,
) -> Critere:
    """Ce que les rencontres passees entre ces deux equipes ajoutent.

    Le modele n'a pas de terme de confrontation directe, et c'est defendable :
    les effectifs changent, et cinq rencontres etalees sur trois ans en disent
    moins que dix matchs recents. Mais il reste des paires d'equipes dont les
    matchs sont systematiquement fermes ou systematiquement ouverts, et cette
    regularite-la ne se lit nulle part ailleurs.

    La correction est donc un melange, pondere par le nombre de rencontres :
    trois confrontations pesent 3/(3+LISSAGE), soit un tiers de ce que dirait un
    echantillon complet, et jamais davantage.
    """
    libelle = "Confrontations directes (head-to-head)"
    source = "historique des rencontres (Flashscore)"
    if not meetings:
        return _absent(
            5, "confrontations_directes", libelle, source,
            "Aucune rencontre passee entre ces deux equipes dans l'historique.",
        )

    toutes = len(meetings)
    meetings = meetings[:H2H_RECENTES]
    buts_domicile = _moyenne([m.get("buts_domicile") for m in meetings])
    buts_exterieur = _moyenne([m.get("buts_exterieur") for m in meetings])
    total_h2h = (buts_domicile or 0) + (buts_exterieur or 0)
    total_modele = lam_reference[0] + lam_reference[1]

    effet: dict[str, tuple[float, float]] = {}
    if poids > 0 and total_modele > 0 and buts_domicile is not None:
        part = len(meetings) / (len(meetings) + LISSAGE)
        facteur = _borne(
            1.0 + poids * part * (total_h2h / total_modele - 1.0), 0.10
        )
        effet["buts"] = (facteur, facteur)

    return Critere(
        numero=5,
        cle="confrontations_directes",
        libelle=libelle,
        disponible=True,
        source=source,
        valeur={
            "rencontres": len(meetings),
            "rencontres_connues": toutes,
            "buts_domicile": buts_domicile,
            "buts_exterieur": buts_exterieur,
            "buts_total": total_h2h,
            "corners_total": detail.get("corners_total"),
            "cartons_total": detail.get("cartons_total"),
            "statistiques_couvertes": detail.get("matchs_couverts", 0),
            "dernieres": meetings[:5],
        },
        resume="%d rencontre(s) retenue(s) sur %d, %.2f but(s) par match en "
        "moyenne (modele : %.2f)." % (len(meetings), toutes, total_h2h, total_modele),
        effet=effet,
        echantillon={"rencontres": len(meetings)},
    )


# ---------------------------------------------------------------------------
# Critere 6 : contexte de la competition et enjeu
# ---------------------------------------------------------------------------

# Phases a elimination directe, telles que le flux les suffixe. La casse et les
# accents sont neutralises avant comparaison.
_COUPERET = (
    "finale", "demi-finale", "quart de finale", "huitieme", "1/8", "1/4", "1/2",
    "barrage", "play-off", "playoff", "qualification", "tour preliminaire",
)


def _sans_accents(text: str) -> str:
    import unicodedata

    stripped = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in stripped if unicodedata.category(c) != "Mn").lower()


def _critere_enjeu(
    competition: str,
    table: dict[str, Any],
    teams: tuple[str, str],
    poids: float,
) -> Critere:
    """Nature de la rencontre et ce que chaque equipe y joue.

    Deux informations distinctes, et le flux les porte a deux endroits : le nom
    de la competition dit la PHASE (un huitieme de finale ne se joue pas comme
    une 12e journee), le classement dit l'ENJEU (une equipe a six points du
    maintien ne joue pas comme une equipe installee au milieu).

    L'effet retenu est celui que la litterature documente : un match couperet
    produit moins de buts et plus de cartons qu'un match de championnat, parce
    que le cout d'une erreur y est asymetrique.
    """
    libelle = "Contexte de la competition et enjeu"
    source = "nom de la phase" + (" + classement" if table else "")
    plat = _sans_accents(competition)
    couperet = any(marqueur in plat for marqueur in _COUPERET)
    phase = competition.split(" - ", 1)[1].strip() if " - " in competition else ""

    situations: dict[str, Any] = {}
    for side, team in (("domicile", teams[0]), ("exterieur", teams[1])):
        row = _rang(table, team)
        equipes = (table or {}).get("equipes") or 0
        journees_restantes = None
        if row and equipes and row.get("joues") is not None:
            # Un championnat aller-retour compte 2*(N-1) journees. C'est faux
            # pour les formats a phase finale, d'ou l'absence de conclusion
            # tiree de cette valeur au-dela de « debut » ou « fin » de saison.
            journees_restantes = max(0, 2 * (equipes - 1) - row["joues"])
        situations[side] = {
            "rang": (row or {}).get("rang"),
            "points": (row or {}).get("points"),
            "zone": (row or {}).get("zone", ""),
            "journees_restantes": journees_restantes,
        }

    effet: dict[str, tuple[float, float]] = {}
    if poids > 0 and couperet:
        ferme = _borne(1.0 - 0.04 * poids, 0.06)
        tendu = _borne(1.0 + 0.08 * poids, 0.15)
        effet["buts"] = (ferme, ferme)
        effet["cartons_jaunes"] = (tendu, tendu)

    return Critere(
        numero=6,
        cle="enjeu_competition",
        libelle=libelle,
        disponible=True,
        source=source,
        valeur={
            "competition": competition,
            "phase": phase,
            "couperet": couperet,
            "classement_disponible": bool(table),
            "domicile": situations["domicile"],
            "exterieur": situations["exterieur"],
        },
        resume=(
            "Match couperet (%s)." % (phase or "phase finale")
            if couperet
            else "Match de championnat%s."
            % (" (%s)" % phase if phase else "")
        ),
        effet=effet,
        echantillon={"classement": bool(table)},
    )


# ---------------------------------------------------------------------------
# Critere 7 : facteur domicile / exterieur
# ---------------------------------------------------------------------------

def _critere_domicile(
    home_entries: list[dict[str, Any]],
    away_entries: list[dict[str, Any]],
    baseline: dict[str, Any] | None,
) -> Critere:
    """Ce que chaque equipe produit selon le lieu -- deja dans le modele.

    Ce critere ne corrige rien, et c'est un resultat de mesure, pas un oubli :
    l'avantage du terrain de la COMPETITION est deja applique par le modele
    (`moyenne_domicile` et `moyenne_exterieur` normalisent separement les deux
    cotes), et l'avantage propre a chaque equipe a ete implemente, mesure sur
    109 matchs, puis laisse a zero -- il degradait la prevision, et d'autant
    plus qu'on lui donnait de poids (README, `Params.home_edge`).

    Ce qui reste utile est l'AFFICHAGE : voir qu'une equipe marque 2,1 buts chez
    elle et 0,7 dehors est une information, meme quand le modele a decide de ne
    pas s'en servir plus qu'il ne le fait deja.
    """
    valeur: dict[str, Any] = {}
    for side, entries, lieu in (
        ("domicile", home_entries, "domicile"),
        ("exterieur", away_entries, "exterieur"),
    ):
        chez_soi = [e for e in entries if e.get("lieu") == lieu]
        ailleurs = [e for e in entries if e.get("lieu") != lieu]
        valeur[side] = {
            "matchs_dans_ce_lieu": len(chez_soi),
            "buts_marques_dans_ce_lieu": _moyenne([e.get("buts_pour") for e in chez_soi]),
            "buts_encaisses_dans_ce_lieu": _moyenne([e.get("buts_contre") for e in chez_soi]),
            "buts_marques_ailleurs": _moyenne([e.get("buts_pour") for e in ailleurs]),
        }
    valeur["competition"] = {
        "moyenne_domicile": (baseline or {}).get("moyenne_domicile"),
        "moyenne_exterieur": (baseline or {}).get("moyenne_exterieur"),
    }
    return Critere(
        numero=7,
        cle="domicile_exterieur",
        libelle="Facteur domicile / exterieur",
        disponible=True,
        source="historique + moyennes de la competition",
        valeur=valeur,
        resume=(
            "Deja applique par le modele : les nombres attendus sont normalises "
            "par les moyennes a domicile et a l'exterieur de la competition. "
            "L'avantage propre a chaque equipe reste desactive (mesure)."
        ),
        effet={},
        echantillon={
            "domicile": valeur["domicile"]["matchs_dans_ce_lieu"],
            "exterieur": valeur["exterieur"]["matchs_dans_ce_lieu"],
        },
    )


# ---------------------------------------------------------------------------
# Critere 8 : fatigue et calendrier
# ---------------------------------------------------------------------------

def _charge(
    entries: list[dict[str, Any]], kickoff: str, table: dict[str, Any], team: str
) -> dict[str, Any]:
    """Repos, matchs recents et prochaine echeance d'une equipe."""
    joues = [e for e in entries if e.get("kickoff_utc")]
    repos = _jours(joues[0]["kickoff_utc"], kickoff) if joues else None
    recents = sum(
        1
        for e in joues
        if (_jours(e["kickoff_utc"], kickoff) or 999) <= FENETRE_CHARGE
    )
    suivant = None
    row = _rang(table, team)
    for fixture in (row or {}).get("prochains") or []:
        ecart = _jours(kickoff, fixture.get("kickoff_utc", ""))
        if ecart is not None and ecart > 0.5:
            suivant = round(ecart, 1)
            break
    return {
        "jours_de_repos": None if repos is None else round(repos, 1),
        "matchs_sur_%d_jours" % FENETRE_CHARGE: recents,
        "jours_avant_le_suivant": suivant,
    }


def _critere_fatigue(
    home_entries: list[dict[str, Any]],
    away_entries: list[dict[str, Any]],
    kickoff: str,
    table: dict[str, Any],
    teams: tuple[str, str],
    poids: float,
) -> Critere:
    """Jours de repos, densite du calendrier, echeance suivante.

    Le repos se lit dans l'historique ; l'echeance SUIVANTE ne s'y lit pas --
    un historique s'arrete a aujourd'hui par definition -- et vient du
    classement, qui publie le prochain match de chaque equipe. C'est ce qui
    permet de voir venir une rotation avant un match plus important.
    """
    libelle = "Fatigue et calendrier"
    source = "dates des matchs" + (" + prochaine journee" if table else "")
    if not kickoff:
        return _absent(
            8, "fatigue_calendrier", libelle, source,
            "Coup d'envoi inconnu : le repos ne peut pas etre calcule.",
        )

    valeur = {
        "domicile": _charge(home_entries, kickoff, table, teams[0]),
        "exterieur": _charge(away_entries, kickoff, table, teams[1]),
    }

    effet: dict[str, tuple[float, float]] = {}
    if poids > 0:
        facteurs = []
        for side in ("domicile", "exterieur"):
            repos = valeur[side]["jours_de_repos"]
            manque = 0.0 if repos is None else max(0.0, REPOS_COURT - repos)
            facteurs.append(_borne(1.0 - 0.025 * poids * manque, 0.08))
        if any(f != 1.0 for f in facteurs):
            effet["buts"] = tuple(facteurs)

    repos_txt = " / ".join(
        "%s j" % valeur[side]["jours_de_repos"]
        if valeur[side]["jours_de_repos"] is not None
        else "?"
        for side in ("domicile", "exterieur")
    )
    return Critere(
        numero=8,
        cle="fatigue_calendrier",
        libelle=libelle,
        disponible=True,
        source=source,
        valeur=valeur,
        resume="Repos : %s. Matchs sur %d jours : %d et %d."
        % (
            repos_txt,
            FENETRE_CHARGE,
            valeur["domicile"]["matchs_sur_%d_jours" % FENETRE_CHARGE],
            valeur["exterieur"]["matchs_sur_%d_jours" % FENETRE_CHARGE],
        ),
        effet=effet,
        echantillon={"prochaine_journee": bool(table)},
    )


# ---------------------------------------------------------------------------
# Critere 9 : motivation et enjeux extra-sportifs
# ---------------------------------------------------------------------------

# Ecart de rang en deca duquel deux equipes se disputent la meme place.
RIVAL_RANGS = 3

# Esperance de gain au-dela de laquelle un ecart au marche devient suspect
# plutot qu'interessant. Vingt-cinq pour cent de rendement attendu sur une issue
# de match de football n'existe pas : le marche integre l'effectif, la nouvelle
# du matin et l'argent de gens qui perdent a leurs frais. Un tel ecart mesure
# donc ce qui manque AU MODELE -- typiquement, un echantillon de trois journees.
ECART_SUSPECT = 0.25


def _critere_motivation(
    meetings: list[dict[str, Any]],
    h2h_detail: dict[str, Any],
    table: dict[str, Any],
    teams: tuple[str, str],
    cartons_competition: float | None,
    poids: float,
) -> Critere:
    """Tension attendue, mesuree plutot que supposee.

    Les listes de derbys ecrites a la main vieillissent mal et n'existent que
    pour les championnats connus. On mesure donc la rivalite la ou elle laisse
    une trace : le nombre de cartons que ces deux equipes se sont deja donnes,
    compare a la moyenne de leur competition. Une paire qui produit
    systematiquement six cartons quand la competition en produit quatre est un
    derby, qu'on l'ait ou non repertorie.

    Deux autres signaux, lisibles sans supposition : la revanche (le resultat de
    la derniere rencontre) et le match a enjeu direct (deux equipes separees de
    moins de RIVAL_RANGS places au classement).
    """
    libelle = "Motivation et enjeux extra-sportifs"
    source = "confrontations directes + classement"

    rangs = [
        (_rang(table, team) or {}).get("rang") for team in teams
    ]
    enjeu_direct = (
        all(r is not None for r in rangs) and abs(rangs[0] - rangs[1]) <= RIVAL_RANGS
    )

    cartons_h2h = h2h_detail.get("cartons_total")
    tension = None
    if cartons_h2h and cartons_competition:
        tension = cartons_h2h / cartons_competition

    derniere = meetings[0] if meetings else None
    revanche = ""
    if derniere:
        ecart = (derniere.get("buts_domicile") or 0) - (derniere.get("buts_exterieur") or 0)
        if abs(ecart) >= 3:
            gagnant = teams[0] if ecart > 0 else teams[1]
            revanche = "derniere rencontre gagnee %d-%d par %s" % (
                derniere.get("buts_domicile"), derniere.get("buts_exterieur"), gagnant,
            )

    if tension is None and not enjeu_direct and not revanche:
        return _absent(
            9, "motivation", libelle, source,
            "Ni confrontations chiffrees, ni classement : la tension attendue "
            "ne peut pas etre mesuree, seulement supposee -- ce que ce module "
            "ne fait pas.",
        )

    effet: dict[str, tuple[float, float]] = {}
    if poids > 0:
        # Le facteur brut est d'abord compose sans le poids, puis le poids est
        # applique UNE fois a l'ecart total. L'appliquer a chaque terme puis a
        # leur produit le ferait intervenir au carre, et un poids de 0,5
        # donnerait le quart de l'effet annonce plutot que la moitie.
        brut = 1.0
        if tension is not None:
            brut *= _lisse(tension, h2h_detail.get("matchs_couverts", 0), 1.0)
        if enjeu_direct:
            brut *= 1.03
        facteur = _borne(1.0 + poids * (brut - 1.0), 0.18)
        if facteur != 1.0:
            effet["cartons_jaunes"] = (facteur, facteur)

    morceaux = []
    if enjeu_direct:
        morceaux.append("equipes separees de %d place(s)" % abs(rangs[0] - rangs[1]))
    if tension is not None:
        morceaux.append(
            "%.1f carton(s) par confrontation contre %.1f dans la competition"
            % (cartons_h2h, cartons_competition)
        )
    if revanche:
        morceaux.append(revanche)

    return Critere(
        numero=9,
        cle="motivation",
        libelle=libelle,
        disponible=True,
        source=source,
        valeur={
            "enjeu_direct": enjeu_direct,
            "rangs": rangs,
            "cartons_par_confrontation": cartons_h2h,
            "cartons_competition": cartons_competition,
            "tension": tension,
            "revanche": revanche,
        },
        resume="; ".join(morceaux) + ".",
        effet=effet,
        echantillon={"confrontations_chiffrees": h2h_detail.get("matchs_couverts", 0)},
    )


# ---------------------------------------------------------------------------
# Critere 10 : meteo et etat du terrain
# ---------------------------------------------------------------------------

# Seuils au-dela desquels la meteo cesse d'etre un decor. Ils sont volontairement
# hauts : une bruine et dix kilometres/heure de vent ne changent rien a un match
# de football, et pretendre le contraire ajouterait du bruit a chaque fiche.
PLUIE_FORTE = 2.0     # mm sur l'heure
VENT_FORT = 30.0      # km/h
FROID = 2.0           # degres
CHALEUR = 30.0        # degres


def _critere_meteo(meteo: dict[str, Any], stade: dict[str, Any], poids: float) -> Critere:
    """Pluie, vent et temperature a l'heure du coup d'envoi.

    L'effet retenu va dans le sens documente et reste petit : la pluie et le
    vent degradent la precision du jeu court, ce qui reduit les buts et
    augmente les degagements -- donc les corners -- et les fautes.
    """
    libelle = "Meteo et etat du terrain"
    source = "Open-Meteo (prevision horaire)"
    if not meteo:
        return _absent(
            10, "meteo", libelle, source,
            "Ville du stade inconnue ou match trop lointain pour une prevision "
            "horaire (%d jours au maximum)." % api_client.WEATHER_HORIZON_DAYS,
        )

    pluie = meteo.get("precipitation_mm") or 0.0
    vent = meteo.get("vent_kmh") or 0.0
    temperature = meteo.get("temperature_c")

    effet: dict[str, tuple[float, float]] = {}
    if poids > 0:
        buts = corners = cartons = 1.0
        if pluie >= PLUIE_FORTE:
            buts *= 1.0 - 0.04 * poids
            corners *= 1.0 + 0.03 * poids
            cartons *= 1.0 + 0.04 * poids
        if vent >= VENT_FORT:
            buts *= 1.0 - 0.03 * poids
            corners *= 1.0 + 0.03 * poids
        if temperature is not None and (temperature >= CHALEUR or temperature <= FROID):
            buts *= 1.0 - 0.03 * poids
        for key, facteur in (
            ("buts", buts), ("corners", corners), ("cartons_jaunes", cartons)
        ):
            if facteur != 1.0:
                effet.update(_both(key, _borne(facteur, 0.10)))

    conditions = []
    if pluie >= PLUIE_FORTE:
        conditions.append("pluie soutenue (%.1f mm/h)" % pluie)
    if vent >= VENT_FORT:
        conditions.append("vent fort (%.0f km/h)" % vent)
    if temperature is not None and temperature >= CHALEUR:
        conditions.append("forte chaleur (%.0f degres)" % temperature)
    if temperature is not None and temperature <= FROID:
        conditions.append("froid (%.0f degres)" % temperature)

    return Critere(
        numero=10,
        cle="meteo",
        libelle=libelle,
        disponible=True,
        source=source,
        valeur=dict(meteo, stade=stade.get("stade", ""), capacite=stade.get("capacite")),
        resume=(
            ", ".join(conditions).capitalize() + "."
            if conditions
            else "Conditions sans effet attendu (%.0f degres, %.1f mm/h, %.0f km/h)."
            % (temperature or 0, pluie, vent)
        ),
        effet=effet,
        echantillon={},
    )


# ---------------------------------------------------------------------------
# Critere 11 : statistiques avancees (xG, xGA)
# ---------------------------------------------------------------------------

def _critere_xg(
    lam_buts: tuple[float, float],
    lam_xg: tuple[float, float] | None,
    echantillon: tuple[float, float],
    poids: float,
) -> Critere:
    """Les buts attendus, plus stables que les buts reellement marques.

    Le nombre de buts d'un match est un comptage tres faible : trois evenements
    en moyenne, et la finition varie beaucoup plus que la creation d'occasions.
    Les buts attendus mesurent la seconde sans la premiere, et c'est ce qui les
    rend plus previsibles que le score -- resultat etabli depuis que la mesure
    existe.

    Le xG passe ici exactement par le meme modele que les buts : moyenne de la
    competition, forces d'attaque et de defense, regularisation. Ce sont donc
    deux estimations de la MEME quantite, et le critere les melange plutot que
    d'appliquer une correction : `lambda = (1 - p) * buts + p * xG`.
    """
    libelle = "Statistiques avancees (xG, xGA)"
    source = "buts attendus de chaque match (Flashscore)"
    if lam_xg is None:
        return _absent(
            11, "statistiques_avancees", libelle, source,
            "Les buts attendus ne sont pas publies pour cette competition.",
        )

    ecart = (lam_xg[0] + lam_xg[1]) - (lam_buts[0] + lam_buts[1])
    return Critere(
        numero=11,
        cle="statistiques_avancees",
        libelle=libelle,
        disponible=True,
        source=source,
        valeur={
            "xg_attendu_domicile": round(lam_xg[0], 2),
            "xg_attendu_exterieur": round(lam_xg[1], 2),
            "buts_attendus_domicile": round(lam_buts[0], 2),
            "buts_attendus_exterieur": round(lam_buts[1], 2),
            "ecart_total": round(ecart, 2),
            "part_melangee": poids,
        },
        resume=(
            "xG : %.2f - %.2f contre %.2f - %.2f pour les buts (%+.2f sur le "
            "total) ; %.0f %% de xG dans le nombre attendu."
            % (lam_xg[0], lam_xg[1], lam_buts[0], lam_buts[1], ecart, 100 * poids)
        ),
        # Le melange n'est pas un multiplicateur : il est applique par `predict`
        # au moment ou les deux estimations existent.
        effet={},
        echantillon={"domicile": echantillon[0], "exterieur": echantillon[1]},
    )


# ---------------------------------------------------------------------------
# Critere 12 : discipline de l'arbitre
# ---------------------------------------------------------------------------

def _profil_arbitre(
    arbitre: str,
    entries: list[dict[str, Any]],
    use_cache: bool,
) -> dict[str, Any]:
    """Cartons par match de cet arbitre, sur les matchs ou on le retrouve.

    On ne dispose pas de l'historique d'un arbitre : la source ne l'expose pas.
    On le reconstitue donc dans les matchs deja connus -- ceux des deux equipes
    -- en lisant l'arbitre de chacun. Chaque lecture coute une requete, gardee
    trente jours ; le plafond ARBITRE_MAX borne ce que la reconstitution coute.

    L'echantillon obtenu est petit et BIAISE : ce sont les matchs de ces deux
    equipes-la, pas ceux de l'arbitre. C'est pourquoi le resultat n'est utilise
    qu'au-dela de ARBITRE_MIN rencontres, et regularise ensuite.
    """
    cartons: list[float] = []
    examines = 0
    for entry in entries[:ARBITRE_MAX]:
        match_id = entry.get("match_id") or ""
        if not match_id:
            continue
        examines += 1
        info = api_client.match_info(
            {"provider": "flashscore", "match_id": match_id, "statut": api_client.FINISHED},
            use_cache=use_cache,
        )
        if (info.get("arbitre") or "").strip() != arbitre:
            continue
        pour = (entry.get("stats") or {}).get("pour", {}).get("cartons_jaunes")
        contre = (entry.get("stats") or {}).get("contre", {}).get("cartons_jaunes")
        if pour is not None and contre is not None:
            cartons.append(pour + contre)
    return {
        "matchs_examines": examines,
        "matchs_de_cet_arbitre": len(cartons),
        "cartons_par_match": _moyenne(cartons),
    }


def _critere_arbitre(
    arbitre: str,
    profil: dict[str, Any],
    cartons_competition: float | None,
    poids: float,
) -> Critere:
    libelle = "Discipline de l'arbitre"
    source = "designation + fiches des matchs arbitres"
    if not arbitre:
        return _absent(
            12, "arbitre", libelle, source,
            "Arbitre pas encore designe (la source le publie le jour du match, "
            "parfois seulement apres).",
        )

    trouves = profil.get("matchs_de_cet_arbitre", 0)
    moyenne = profil.get("cartons_par_match")
    if not moyenne or trouves < ARBITRE_MIN or not cartons_competition:
        return Critere(
            numero=12,
            cle="arbitre",
            libelle=libelle,
            disponible="partiel",
            source=source,
            valeur=dict(profil, arbitre=arbitre),
            resume=(
                "%s designe. %d match(s) retrouve(s) dans l'historique des deux "
                "equipes : trop peu pour etablir sa moyenne de cartons (%d "
                "requis)." % (arbitre, trouves, ARBITRE_MIN)
            ),
            effet={},
            echantillon={"matchs": trouves},
        )

    rapport = _lisse(moyenne / cartons_competition, trouves)
    effet: dict[str, tuple[float, float]] = {}
    if poids > 0:
        facteur = _borne(1.0 + poids * (rapport - 1.0), 0.20)
        effet["cartons_jaunes"] = (facteur, facteur)

    return Critere(
        numero=12,
        cle="arbitre",
        libelle=libelle,
        disponible=True,
        source=source,
        valeur=dict(
            profil,
            arbitre=arbitre,
            cartons_competition=cartons_competition,
            rapport=rapport,
        ),
        resume="%s : %.1f carton(s) par match sur %d rencontre(s), contre %.1f "
        "dans la competition." % (arbitre, moyenne, trouves, cartons_competition),
        effet=effet,
        echantillon={"matchs": trouves},
    )


# ---------------------------------------------------------------------------
# Discipline : apport du modele des cartons 2.0.0
# ---------------------------------------------------------------------------

# Index des feuilles de match, un par date de coupure : le construire lit toute
# la table `feuilles` (quelques milliers de lignes), ce qu'une journee de
# fiches ne doit faire qu'une fois.
_INDEX_DISCIPLINE: dict[str, Any] = {}

# Matchs a partir desquels un entraineur est considere comme installe : son
# systeme est alors dans l'historique de l'equipe.
INSTALLATION_ENTRAINEUR = 8


def _index_discipline(date: str):
    from modeles.discipline import Discipline
    import store

    if date not in _INDEX_DISCIPLINE:
        _INDEX_DISCIPLINE.clear()
        try:
            _INDEX_DISCIPLINE[date] = Discipline().alimenter(store.feuilles(avant=date))
        except Exception:  # noqa: BLE001 - une archive illisible vaut neutre
            _INDEX_DISCIPLINE[date] = Discipline()
    return _INDEX_DISCIPLINE[date]


def discipline_du_match(
    match: dict[str, Any],
    teams: tuple[str, str],
    arbitre: str,
    compositions: dict[str, Any],
    arbitre_pays: str = "",
) -> dict[str, Any]:
    """Arbitre, onze aligne et entraineur, tels que les feuilles passees les voient.

    C'est l'apport que recoit le modele des cartons (`ModeleCartonsJaunes.
    ajuster`). Chaque volet se degrade seul : arbitre pas encore designe
    (rapport 1, mais sa variance elargit le total), composition pas encore
    publiee (facteur 1 : l'onze habituel est celui de l'historique),
    entraineur inconnu (facteur 1).
    """
    date = (match.get("kickoff_utc") or match.get("date") or "")[:10]
    if not date:
        date = datetime.now(timezone.utc).date().isoformat()
    from modeles.discipline import cle_arbitre

    index = _index_discipline(date)
    rendu: dict[str, Any] = {
        "arbitre": dict(index.arbitre(cle_arbitre(arbitre, arbitre_pays), date), arbitre=arbitre)
    }
    for cote, equipe in zip(("domicile", "exterieur"), teams):
        bloc = (compositions or {}).get(cote) or {}
        titulaires = [j for j in bloc.get("onze") or [] if j.get("id")]
        joueurs = index.facteur_joueurs(equipe, titulaires, bloc.get("systeme") or "", date)
        coach = (bloc.get("entraineur") or {}).get("id") or index.entraineur_en_poste(equipe).get("id", "")
        entraineur = index.facteur_entraineur(equipe, coach, date, INSTALLATION_ENTRAINEUR)
        entraineur["nom"] = (bloc.get("entraineur") or {}).get("nom") or index.entraineur_en_poste(equipe).get("nom", "")
        rendu[cote] = {"joueurs": joueurs, "entraineur": entraineur}
    return rendu


# ---------------------------------------------------------------------------
# Styles des joueurs : apport des modeles des corners et des tirs cadres
# ---------------------------------------------------------------------------

# Index des statistiques par joueur, un par date de coupure (meme raison que
# celui de la discipline).
_INDEX_STYLES: dict[str, Any] = {}


def _index_styles(date: str):
    from modeles.styles import Styles
    import store

    if date not in _INDEX_STYLES:
        _INDEX_STYLES.clear()
        try:
            matchs = [m for m in store.stats_joueurs_archivees() if (m.get("date") or "") < date]
            _INDEX_STYLES[date] = Styles().alimenter(matchs)
        except Exception:  # noqa: BLE001 - une archive illisible vaut neutre
            _INDEX_STYLES[date] = Styles()
    return _INDEX_STYLES[date]


def styles_du_match(
    match: dict[str, Any], teams: tuple[str, str], compositions: dict[str, Any]
) -> dict[str, Any]:
    """Ce que les onze du jour changent aux corners et aux tirs cadres.

    Par grandeur et par cote, le facteur de `modeles.styles` : volume de
    l'onze (aligne s'il est publie, habituel sinon) sur volume des onzes que
    l'historique de l'equipe reflete. Facteur 1 pour une equipe hors des
    championnats releves, ou trop peu vue.

        {"corners": {"domicile": {...}, "exterieur": {...}},
         "tirs_cadres": {...}}
    """
    date = (match.get("kickoff_utc") or match.get("date") or "")[:10]
    if not date:
        date = datetime.now(timezone.utc).date().isoformat()
    index = _index_styles(date)
    rendu: dict[str, Any] = {}
    for grandeur in ("corners", "tirs_cadres"):
        rendu[grandeur] = {}
        for cote, equipe in zip(("domicile", "exterieur"), teams):
            bloc = (compositions or {}).get(cote) or {}
            onze = [j.get("id") for j in bloc.get("onze") or [] if j.get("id")]
            rendu[grandeur][cote] = index.facteur(equipe, onze, date, grandeur)
    return rendu


# ---------------------------------------------------------------------------
# Critere 13 : cotes du marche et mouvements de lignes
# ---------------------------------------------------------------------------

def probabilites_implicites(cotes: dict[str, float]) -> dict[str, float]:
    """Probabilites du marche, marge retiree proportionnellement.

    La somme des inverses des cotes depasse 1 : l'exces est la marge de
    l'operateur. La retirer proportionnellement (methode dite « basique ») est
    la convention la plus repandue ; elle sous-estime legerement les favoris,
    ce que des methodes plus fines corrigent au prix d'hypotheses que rien ici
    ne permet de verifier.
    """
    inverses = {
        key: 1.0 / float(value)
        for key, value in cotes.items()
        if value and float(value) > 1.0
    }
    total = sum(inverses.values())
    if total <= 0:
        return {}
    return {key: value / total for key, value in inverses.items()}


def _deux_cotes(cotes: dict[str, Any] | None) -> tuple[dict, dict]:
    """(cotes moyennes, meilleures cotes) a partir d'un releve.

    Deux formes de releve coexistent, et il n'y a pas lieu d'en imposer une :
    celui de `api_client.odds_for` porte les deux jeux ("moyennes" et
    "meilleures"), celui qu'un utilisateur fournit a la main n'en porte qu'un.
    Dans le second cas la meme cote sert des deux cotes -- ce qui est exact :
    une cote unique est a la fois le consensus qu'on connait et celle qu'on
    peut prendre.
    """
    if not cotes:
        return {}, {}
    if "moyennes" in cotes or "meilleures" in cotes:
        moyennes = dict(cotes.get("moyennes") or {})
        return moyennes, dict(cotes.get("meilleures") or moyennes)
    plates = {
        cle: float(valeur)
        for cle, valeur in cotes.items()
        if cle in ("domicile", "nul", "exterieur") and valeur
    }
    return plates, plates


def critere_cotes(
    cotes: dict[str, Any] | None,
    modele: dict[str, float] | None,
    historique: list[dict[str, Any]] | None = None,
) -> Critere:
    """Ecart entre le modele et le marche, valeur des paris, derive des lignes.

    Publique, contrairement aux treize autres : les cotes arrivent APRES
    l'emission -- on les releve la veille, puis une heure avant -- alors que le
    contexte, lui, est fige avec la fiche. L'API doit donc pouvoir recalculer ce
    seul critere sur une prevision deja enregistree, sans la reemettre.

    Ce critere ne corrige RIEN, et c'est delibere. Melanger les probabilites du
    modele a celles du marche ameliorerait mecaniquement toutes les mesures de
    calibration -- le marche est bien calibre -- mais reviendrait a recopier le
    marche en croyant le prevoir. Trois choses utiles en sont tirees a la
    place :

      - **l'ecart** entre le modele et le consensus (cotes moyennes) ;
      - **la valeur** de chaque pari aux MEILLEURES cotes offertes, seule
        grandeur qui dise si un pari rapporte : `p x cote - 1`, l'esperance de
        gain par euro engage ;
      - **le mouvement de ligne** entre deux releves, qu'aucune cote seule ne
        peut montrer.
    """
    libelle = "Cotes du marche et mouvements de lignes"
    source = "betexplorer.com (une trentaine d'operateurs)"
    if not cotes:
        return _absent(
            13, "cotes_marche", libelle, source,
            "Aucune cote relevee pour ce match. Flashscore n'en publie pas ; "
            "BetExplorer n'en avait pas au moment du releve.",
        )

    moyennes, meilleures = _deux_cotes(cotes)
    marche = probabilites_implicites(moyennes)
    ecarts = {
        cle: round(modele[cle] - marche[cle], 4)
        for cle in marche
        if modele and cle in modele
    }
    # Esperance de gain par euro engage, aux meilleures cotes. Positive = le
    # modele juge le pari rentable ; c'est la seule lecture qui tienne compte
    # de ce qu'on touche reellement, la ou l'ecart de probabilite n'en dit rien.
    valeur = {
        cle: round(modele[cle] * meilleures[cle] - 1.0, 4)
        for cle in meilleures
        if modele and cle in modele
    }

    mouvement: dict[str, Any] = {}
    if historique and len(historique) >= 2:
        premier, dernier = historique[0], historique[-1]
        derive = {
            cle: round(float(dernier["cotes"][cle]) - float(premier["cotes"][cle]), 3)
            for cle in ("domicile", "nul", "exterieur")
            if cle in (premier.get("cotes") or {}) and cle in (dernier.get("cotes") or {})
        }
        mouvement = {
            "releves": len(historique),
            "depuis": premier.get("releve_le"),
            "jusqu_a": dernier.get("releve_le"),
            "derive": derive,
            # Une cote qui BAISSE veut dire que l'argent est alle sur cette
            # issue : c'est le sens du mouvement qui informe, pas son ampleur.
            "resserrement": [cle for cle, ecart in derive.items() if ecart <= -0.05],
        }

    marge = round(sum(1.0 / c for c in moyennes.values() if c > 1) - 1.0, 4)
    meilleur = max(valeur, key=valeur.get) if valeur else ""

    # Un ecart enorme au marche n'est presque jamais une occasion : c'est le
    # signe que le modele est mal informe. Le marche integre l'effectif, la
    # nouvelle du matin et l'argent de gens qui ont tort a leurs frais ; un
    # modele qui lui donne 25 points d'ecart sur trois journees de championnat
    # se trompe plus souvent qu'il ne trouve. La fiche le dit, parce qu'un
    # « +129 % d'esperance » affiche sans reserve se lit comme un conseil.
    demesure = bool(meilleur) and valeur[meilleur] > ECART_SUSPECT
    return Critere(
        numero=13,
        cle="cotes_marche",
        libelle=libelle,
        disponible=True,
        source=source,
        valeur={
            "cotes_moyennes": moyennes,
            "cotes_meilleures": meilleures,
            "probabilites_marche": {cle: round(p, 4) for cle, p in marche.items()},
            "marge_operateurs": marge,
            "ecart_modele_marche": ecarts,
            "valeur_esperee": valeur,
            "ecart_demesure": demesure,
            "mouvement": mouvement,
        },
        resume=(
            "Marge %.1f %%. Meilleure valeur : %s a %.2f (%+.1f %% d'esperance).%s"
            % (
                100 * marge, meilleur, meilleures[meilleur], 100 * valeur[meilleur],
                " Ecart au marche demesure : a ce niveau, c'est le modele qui "
                "est le plus souvent mal informe." if demesure else "",
            )
            if meilleur
            else "Cotes relevees ; probabilites du modele indisponibles."
        ),
        effet={},
        echantillon={"releves": len(historique or [])},
    )


# ---------------------------------------------------------------------------
# Critere 14 : taille de l'echantillon et score de confiance
# ---------------------------------------------------------------------------

# Effectif au-dela duquel une estimation est consideree pleinement informee.
# Dix matchs est la profondeur d'historique par defaut du projet ; en demander
# davantage penaliserait toutes les previsions de debut de saison.
ECHANTILLON_PLEIN = 10.0


def _confiance(
    effectifs: dict[str, tuple[float, float]],
    criteres: list[Critere],
) -> dict[str, Any]:
    """Score de confiance par grandeur, et global.

    Deux composantes, toutes deux mesurables :

      - **l'effectif** sur lequel la grandeur est estimee, rapporte a
        ECHANTILLON_PLEIN. C'est ce que le critere 14 demande, et c'est de loin
        la composante dominante : une prevision sur trois matchs n'est pas une
        prevision sur douze, quel que soit le reste.
      - **la couverture du contexte** : la part des treize autres criteres
        effectivement renseignes. Une fiche ou l'arbitre, la meteo et le
        classement manquent est moins informee qu'une fiche complete, meme si
        le modele, lui, a tout ce qu'il lui faut.

    Le score ne corrige aucune probabilite. Il qualifie ce qui est annonce, ce
    qui est autre chose -- et l'annoncer sans le qualifier serait le defaut que
    ce critere existe pour corriger.
    """
    # Le critere 14 n'est pas encore dans la liste -- il est ce qu'on calcule
    # ici -- mais il compte parmi les quatorze, et il est toujours renseigne.
    # L'omettre afficherait « 10 criteres sur 13 » sur une fiche qui en porte
    # quatorze.
    renseignes = sum(1 for c in criteres if c.disponible is True) + 1
    partiels = sum(1 for c in criteres if c.disponible == "partiel")
    total = len(criteres) + 1
    couverture = (renseignes + 0.5 * partiels) / max(1, total)

    par_grandeur: dict[str, float] = {}
    for key, (home, away) in effectifs.items():
        volume = min(1.0, min(home, away) / ECHANTILLON_PLEIN)
        # La couverture pese le quart : elle informe le lecteur, elle ne
        # remplace pas les matchs manquants.
        par_grandeur[key] = round(0.75 * volume + 0.25 * couverture, 3)

    global_ = round(sum(par_grandeur.values()) / len(par_grandeur), 3) if par_grandeur else 0.0
    niveau = "faible" if global_ < 0.5 else "moyenne" if global_ < 0.75 else "bonne"
    return {
        "score": global_,
        "niveau": niveau,
        "par_grandeur": par_grandeur,
        "criteres_renseignes": renseignes,
        "criteres_partiels": partiels,
        "criteres_total": total,
        "couverture_contexte": round(couverture, 3),
    }


def _critere_echantillon(confiance: dict[str, Any]) -> Critere:
    return Critere(
        numero=14,
        cle="taille_echantillon",
        libelle="Taille de l'echantillon statistique",
        disponible=True,
        source="tout ce qui precede",
        valeur=confiance,
        resume=(
            "Confiance %s (%.2f) : %d critere(s) renseigne(s) sur %d, "
            "%d partiel(s)."
            % (
                confiance["niveau"],
                confiance["score"],
                confiance["criteres_renseignes"],
                confiance["criteres_total"],
                confiance["criteres_partiels"],
            )
        ),
        effet={},
        echantillon={},
    )


# ---------------------------------------------------------------------------
# Assemblage
# ---------------------------------------------------------------------------

def combiner(criteres: list[Critere]) -> dict[str, dict[str, float]]:
    """Produit des corrections de tous les criteres, plafonne par grandeur.

    Le plafond n'est pas une precaution decorative : quatorze facteurs a 3 %
    dans le meme sens font 1,5, et aucun des quatorze ne le voulait. Il agit sur
    le PRODUIT, la ou chaque critere ne connait que sa propre part.
    """
    total: dict[str, dict[str, float]] = {}
    for critere in criteres:
        for key, (home, away) in critere.effet.items():
            bucket = total.setdefault(key, {"domicile": 1.0, "exterieur": 1.0})
            bucket["domicile"] *= home
            bucket["exterieur"] *= away
    for key, bucket in total.items():
        plafond = PLAFOND.get(key, 1.15)
        for side in bucket:
            bucket[side] = round(max(1.0 / plafond, min(plafond, bucket[side])), 4)
    return total


# ---------------------------------------------------------------------------
# Orchestration, en deux passes
# ---------------------------------------------------------------------------
#
# Deux criteres ont besoin de ce que le modele produit -- le critere 5 compare
# les confrontations directes au nombre de buts attendu, le critere 11 melange
# ce nombre avec celui tire des xG -- et un critere modifie ce que le modele
# calcule : le critere 4 repondere l'historique AVANT qu'il ne soit moyenne.
#
# L'ordre est donc contraint : collecter ce qui ne depend pas du modele, laisser
# le modele calculer avec ces poids, puis finir. Le faire en une seule passe
# demanderait soit de calculer les lambdas deux fois, soit d'appliquer le
# critere 4 apres coup -- c'est-a-dire de corriger une moyenne au lieu de la
# calculer correctement.

# Derniers matchs interroges par equipe pour reconstituer une composition
# probable (critere 3). Chacun coute une requete, gardee trente jours -- une
# composition passee ne change plus.
SYSTEMES_MAX = api_client.LINEUP_HISTORY


def _cartons_competition(baseline: dict[str, Any] | None) -> float | None:
    """Cartons jaunes par match dans la competition, les deux equipes cumulees.

    La reference stocke une moyenne PAR EQUIPE : le total d'un match en vaut le
    double. Comparer une moyenne de confrontation (qui est un total) a une
    moyenne par equipe donnerait un rapport de deux, et un arbitre parfaitement
    moyen passerait pour le plus severe du championnat.
    """
    means = ((baseline or {}).get("stats") or {}).get("cartons_jaunes") or {}
    globale = means.get("moyenne_globale")
    return 2.0 * globale if globale else None


def collecter(
    match: dict[str, Any],
    form: dict[str, Any],
    baseline: dict[str, Any] | None = None,
    poids: Poids = DEFAULT_POIDS,
    tz_name: str = "Europe/Paris",
    use_cache: bool = True,
    avec_systemes: int = SYSTEMES_MAX,
    avec_arbitre: bool = True,
    avec_cotes: bool = True,
    retrospectif: bool = False,
) -> dict[str, Any]:
    """Premiere passe : tout le contexte qui ne depend pas du modele.

    Rend un dictionnaire de travail que `finaliser` completera. Il porte deja
    les criteres 1, 2, 3, 4, 6, 7, 8, 9, 10 et 12, ainsi que la table des poids
    du critere 4 -- celle que `predict` doit appliquer a chaque match
    d'historique avant d'en faire la moyenne.

    Aucune de ces lectures n'est indispensable : chacune est protegee, et une
    source muette produit un critere `disponible: False` plutot qu'une erreur.
    Une prevision doit pouvoir sortir meme quand rien du contexte n'est connu.

    `retrospectif` sert a EVALUER le contexte sur des matchs deja joues. Deux
    sources ne peuvent pas etre ramenees a la date du match : le classement, qui
    est celui d'aujourd'hui et contient donc le resultat qu'on cherche a
    prevoir, et l'arbitre, publie apres coup alors qu'il est le plus souvent
    inconnu avant. Les lire sur un match passe donnerait une mesure flatteuse et
    fausse -- exactement ce que la coupure temporelle du banc d'essai existe
    pour empecher. Elles sont donc coupees, et les criteres qui en dependent se
    declarent indisponibles, comme ils le feraient sur un match reel sans elles.
    """
    teams = (form["domicile"]["equipe"], form["exterieur"]["equipe"])
    home_entries = _officiels(form["domicile"]["matchs"])
    away_entries = _officiels(form["exterieur"]["matchs"])
    kickoff = match.get("kickoff_utc") or ""

    # Les cotes d'un match passe sont celles d'apres coup, quand elles
    # existent encore : les lire en retrospectif donnerait au modele une
    # information que personne n'avait avant le match.
    cotes = (
        None
        if retrospectif or not avec_cotes
        else api_client.odds_for(match, use_cache=use_cache)
    )
    marche = None
    if cotes:
        marche = {
            "moyennes": api_client.odds_for(match, "moyennes", use_cache) or cotes,
            "meilleures": cotes,
        }

    table = {} if retrospectif else api_client.standings(match, tz_name, use_cache)
    infos = api_client.match_info(match, use_cache)
    if retrospectif:
        infos = {k: v for k, v in infos.items() if k != "arbitre"}
    compositions = api_client.lineups(match, use_cache)
    meteo = api_client.weather(infos.get("ville", ""), kickoff, use_cache)
    # Les blessures publiees aujourd'hui ne sont pas celles d'avant un match
    # deja joue : en retrospectif, la source donnerait l'effectif d'aujourd'hui
    # a une rencontre d'il y a une semaine.
    absences = {} if retrospectif else api_client.injuries(match, use_cache)

    historiques: dict[str, Any] = {}
    if avec_systemes and not compositions:
        # Inutile de reconstituer un onze probable quand la composition du jour
        # est publiee : elle fait foi, et elle porte deja les places.
        historiques = {
            "domicile": api_client.recent_lineups(
                home_entries, avec_systemes, use_cache
            ),
            "exterieur": api_client.recent_lineups(
                away_entries, avec_systemes, use_cache
            ),
        }

    profils = {"domicile": _profil(home_entries), "exterieur": _profil(away_entries)}
    meetings = form.get("confrontations") or []
    h2h = _h2h_stats(meetings, use_cache) if meetings else {"matchs_couverts": 0}
    cartons_ref = _cartons_competition(baseline)

    similarite, poids_entrees = _critere_similarite(
        home_entries, away_entries, baseline, teams, poids.similarite
    )

    arbitre = (infos.get("arbitre") or "").strip()
    profil_arbitre: dict[str, Any] = {}
    if arbitre and avec_arbitre:
        # Le cout est nul tant qu'aucun arbitre n'est designe, ce qui est le cas
        # de la plupart des matchs a venir : la reconstitution ne part que
        # lorsqu'il y a un nom a chercher.
        profil_arbitre = _profil_arbitre(
            arbitre, home_entries + away_entries, use_cache
        )

    criteres = [
        _critere_style(profils["domicile"], profils["exterieur"], poids.style),
        _critere_forme(home_entries, away_entries, table, teams, poids.forme),
        _critere_effectif(compositions, historiques, absences, poids.effectif),
        similarite,
        # 5 : confrontations directes -- seconde passe (compare au modele).
        _critere_enjeu(match.get("championnat", ""), table, teams, poids.enjeu),
        _critere_domicile(home_entries, away_entries, baseline),
        _critere_fatigue(
            home_entries, away_entries, kickoff, table, teams, poids.fatigue
        ),
        _critere_motivation(
            meetings, h2h, table, teams, cartons_ref, poids.motivation
        ),
        _critere_meteo(meteo, infos, poids.meteo),
        # 11 : statistiques avancees -- seconde passe (melange avec le modele).
        _critere_arbitre(arbitre, profil_arbitre, cartons_ref, poids.arbitre),
        # 13 : cotes -- seconde passe (comparees aux probabilites du modele).
        # 14 : taille de l'echantillon -- seconde passe (resume tout le reste).
    ]

    return {
        "criteres": criteres,
        "poids_entrees": poids_entrees,
        # L'apport du modele des cartons. En retrospectif, l'arbitre est coupe
        # comme pour le critere 12 : seul son inconnu (la variance) joue.
        "discipline": discipline_du_match(
            match, teams, arbitre, compositions, infos.get("arbitre_pays", "")
        ),
        # L'apport des corners et des tirs cadres : les styles des joueurs.
        "styles": styles_du_match(match, teams, compositions),
        "poids": poids,
        "profils": profils,
        "confrontations": meetings,
        "confrontations_chiffrees": h2h,
        "informations": infos,
        "meteo": meteo,
        "absences": absences,
        "cotes": marche,
        "classement_disponible": bool(table),
        "sources": {
            "classement": bool(table),
            "informations": bool(infos),
            "compositions": bool(compositions),
            "absences": bool(absences),
            "meteo": bool(meteo),
            "arbitre": bool(arbitre),
            "cotes": bool(marche),
        },
    }


def confronter(
    collecte: dict[str, Any],
    lam_buts: tuple[float, float],
    lam_xg: tuple[float, float] | None,
    echantillon_xg: tuple[float, float] = (0.0, 0.0),
) -> tuple[list[Critere], dict[str, dict[str, float]]]:
    """Deuxieme temps : les deux criteres qui se comparent au modele.

    Le critere 5 rapporte les confrontations directes au nombre de buts attendu,
    le critere 11 confronte ce nombre a celui tire des xG. Les deux ont besoin
    de ce que le modele a produit AVANT toute correction -- corriger d'abord
    puis comparer reviendrait a mesurer un ecart qu'on vient soi-meme de reduire.

    Rend ces deux criteres et le produit de TOUTES les corrections, celles-ci
    comprises. A ce stade, chaque critere porteur d'effet est connu : `predict`
    peut appliquer les corrections et calculer ses probabilites definitives.
    """
    poids: Poids = collecte["poids"]
    confrontations = _critere_confrontations(
        collecte["confrontations"],
        lam_buts,
        collecte["confrontations_chiffrees"],
        poids.confrontations,
    )
    avancees = _critere_xg(lam_buts, lam_xg, echantillon_xg, poids.xg)
    criteres = [confrontations, avancees]
    return criteres, combiner(collecte["criteres"] + criteres)


def finaliser(
    collecte: dict[str, Any],
    criteres_modele: list[Critere],
    effectifs: dict[str, tuple[float, float]],
    corrections: dict[str, dict[str, float]],
    resultat: dict[str, float] | None = None,
    cotes: dict[str, Any] | None = None,
    historique_cotes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Troisieme temps : la fiche de contexte, telle qu'elle sera enregistree.

    Les deux derniers criteres arrivent ici parce qu'ils portent sur le
    resultat : le 13 compare les probabilites definitives aux cotes du marche,
    le 14 resume ce que valent toutes les autres. Aucun des deux ne corrige
    quoi que ce soit -- les corrections sont deja arretees par `confronter`, et
    les recalculer ici pourrait les faire diverger de celles qui ont
    effectivement servi.

    `effectifs` porte, par grandeur, le nombre EFFICACE de matchs sur lequel
    elle est estimee : c'est la matiere du critere 14.
    """
    criteres: list[Critere] = list(collecte["criteres"]) + list(criteres_modele)
    # Les cotes passees en argument l'emportent : elles viennent de l'appelant,
    # qui peut en avoir de plus fraiches ou d'un operateur precis. A defaut, on
    # prend celles que la collecte a relevees.
    criteres.append(
        critere_cotes(cotes or collecte.get("cotes"), resultat, historique_cotes)
    )

    confiance = _confiance(effectifs, criteres)
    criteres.append(_critere_echantillon(confiance))
    criteres.sort(key=lambda c: c.numero)

    return {
        "criteres": [c.as_dict() for c in criteres],
        "corrections": corrections,
        "confiance": confiance,
        "profils": collecte["profils"],
        "informations": collecte["informations"],
        "meteo": collecte["meteo"],
        "sources": collecte["sources"],
        "poids": collecte["poids"]._asdict(),
    }


def vide(raison: str = "") -> dict[str, Any]:
    """Fiche de contexte inexploitable, sans aucune correction.

    Sert quand la collecte a echoue en bloc -- provider sans historique, match
    d'une source autre que Flashscore. La prevision reste emise : elle est alors
    exactement celle du modele seul, et la fiche le dit au lieu de laisser
    croire que les quatorze criteres ont ete regardes.
    """
    return {
        "criteres": [],
        "corrections": {},
        "confiance": {
            "score": 0.0,
            "niveau": "faible",
            "par_grandeur": {},
            "criteres_renseignes": 0,
            "criteres_partiels": 0,
            "criteres_total": 0,
            "couverture_contexte": 0.0,
        },
        "profils": {},
        "informations": {},
        "meteo": {},
        "sources": {},
        "poids": DEFAULT_POIDS._asdict(),
        "indisponible": raison or "Contexte non collecte pour ce match.",
    }
