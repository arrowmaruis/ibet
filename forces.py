"""Force des equipes sur une echelle unique, toutes competitions confondues.

`predict` normalise chaque equipe par la moyenne de SA competition, puis
multiplie les deux forces. La construction est correcte a l'interieur d'un
championnat, et fausse des qu'on en sort : deux equipes qui marquent chacune
1.8 but par match recoivent la meme note, que ce championnat soit la Premier
League ou la troisieme division suedoise. Le modele n'a aucune notion de force
RELATIVE des competitions.

La mesure des fiches emises l'a etabli : sur les issues, le modele n'atteignait
50 % de certitude que 7 fois sur 26, la ou le marche va jusqu'a 90 %. Il ne
savait pas dire qu'une equipe est nettement superieure a une autre, parce que la
regularisation le ramenait chaque fois vers la moyenne de sa competition -- et
qu'il n'avait rien d'autre vers quoi le ramener.

Ce module fournit ce qui manquait : des notes d'ATTAQUE et de DEFENSE sur une
echelle commune, construites sur les resultats reels et sur eux seuls.

  - **Le modele de Maher (1982) et Dixon & Coles (1997)**, qui separe ce qu'une
    equipe produit de ce qu'elle concede :

        lambda_dom = exp(mu + h + attaque[dom] - defense[ext])
        lambda_ext = exp(mu     + attaque[ext] - defense[dom])

    Les notes sont estimees au maximum de vraisemblance sur tout le corpus,
    chaque match pondere par son anciennete (voir `construire`). L'echelle se
    construit ainsi toute seule, sans qu'on ait a declarer qu'un championnat
    vaut plus qu'un autre : battre une equipe bien notee rapporte plus que
    battre une equipe faible.

  - **Deux jeux de notes, deux usages.** Celles construites sur les BUTS donnent
    le meilleur ecart entre les deux equipes ; celles construites sur les xG
    donnent le meilleur TOTAL. Les xG lissent la reussite devant le but : ils
    disent mieux combien d'occasions on se procure, moins bien laquelle des deux
    convertit. Voir `lambdas_attendus`.

  - **Rien n'est declare, tout est mesure.** La moyenne de reference, l'avantage
    du terrain, l'oubli et l'a priori sont estimes ou regles sur le corpus.

Mesure en walk-forward sur 5 360 matchs, chaque match prevu avec les notes
d'AVANT lui (premiere version, apprise en ligne) :

    forme recente seule      Brier 0.6196     <- ce que faisait le modele
    Elo (ecart seul)         Brier 0.6047     t = -4.8 contre la forme
    attaque / defense        Brier 0.5916     t = -11.7 contre la forme
    uniforme                 Brier 0.6667

L'estimation au maximum de vraisemblance fait mieux encore que l'apprentissage
en ligne ; ses mesures sont dans `construire`.

Et, contrairement a Elo, elles ameliorent aussi le TOTAL : erreur absolue 1.396
contre 1.437, t = -5.9. Elo ne savait que departager deux equipes ; il a donc
ete remplace par ce modele, qui dit aussi combien de buts attendre.

Les notes se construisent a partir des historiques deja en cache -- douze mille
matchs de 2003 a aujourd'hui, sans une requete de plus.

    python forces.py     # reconstruit forces.json
"""

from __future__ import annotations

import datetime
import json
import math
import pathlib
from typing import Any, Sequence

import cache

#: Sous ce nombre de matchs, les notes d'une equipe ne sont pas etablies : elles
#: ont trop peu bouge pour valoir mieux que la note de depart, et s'en servir
#: reviendrait a declarer moyenne une equipe qu'on ne connait pas.
MATCHS_MIN = 8

def composantes(matchs: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Groupe de chaque equipe : deux equipes reliees par une suite de matchs.

    La mise a jour est a somme nulle entre les deux equipes : ce que l'attaque
    de l'une gagne, la defense de l'autre le perd. La moyenne d'un groupe ferme
    ne bouge donc jamais, quoi qu'il arrive -- et la meilleure equipe d'un
    championnat faible monte exactement comme la meilleure d'un championnat
    fort. Les notes sont comparables A L'INTERIEUR d'un groupe, et ne veulent
    rien dire d'un groupe a l'autre.

    Le corpus le montre : sur 2 140 equipes, 16 groupes, dont un de 1 673
    equipes (78 %) relie par les coupes d'Europe. Shymkent y ressortait
    au-dessus du Bayern Munich, parce qu'il domine un ilot qui n'a jamais
    rencontre le reste -- sa note ne disait pas qu'il etait fort, seulement
    qu'il etait le meilleur de son ilot.

    On ne peut pas corriger ce decalage sans matchs entre les groupes : c'est
    une information qui n'existe pas. On peut seulement refuser de comparer ce
    qui n'est pas comparable, et c'est ce que fait `ecart_attendu`.

    Rend {equipe: numero de groupe}, le groupe 0 etant le plus grand.
    """
    voisins: dict[str, set[str]] = {}
    for match in matchs:
        dom, ext = match.get("domicile"), match.get("exterieur")
        if not dom or not ext or dom == ext:
            continue
        voisins.setdefault(dom, set()).add(ext)
        voisins.setdefault(ext, set()).add(dom)

    groupes: list[set[str]] = []
    vus: set[str] = set()
    for depart in voisins:
        if depart in vus:
            continue
        pile, groupe = [depart], set()
        while pile:
            equipe = pile.pop()
            if equipe in groupe:
                continue
            groupe.add(equipe)
            vus.add(equipe)
            pile.extend(voisins[equipe] - groupe)
        groupes.append(groupe)

    groupes.sort(key=len, reverse=True)
    return {
        equipe: numero
        for numero, groupe in enumerate(groupes)
        for equipe in groupe
    }


def _match_depuis_entree(equipe: str, entree: dict[str, Any]) -> dict[str, Any] | None:
    """Une ligne d'historique, vue depuis l'equipe suivie, remise a l'endroit.

    L'historique dit « ce que MON equipe a marque et encaisse, chez elle ou a
    l'exterieur ». Les notes ont besoin du match tel qu'il s'est joue : qui
    recevait, et le score dans cet ordre -- l'avantage du terrain est un terme
    du modele, il ne peut pas etre estime sur un historique qui melange les
    deux lieux.
    """
    if entree.get("amical"):
        return None
    pour, contre = entree.get("buts_pour"), entree.get("buts_contre")
    adversaire = (entree.get("adversaire") or "").strip()
    if pour is None or contre is None or not adversaire or not equipe:
        return None
    a_domicile = entree.get("lieu") == "domicile"
    return {
        "match_id": entree.get("match_id", ""),
        "kickoff_utc": entree.get("kickoff_utc", ""),
        "competition": entree.get("competition", ""),
        "domicile": equipe if a_domicile else adversaire,
        "exterieur": adversaire if a_domicile else equipe,
        "buts_domicile": pour if a_domicile else contre,
        "buts_exterieur": contre if a_domicile else pour,
    }


def corpus_depuis_cache(chemin: pathlib.Path | None = None) -> list[dict[str, Any]]:
    """Tous les matchs joues retrouvables dans le cache des historiques.

    Chaque match y figure deux fois -- une par equipe suivie -- et on le
    deduplique par son identifiant : compte deux fois, il pesterait deux fois
    sur les notes.

    Les amicaux sont ecartes, comme partout ailleurs dans le projet : un 8-0 de
    pre-saison ne dit rien de la force d'une equipe.
    """
    dossier = chemin or cache.CACHE_DIR
    if not dossier.exists():
        return []
    matchs: dict[str, dict[str, Any]] = {}
    for fichier in dossier.glob("*.json"):
        try:
            paquet = json.loads(fichier.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if "|hist|" not in str(paquet.get("key", "")):
            continue
        for bord in paquet.get("value") or []:
            if not isinstance(bord, dict) or "matchs" not in bord:
                continue
            equipe = (bord.get("equipe") or "").strip()
            for entree in bord["matchs"]:
                match = _match_depuis_entree(equipe, entree)
                if match and match["match_id"]:
                    matchs[match["match_id"]] = match
    return list(matchs.values())


# ---------------------------------------------------------------------------
# Notes persistees
# ---------------------------------------------------------------------------


def joindre_xg(matchs: Sequence[dict[str, Any]]) -> int:
    """Attache a chaque match ses xG, lus dans les fiches statistiques en cache.

    Les xG sont le produit central des fournisseurs de donnees evenementielles :
    ils comptent la QUALITE des occasions au lieu de leur issue. Un but est un
    evenement rare, donc tres bruite ; une somme d'occasions l'est beaucoup
    moins, et dit mieux ce qu'une equipe a produit.

    Rend le nombre de matchs enrichis. Les fiches sont deja en cache : aucune
    requete.
    """
    reperes = {m["match_id"]: m for m in matchs if m.get("match_id")}
    trouves = 0
    if not cache.CACHE_DIR.exists():
        return 0
    for fichier in cache.CACHE_DIR.glob("*.json"):
        try:
            paquet = json.loads(fichier.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        cle = str(paquet.get("key", ""))
        if "|stats|" not in cle:
            continue
        match = reperes.get(cle.rsplit("|", 1)[-1])
        if not match:
            continue
        valeur = paquet.get("value") or {}
        gauche = _nombre((valeur.get("domicile") or {}).get("xg"))
        droite = _nombre((valeur.get("exterieur") or {}).get("xg"))
        if gauche is not None and droite is not None:
            match["xg_domicile"], match["xg_exterieur"] = gauche, droite
            trouves += 1
    return trouves


def _nombre(valeur: Any) -> float | None:
    if isinstance(valeur, (int, float)):
        return float(valeur)
    if isinstance(valeur, str):
        try:
            return float(valeur.replace(",", "."))
        except ValueError:
            return None
    return None


#: Oubli des matchs anciens, par jour : poids exp(-OUBLI * age). 0.002 donne une
#: demi-vie d'environ un an. Balayage en walk-forward (4 996 matchs de 2026,
#: a priori 1 match) : 0 -> 0.5932, 0.002 -> 0.5924, 0.005 -> 0.5942.
OUBLI = 0.002

#: A priori des notes : chaque equipe part de ce nombre de matchs fictifs joues
#: exactement a la moyenne. C'est la regularisation qui empeche une equipe vue
#: deux fois de recevoir une note extreme. 1 -> 0.5924, 3 -> 0.5937.
A_PRIORI = 1.0

#: Passes de l'estimation. Chaque passe resout exactement chaque note, les
#: autres fixees ; huit suffisent a stabiliser le Brier au quatrieme chiffre.
PASSES = 12

#: Bornes des notes, en logarithme. e^2 = 7.4 : une borne de securite, que
#: l'a priori rend presque toujours inactive -- l'ancienne, 1.2, avait ete
#: choisie pour l'apprentissage en ligne et ecrasait les ecarts reels entre
#: un grand club et un club de troisieme division.
PLAFOND = 2.0


#: Fichier des notes attaque / defense.
CHEMIN = pathlib.Path(__file__).parent / "forces.json"


class Forces:
    """Notes attaque / defense d'un ensemble d'equipes.

    Les deux jeux de notes -- buts et xG -- ne servent pas a la meme chose, voir
    `lambdas_attendus`. Les notes sont estimees par `construire`.
    """

    def __init__(
        self,
        base: float,
        avantage: float,
        attaque: dict[str, float] | None = None,
        defense: dict[str, float] | None = None,
        joues: dict[str, int] | None = None,
    ) -> None:
        self.base = base
        self.avantage = avantage
        self.attaque: dict[str, float] = dict(attaque or {})
        self.defense: dict[str, float] = dict(defense or {})
        self.joues: dict[str, int] = dict(joues or {})

    def lambdas(self, domicile: str, exterieur: str) -> tuple[float, float]:
        """Nombres attendus pour les deux equipes."""
        return (
            math.exp(
                self.base + self.avantage
                + self.attaque.get(domicile, 0.0)
                - self.defense.get(exterieur, 0.0)
            ),
            math.exp(
                self.base
                + self.attaque.get(exterieur, 0.0)
                - self.defense.get(domicile, 0.0)
            ),
        )

    def etablies(self, *equipes: str) -> bool:
        return all(self.joues.get(e, 0) >= MATCHS_MIN for e in equipes)


#: Grandeurs de style : ce qu'une equipe produit, au-dela du score. Elles ne
#: servent PAS au modele -- elles servent a le commenter. Un simulateur qui ne
#: dirait que des buts serait juste et illisible.
CHAMPS_STYLE = ("corners", "tirs_cadres", "cartons_jaunes", "possession")

#: Sous ce nombre de fiches, une moyenne de style n'est pas rendue : trois
#: matchs ne disent pas comment une equipe joue.
STYLE_MIN = 4


def profils_de_style(matchs: Sequence[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Moyennes par equipe des grandeurs observees, et forme recente.

    Lues dans les fiches deja en cache : aucune requete. La possession arrive
    en pourcentage ("55%"), d'ou le nettoyage -- `_nombre` seul la rejetterait
    et la colonne serait vide sans que rien ne le signale.
    """
    reperes = {m["match_id"]: m for m in matchs if m.get("match_id")}
    cumuls: dict[str, dict[str, list[float]]] = {}

    def ajouter(equipe: str, champ: str, valeur: float) -> None:
        cumuls.setdefault(equipe, {}).setdefault(champ, []).append(valeur)

    if cache.CACHE_DIR.exists():
        for fichier in cache.CACHE_DIR.glob("*.json"):
            try:
                paquet = json.loads(fichier.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            cle = str(paquet.get("key", ""))
            if "|stats|" not in cle:
                continue
            match = reperes.get(cle.rsplit("|", 1)[-1])
            if not match:
                continue
            valeur = paquet.get("value") or {}
            for cote, equipe in (("domicile", match["domicile"]),
                                 ("exterieur", match["exterieur"])):
                bloc = valeur.get(cote) or {}
                for champ in CHAMPS_STYLE:
                    brut = bloc.get(champ)
                    if isinstance(brut, str):
                        brut = brut.strip().rstrip("%")
                    nombre = _nombre(brut)
                    if nombre is not None:
                        ajouter(equipe, champ, nombre)

    # Huit derniers resultats, du plus ancien au plus recent.
    formes: dict[str, list[str]] = {}
    for match in matchs:
        for equipe, pour, contre in (
            (match["domicile"], match["buts_domicile"], match["buts_exterieur"]),
            (match["exterieur"], match["buts_exterieur"], match["buts_domicile"]),
        ):
            lettre = "V" if pour > contre else ("N" if pour == contre else "D")
            formes.setdefault(equipe, []).append(lettre)

    profils: dict[str, dict[str, Any]] = {}
    for equipe, champs in cumuls.items():
        fiche: dict[str, Any] = {}
        for champ, valeurs in champs.items():
            if len(valeurs) >= STYLE_MIN:
                fiche[champ] = round(sum(valeurs) / len(valeurs), 2)
        if fiche:
            profils[equipe] = fiche
    for equipe, lettres in formes.items():
        profils.setdefault(equipe, {})["forme"] = "".join(lettres[-8:])
    return profils


def _jour(match: dict[str, Any]) -> int | None:
    try:
        return datetime.date.fromisoformat(
            (match.get("kickoff_utc") or "")[:10]
        ).toordinal()
    except ValueError:
        return None


def construire(
    matchs: Sequence[dict[str, Any]],
    cle_dom: str,
    cle_ext: str,
    oubli: float = OUBLI,
    a_priori: float = A_PRIORI,
    passes: int = PASSES,
) -> Forces:
    """Notes attaque / defense au maximum de vraisemblance, sur tout le corpus.

    C'est l'estimation de Maher (1982) et Dixon & Coles (1997) : toutes les
    notes a la fois, chaque match pondere par son anciennete. Elle remplace une
    descente de gradient en ligne -- un pas de 0.04 par match --, qui ne
    laissait pas aux notes le temps de s'ecarter de la moyenne : l'equipe
    mediane du corpus n'a que 4 matchs, et meme les grands clubs restaient
    tasses. PSG - Slovan Bratislava ressortait a 55 % pour Paris, le marche a
    95 %.

    Mesure, chaque match prevu avec les seules donnees d'avant lui :

        4 996 matchs de 2026     en ligne Brier 0.6014   ici 0.5924   t = -4.8
        67 fiches emises         en ligne Brier 0.5332   ici 0.4891
                                 (marche 0.4581 ; favori juste 45 -> 49 / 67,
                                 marche 50)

    Les notes restent CALIBREES : un favori annonce a 64 % gagne 63 % du temps.
    Le gain ne vient pas de probabilites plus tranchees, mais d'equipes mieux
    departagees.

    Chaque passe resout exactement chaque note, les autres fixees -- une
    moyenne ponderee, sans pas a regler :

        exp(attaque[i]) = (buts marques + a_priori) / (buts attendus + a_priori)

    `a_priori` ajoute a chaque equipe des matchs fictifs joues a la moyenne ;
    `oubli` fait peser un match d'il y a un an deux fois moins qu'un match
    d'hier. La date de reference est celle du dernier match du corpus.
    """
    lignes = []
    jours = [j for j in (_jour(m) for m in matchs) if j is not None]
    reference = max(jours) if jours else 0
    joues: dict[str, int] = {}
    for match in matchs:
        domicile, exterieur = match.get("domicile"), match.get("exterieur")
        if not domicile or not exterieur or domicile == exterieur:
            continue
        cible_dom, cible_ext = match.get(cle_dom), match.get(cle_ext)
        if cible_dom is None or cible_ext is None:
            # Un match sans xG ne dit rien des notes de xG : le compter ferait
            # passer pour etablie une equipe dont aucun xG n'a ete releve.
            continue
        joues[domicile] = joues.get(domicile, 0) + 1
        joues[exterieur] = joues.get(exterieur, 0) + 1
        jour = _jour(match)
        poids = math.exp(-oubli * (reference - jour)) if jour is not None else 1.0
        lignes.append((domicile, exterieur, float(cible_dom), float(cible_ext), poids))

    forces = Forces(0.0, 0.0, joues=joues)
    marques_dom = sum(l[2] * l[4] for l in lignes)
    marques_ext = sum(l[3] * l[4] for l in lignes)
    if marques_dom + marques_ext <= 0:
        return forces
    # Sans but d'un des deux cotes, terrain et attaque ne se separent plus :
    # le terrain reste neutre plutot que de partir a l'infini.
    terrain_estimable = marques_dom > 0 and marques_ext > 0

    # Notes multiplicatives pendant l'estimation : `offense` multiplie ce que
    # l'equipe marque, `faille` ce qu'elle concede.
    offense = {nom: 1.0 for nom in joues}
    faille = {nom: 1.0 for nom in joues}
    moyenne, terrain = 1.0, 1.0
    for _ in range(passes):
        attendus_dom = sum(l[4] * offense[l[0]] * faille[l[1]] for l in lignes)
        attendus_ext = sum(l[4] * offense[l[1]] * faille[l[0]] for l in lignes)
        moyenne = (marques_dom + marques_ext) / (terrain * attendus_dom + attendus_ext)
        if terrain_estimable:
            terrain = marques_dom / (moyenne * attendus_dom)
        dom_fac, ext_fac = moyenne * terrain, moyenne

        observe: dict[str, float] = {}
        attendu: dict[str, float] = {}
        for dom, ext, buts_dom, buts_ext, poids in lignes:
            observe[dom] = observe.get(dom, 0.0) + poids * buts_dom
            attendu[dom] = attendu.get(dom, 0.0) + poids * dom_fac * faille[ext]
            observe[ext] = observe.get(ext, 0.0) + poids * buts_ext
            attendu[ext] = attendu.get(ext, 0.0) + poids * ext_fac * faille[dom]
        for nom, total in observe.items():
            offense[nom] = (total + a_priori) / (attendu[nom] + a_priori)

        observe, attendu = {}, {}
        for dom, ext, buts_dom, buts_ext, poids in lignes:
            observe[ext] = observe.get(ext, 0.0) + poids * buts_dom
            attendu[ext] = attendu.get(ext, 0.0) + poids * dom_fac * offense[dom]
            observe[dom] = observe.get(dom, 0.0) + poids * buts_ext
            attendu[dom] = attendu.get(dom, 0.0) + poids * ext_fac * offense[ext]
        for nom, total in observe.items():
            faille[nom] = (total + a_priori) / (attendu[nom] + a_priori)

    forces.base = math.log(moyenne)
    forces.avantage = math.log(terrain)
    for nom in joues:
        forces.attaque[nom] = max(-PLAFOND, min(PLAFOND, math.log(offense[nom])))
        forces.defense[nom] = max(-PLAFOND, min(PLAFOND, -math.log(faille[nom])))
    return forces


#: Matchs fictifs a la moyenne globale ajoutes a chaque competition pour sa
#: moyenne de buts : une competition vue dix fois n'a pas de moyenne propre.
TOTAL_A_PRIORI = 10.0


def totaux_par_competition(
    matchs: Sequence[dict[str, Any]],
) -> tuple[float, dict[str, float]]:
    """Buts par match, en moyenne globale et par competition (nom normalise)."""
    if not matchs:
        return 0.0, {}
    cumuls: dict[str, list[float]] = {}
    for match in matchs:
        nom = normaliser_competition(match.get("competition", ""))
        cumul = cumuls.setdefault(nom, [0.0, 0.0])
        cumul[0] += match["buts_domicile"] + match["buts_exterieur"]
        cumul[1] += 1
    globale = sum(c[0] for c in cumuls.values()) / len(matchs)
    return globale, {
        nom: (buts + TOTAL_A_PRIORI * globale) / (nombre + TOTAL_A_PRIORI)
        for nom, (buts, nombre) in cumuls.items()
        if nom
    }


def normaliser_competition(nom: str) -> str:
    """Meme convention que `api_client.normalize_competition`, sans l'importer.

    Le flux du jour ajoute un suffixe de phase (« Liga Profesional -
    Cloture ») que les historiques n'ont pas.
    """
    return (nom or "").split(" - ", 1)[0].strip()


def rafraichir(chemin: pathlib.Path = CHEMIN) -> dict[str, Any]:
    """Reconstruit les deux jeux de notes depuis le cache et les ecrit."""
    matchs = corpus_depuis_cache()
    joindre_xg(matchs)
    groupes = composantes(matchs)
    buts = construire(matchs, "buts_domicile", "buts_exterieur")
    xg = construire(matchs, "xg_domicile", "xg_exterieur")
    total_moyen, totaux = totaux_par_competition(matchs)
    paquet = {
        "matchs": len(matchs),
        "matchs_xg": sum(1 for m in matchs if m.get("xg_domicile") is not None),
        # Le style sert a decrire une equipe, pas a la prevoir. Il vit
        # ici pour que l'API n'ait rien a relire du cache a chaque appel.
        "profils": profils_de_style(matchs),
        "base": buts.base,
        "avantage": buts.avantage,
        "groupes": {nom: numero for nom, numero in groupes.items()},
        # Reference du resserrement des totaux, voir `lambdas_attendus`.
        "total_moyen": total_moyen,
        "totaux": totaux,
        "buts": {
            "attaque": buts.attaque, "defense": buts.defense, "joues": buts.joues
        },
        "xg": {
            "attaque": xg.attaque, "defense": xg.defense, "joues": xg.joues
        },
    }
    chemin.write_text(json.dumps(paquet, ensure_ascii=False), encoding="utf-8")
    return paquet


_forces: dict[str, Any] | None = None


def charger(chemin: pathlib.Path = CHEMIN) -> dict[str, Any]:
    global _forces
    if _forces is None:
        try:
            _forces = json.loads(chemin.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _forces = {}
    return _forces


# Au-dela de ce retard, les notes meritent d'etre refaites. Ce n'est pas un
# seuil de justesse -- des notes un peu anciennes restent bonnes -- mais le
# point ou l'ecart devient assez grand pour changer QUI est note : c'est en
# franchissant ce cap que les selections sont passees de « aucune note » a
# « toutes notees ».
RETARD_TOLERE = 0.10


def fraicheur(paquet: dict[str, Any] | None = None) -> dict[str, Any]:
    """Les notes en service sont-elles construites sur le corpus actuel ?

    Un fichier de notes ne se perime pas bruyamment : il continue de repondre,
    avec des valeurs plausibles, simplement calculees sur moins de matchs qu'il
    n'en existe. Rien ne le signale, et c'est ce qui s'est produit -- le corpus
    a grossi d'un tiers (12 710 -> 16 855 matchs) sans que les notes soient
    refaites. Les consequences n'etaient pas subtiles : AUCUNE selection
    n'atteignait le seuil de huit matchs dans l'ancien corpus, donc toutes les
    fiches de selection retombaient sur le modele de forme -- celui que la
    mesure donne perdant de 0.097 d'erreur sur l'ecart (t = -5.5).

    D'ou cette fonction : elle compare le nombre de matchs dont les notes sont
    issues a ce que le cache contient maintenant.
    """
    notes = paquet if paquet is not None else charger()
    appris = int(notes.get("matchs", 0) or 0)
    disponibles = len(corpus_depuis_cache())
    retard = (disponibles - appris) / disponibles if disponibles else 0.0
    return {
        "appris": appris,
        "disponibles": disponibles,
        "retard": retard,
        "a_refaire": bool(notes) and retard > RETARD_TOLERE,
    }


def avertissement() -> str:
    """Phrase a afficher quand les notes sont en retard, sinon chaine vide.

    Rendue plutot qu'imprimee : `forecast` l'ecrit sur la sortie d'erreur,
    l'API la sert dans sa reponse, et un test la lit. Trois usages, une seule
    formulation.
    """
    etat = fraicheur()
    if not etat["a_refaire"]:
        return ""
    return (
        "Notes de force en retard : construites sur %d matchs, le cache en "
        "contient %d (%.0f %% de retard). Relancez `python forces.py` -- sans "
        "quoi des equipes pourtant couvertes restent sans note, et leurs "
        "previsions retombent sur le modele de forme."
        % (etat["appris"], etat["disponibles"], etat["retard"] * 100)
    )


def _jeu(paquet: dict[str, Any], nom: str) -> Forces | None:
    bloc = paquet.get(nom)
    if not bloc:
        return None
    return Forces(
        paquet.get("base", 0.0), paquet.get("avantage", 0.0),
        bloc.get("attaque"), bloc.get("defense"), bloc.get("joues"),
    )


#: Part de l'ecart a la moyenne de la competition conservee dans le total
#: attendu. Les notes etalent trop les totaux : en walk-forward (4 899 matchs de
#: 2026), la pente du total reel sur le total prevu n'est que de 0.52 -- un
#: match annonce a 3.8 buts en donne 3.3, un match annonce a 1.8 en donne 2.3.
#: Resserrer de moitie vers la moyenne de la competition :
#:
#:     Brier des seuils 1.5 / 2.5 / 3.5    0.2141 -> 0.2087, t = -7.8
#:     propositions de total a 60-95 %     annonce 73.6 / observe 71.2
#:                                     ->  annonce 72.2 / observe 72.7
#:
#: 1 laisse les notes inchangees. Le meme resserrement sur l'ECART n'apporte
#: rien de mesurable (t = -1.1) : il n'est pas applique.
RESSERREMENT_TOTAL = 0.5


def lambdas_attendus(
    domicile: str,
    exterieur: str,
    paquet: dict[str, Any] | None = None,
    competition: str = "",
) -> tuple[float, float] | None:
    """Nombres de buts attendus pour les deux equipes, ou None si inconnus.

    Le TOTAL vient des notes sur les xG, l'ECART des notes sur les buts. Ce
    partage n'est pas un compromis : il est mesure, sur 1 201 matchs ou les deux
    jeux de notes sont etablis.

        notes sur les buts      Brier 0.5969   erreur sur le total 1.388
        notes sur les xG        Brier 0.6033   erreur sur le total 1.344
        total xG + ecart buts   Brier 0.5975   erreur sur le total 1.343

    Les xG lissent la reussite devant le but : ils disent mieux combien
    d'occasions une equipe se procure (donc le volume, t = -3.9 sur l'erreur de
    total), moins bien laquelle des deux convertit (l'ecart, ou les buts reels
    gardent l'avantage). Prendre le meilleur des deux sur chaque moitie donne le
    total des xG sans rien perdre sur l'issue.

    Le total est ensuite resserre vers la moyenne de la `competition` (voir
    `RESSERREMENT_TOTAL`) ; l'ecart, lui, est garde tel quel.

    Rend None des que les notes ne sont pas etablies ou que les deux equipes
    n'appartiennent pas au meme groupe : deux notes construites dans deux jeux a
    somme nulle separes ne se soustraient pas (voir `composantes`).
    """
    paquet = paquet if paquet is not None else charger()
    if not paquet:
        return None
    groupes = paquet.get("groupes") or {}
    gauche, droite = groupes.get(domicile), groupes.get(exterieur)
    if gauche is None or gauche != droite:
        return None

    buts = _jeu(paquet, "buts")
    if buts is None or not buts.etablies(domicile, exterieur):
        return None
    lam_buts = buts.lambdas(domicile, exterieur)
    ecart = lam_buts[0] - lam_buts[1]

    xg = _jeu(paquet, "xg")
    if xg is None or not xg.etablies(domicile, exterieur):
        # Sans notes de xG, les notes sur les buts font les deux moities.
        total = lam_buts[0] + lam_buts[1]
    else:
        lam_xg = xg.lambdas(domicile, exterieur)
        total = lam_xg[0] + lam_xg[1]

    # Un paquet sans moyennes (ancien fichier de notes) laisse le total tel quel.
    reference = (paquet.get("totaux") or {}).get(
        normaliser_competition(competition), paquet.get("total_moyen")
    )
    if reference:
        total = reference + RESSERREMENT_TOTAL * (total - reference)

    # Le total borne l'ecart, sans quoi un lambda deviendrait negatif.
    limite = max(0.0, total - 0.1)
    ecart = max(-limite, min(limite, ecart))
    return (total + ecart) / 2, (total - ecart) / 2


if __name__ == "__main__":  # python forces.py : reconstruit les notes
    import sys

    paquet = rafraichir()
    print(
        "%d matchs dont %d avec xG, %d groupes -> %s"
        % (paquet["matchs"], paquet["matchs_xg"],
           len(set(paquet["groupes"].values())), CHEMIN.name),
        file=sys.stderr,
    )
    print(
        "  moyenne de reference %.3f but par equipe, avantage du terrain %.3f"
        % (math.exp(paquet["base"]), paquet["avantage"]),
        file=sys.stderr,
    )
    pretes = sum(1 for n in paquet["buts"]["joues"].values() if n >= MATCHS_MIN)
    print("  %d equipes aux notes etablies (>= %d matchs)" % (pretes, MATCHS_MIN),
          file=sys.stderr)
