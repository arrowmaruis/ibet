"""Mesure prospective des criteres laisses a poids zero.

Quatre des quatorze criteres sont calcules, affiches, et ne deplacent rien :
l'effectif disponible (3), l'enjeu de la competition (6), la motivation (9) et
la discipline de l'arbitre (12). Ils donnent CINQ signaux a mesurer, le critere
3 en portant deux qui n'ont rien a voir l'un avec l'autre -- les absences, et le
changement de dispositif par rapport a l'habitude. Ce n'est pas un jugement sur leur pertinence,
c'est l'absence de mesure -- et la difference compte, parce que la campagne a
montre que quatre criteres parfaitement defendables sur le papier DEGRADAIENT
la prevision une fois mesures.

Ils ne sont pas mesurables a posteriori. Leurs sources ne publient que l'etat
courant (la liste des blesses d'aujourd'hui, le classement d'aujourd'hui) ou
publient apres coup (l'arbitre designe). Rejouer un match passe en leur donnant
ces valeurs reviendrait a leur donner ce que personne n'avait avant le coup
d'envoi, et le gain mesure serait un artefact.

Ils sont en revanche mesurables PROSPECTIVEMENT, et c'est ce que fait ce module.
Chaque fiche emise enregistre son contexte tel qu'il etait a l'emission : la
liste des absents de ce jour-la, l'arbitre de ce jour-la. Une fois le match
joue, on dispose donc du signal tel qu'il etait connu AVANT, et du resultat.
C'est tout ce qu'il faut.

**Ce qui est mesure.** Un poids nul n'annule pas le critere : `effet` reste vide
mais `valeur` est calcule et enregistre. Le signal brut est donc au dossier,
meme si aucune correction n'en a ete tiree. On regresse alors le residu du
modele sur ce signal :

    residu = reel / prevu        signal = ce que le critere avait vu

    residu = 1 + pente x (signal - reference) + bruit

  - **pente nulle** : le critere ne dit rien que le modele ne sache deja. C'est
    le resultat le plus probable et il faut pouvoir le lire : il justifie de
    laisser le poids a zero, cette fois pour une raison mesuree.
  - **pente positive** : le critere voit ce que le modele rate, et dans le bon
    sens. C'est la seule justification acceptable pour lui donner un poids.
  - **pente negative** : il voit a l'envers. Le laisser a zero ne suffirait pas,
    il faudrait comprendre pourquoi.

Le rapport rend la pente AVEC son erreur type, et rien n'est conclu sous deux
erreurs types -- meme barre que partout ailleurs dans le projet. Sur quelques
dizaines de fiches, l'issue attendue est « on ne sait pas encore », et le dire
est le seul resultat honnete tant que le compte n'y est pas.

Ce module ne modifie AUCUN poids. Il produit le chiffre a partir duquel un
poids pourrait etre change, a la main, dans `context.Poids`.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Sequence

import store

#: Nombre de fiches sous lequel on ne rend pas de pente. Une regression sur dix
#: points donne un chiffre, jamais un resultat -- et l'afficher inviterait a le
#: lire.
FICHES_MIN = 30


def _rapport_arbitre(valeur: dict[str, Any]) -> float | None:
    """Cartons de l'arbitre designe, rapportes a la moyenne de la competition.

    Deja lisse par le critere selon le nombre de matchs retrouves, et deja
    centre sur 1 : un arbitre moyen vaut 1, un arbitre severe 1.2. C'est
    directement le facteur que le critere appliquerait si on lui donnait un
    poids, ce qui rend la pente lisible sans conversion -- une pente de 1
    signifie « le critere a exactement raison ».
    """
    rapport = valeur.get("rapport")
    return float(rapport) if isinstance(rapport, (int, float)) else None


def _absences(valeur: dict[str, Any]) -> float | None:
    """Poids des absences offensives des deux equipes, cumule.

    Cumule et non differencie : le residu mesure ici porte sur le TOTAL de buts,
    et deux attaquants absents d'un cote ou un de chaque cote pesent pareil sur
    le total. Le sens attendu est negatif -- plus d'absents, moins de buts --,
    ce qui est la seule chose que la pente doive confirmer ou infirmer.
    """
    manque = valeur.get("manque")
    if not isinstance(manque, dict):
        return None
    total = 0.0
    for cote in ("domicile", "exterieur"):
        part = manque.get(cote)
        if isinstance(part, dict):
            total += float(part.get("offensif", 0.0) or 0.0)
        elif isinstance(manque.get("offensif"), (int, float)):
            # Forme a plat, sans distinction de cote.
            return float(manque["offensif"])
    return total


def _tension(valeur: dict[str, Any]) -> float | None:
    """Tension de la rencontre telle que le critere 9 l'a estimee."""
    tension = valeur.get("tension")
    return float(tension) if isinstance(tension, (int, float)) else None


def _couperet(valeur: dict[str, Any]) -> float | None:
    """1 si le match est un couperet, 0 sinon. Indicatrice, pas une echelle."""
    couperet = valeur.get("couperet")
    return 1.0 if couperet else 0.0


def _defensivite(systeme: str) -> float | None:
    """Indice defensif d'un dispositif : defenseurs moins attaquants.

    "5-3-2" vaut +3, "4-4-2" +2, "4-3-3" +1, "3-4-3" 0. L'echelle n'a pas de
    sens absolu et n'en a pas besoin : seule sa VARIATION est lue.

    Deux ecritures coexistent et il faut accepter les deux. La source ecrit
    "1-4-2-3-1" -- gardien COMPRIS, donc onze joueurs --, alors que la notation
    courante l'omet et n'en compte que dix. Exiger dix rendait None sur tous les
    dispositifs reels, et le signal ne mesurait rien sans que rien ne le dise :
    la fonction rendait simplement « inconnu » a chaque appel. On retire donc le
    gardien quand il est la, ce qu'un premier nombre valant 1 pour un total de
    onze suffit a reconnaitre.

    Une ligne unique ("11") ou illisible rend None plutot que zero : un
    dispositif inconnu n'est pas un dispositif equilibre.
    """
    lignes = []
    for morceau in (systeme or "").split("-"):
        morceau = morceau.strip()
        if not morceau.isdigit():
            return None
        lignes.append(int(morceau))
    if len(lignes) >= 3 and sum(lignes) == 11 and lignes[0] == 1:
        lignes = lignes[1:]
    if len(lignes) < 2 or sum(lignes) != 10:
        # Dix joueurs de champ : ce qui ne les compte pas n'est pas un systeme.
        return None
    return float(lignes[0] - lignes[-1])


def _bascule_defensive(valeur: dict[str, Any]) -> float | None:
    """De combien les deux equipes se replient PAR RAPPORT A LEUR HABITUDE.

    C'est la seule lecture du dispositif que le modele ne fasse pas deja. Le
    critere 1 classe le style depuis les comptages -- possession, tirs, corners
    -- et y lit deja le bloc bas et la contre-attaque, mieux que ne le ferait
    une etiquette : un "5-3-2" sur le papier peut etre un bloc haut a trois
    centraux, la possession le distingue, le libelle non. Le systeme HABITUEL
    d'une equipe est donc redondant, et c'est ce que dit le critere 3 en
    refusant de corriger sur lui.

    Mais un CHANGEMENT ne l'est pas. Une equipe qui joue 4-3-3 toute la saison
    et qui aligne un 5-4-1 fait quelque chose que ses statistiques n'ont jamais
    vu : aucun profil tactique ne peut le savoir, et l'information n'existe
    qu'une heure avant le coup d'envoi, dans la composition annoncee. C'est
    exactement le genre de signal qui ne peut pas etre mesure a posteriori --
    d'ou sa place ici.

    Positif = les deux equipes se replient plus qu'a leur habitude, ce qui doit
    faire BAISSER le total de buts. La pente attendue est donc negative, et
    c'est la seule chose que la mesure doive confirmer ou infirmer.

    Somme des deux cotes, et non difference : le residu mesure porte sur le
    total de buts, auquel un repli de l'une ou de l'autre retire pareil.
    """
    total = 0.0
    vu = False
    for cote in ("domicile", "exterieur"):
        part = valeur.get(cote)
        if not isinstance(part, dict):
            continue
        annonce = _defensivite(part.get("systeme_annonce", ""))
        habituel = _defensivite(part.get("systeme_habituel", ""))
        # Sans composition annoncee il n'y a pas de changement a constater : la
        # prevision de la veille ne connait que l'habitude, et comparer
        # l'habitude a elle-meme donnerait zero pour tout le monde -- un signal
        # constant, que la regression rejetterait a juste titre.
        if annonce is None or habituel is None:
            continue
        total += annonce - habituel
        vu = True
    return total if vu else None


#: Les quatre criteres a poids zero, avec la grandeur sur laquelle chacun
#: pretend agir, l'extracteur de son signal, et la reference autour de laquelle
#: ce signal est centre. La grandeur compte : un critere d'arbitre juge sur les
#: buts ne serait pas juge du tout.
SIGNAUX: dict[str, tuple[str, str, Callable[[dict], float | None], float]] = {
    "arbitre": ("Discipline de l'arbitre", "Cartons jaunes", _rapport_arbitre, 1.0),
    "effectif": ("Systeme et effectif disponible", "Buts", _absences, 0.0),
    "motivation": ("Motivation et enjeux", "Cartons jaunes", _tension, 0.0),
    "enjeu": ("Contexte de la competition", "Buts", _couperet, 0.0),
    # Cinquieme entree, sur le MEME critere 3 que "effectif" mais sur un autre
    # de ses signaux : les absences d'un cote, le changement de dispositif de
    # l'autre. Les deux sont dans la meme fiche et n'ont rien a voir -- perdre
    # son buteur et se replier a cinq derriere ne se mesurent pas ensemble.
    "systeme": ("Bascule defensive du dispositif", "Buts", _bascule_defensive, 0.0),
}


#: Critere du contexte ou lire le signal, quand sa cle differe de celle du
#: signal. `effectif` et `systeme` sont deux lectures du meme critere 3.
CRITERE_PORTEUR = {
    "effectif": "systeme_et_effectif",
    "systeme": "systeme_et_effectif",
}


def _reel(resultat: dict[str, Any], grandeur: str) -> float | None:
    """Total observe d'une grandeur, depuis le resultat enregistre.

    Les buts sont sous la cle "score", les autres sous leur libelle ; les deux
    sont ecrits "domicile - exterieur". Cette asymetrie vient du verificateur et
    n'est pas reecrite ici : une fiche deja enregistree ne se reecrit pas.
    """
    brut = resultat.get("score" if grandeur == "Buts" else grandeur)
    if not isinstance(brut, str):
        return None
    domicile, separateur, exterieur = brut.partition(" - ")
    if not separateur:
        return None
    try:
        return float(domicile) + float(exterieur)
    except ValueError:
        return None


def observations(cle: str, fiches: Sequence[dict[str, Any]]) -> list[tuple[float, float]]:
    """(signal, residu) de chaque fiche exploitable pour ce critere.

    Une fiche est ecartee sans bruit si le critere etait indisponible ce jour-la
    (arbitre pas encore designe, classement introuvable), si la grandeur n'a pas
    ete prevue, ou si le match n'a pas encore ete tranche.
    """
    if cle not in SIGNAUX:
        return []
    _, grandeur, extraire, _ = SIGNAUX[cle]
    # Deux signaux different peuvent etre lus dans le MEME critere : les
    # absences et la bascule defensive sont tous deux dans le critere 3.
    cle_critere = CRITERE_PORTEUR.get(cle, cle)
    couples = []
    for fiche in fiches:
        resultat = fiche.get("resultat_reel")
        contexte = fiche.get("contexte") or {}
        if not resultat or not contexte:
            continue
        critere = next(
            (c for c in contexte.get("criteres") or []
             if c.get("cle") == cle_critere),
            None,
        )
        if not critere or not critere.get("disponible"):
            continue
        signal = extraire(critere.get("valeur") or {})
        if signal is None:
            continue
        ligne = next(
            (g for g in fiche.get("grandeurs") or [] if g.get("grandeur") == grandeur),
            None,
        )
        prevu = (ligne or {}).get("attendu_total")
        observe = _reel(resultat, grandeur)
        if not prevu or observe is None:
            continue
        couples.append((signal, observe / prevu))
    return couples


def _pente(couples: Sequence[tuple[float, float]], reference: float) -> dict[str, Any]:
    """Moindres carres du residu sur le signal centre, avec son erreur type.

    L'ordonnee a l'origine est laissee libre plutot que fixee a 1 : un biais
    d'ensemble sur la grandeur (les corners l'ont montre) n'a rien a voir avec
    la question posee, et le contraindre a 1 le ferait remonter dans la pente.
    """
    n = len(couples)
    if n < 3:
        return {"fiches": n}
    xs = [signal - reference for signal, _ in couples]
    ys = [residu for _, residu in couples]
    moyenne_x = sum(xs) / n
    moyenne_y = sum(ys) / n
    variance = sum((x - moyenne_x) ** 2 for x in xs)
    if variance <= 0:
        # Signal constant sur tout l'echantillon : la pente n'est pas definie.
        # C'est le cas d'un critere binaire dont aucune fiche n'a vu le cas
        # rare -- aucun match couperet, par exemple. Ce n'est pas un echec, et
        # le dire evite de le prendre pour un zero mesure.
        return {"fiches": n, "signal_constant": True}
    covariance = sum((x - moyenne_x) * (y - moyenne_y) for x, y in zip(xs, ys))
    pente = covariance / variance
    origine = moyenne_y - pente * moyenne_x
    residus = [y - (origine + pente * x) for x, y in zip(xs, ys)]
    if n > 2:
        s2 = sum(r * r for r in residus) / (n - 2)
        erreur = (s2 / variance) ** 0.5
    else:
        erreur = None
    return {
        "fiches": n,
        "pente": pente,
        "erreur_type": erreur,
        "t": pente / erreur if erreur else None,
        "significatif": abs(pente) > 2 * erreur if erreur else False,
        "signal_moyen": moyenne_x + reference,
        "residu_moyen": moyenne_y,
    }


def _fiches_tranchees(limite: int) -> list[dict[str, Any]]:
    """Fiches dont le match a ete joue ET verifie, contexte compris."""
    with store.connect() as connection:
        lignes = connection.execute(
            "SELECT payload FROM predictions WHERE resultat_reel IS NOT NULL"
            " ORDER BY coup_denvoi_local DESC LIMIT ?",
            (limite,),
        ).fetchall()
    return [json.loads(ligne["payload"]) for ligne in lignes]


def bilan(limite: int = 1000) -> dict[str, Any]:
    """Ce que chacun des quatre criteres a poids zero vaut, sur les fiches emises.

    Rend une ligne par critere : combien de fiches l'ont vu, la pente du residu
    sur son signal, et si elle sort du bruit. C'est le chiffre qui manque pour
    decider de leur poids, et il ne peut venir que de l'usage reel.
    """
    fiches = _fiches_tranchees(limite)
    lignes = []
    for cle, (libelle, grandeur, _, reference) in SIGNAUX.items():
        couples = observations(cle, fiches)
        mesure = _pente(couples, reference)
        lignes.append(
            dict(
                mesure,
                critere=cle,
                libelle=libelle,
                grandeur=grandeur,
                # Le compte manquant est plus utile qu'un verdict : il dit
                # combien de fiches il reste a emettre avant de pouvoir trancher.
                assez=mesure.get("fiches", 0) >= FICHES_MIN,
                manquantes=max(0, FICHES_MIN - mesure.get("fiches", 0)),
            )
        )
    return {
        "fiches_tranchees": len(fiches),
        "seuil": FICHES_MIN,
        "criteres": lignes,
    }
