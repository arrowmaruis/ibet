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

    La litterature l'estime par maximum de vraisemblance sur une saison entiere.
    Un systeme qui cote en continu ne le peut pas : il doit se mettre a jour
    apres chaque match sans tout reestimer. Il emploie donc la descente de
    gradient en ligne, dont la mise a jour pour une vraisemblance de Poisson est
    d'une simplicite remarquable :

        d log L / d attaque[dom] = buts_dom - lambda_dom

    soit « on monte l'attaque quand l'equipe marque plus que prevu ». L'echelle
    se construit ainsi toute seule, sans qu'on ait a declarer qu'un championnat
    vaut plus qu'un autre : battre une equipe bien notee rapporte plus que
    battre une equipe faible.

  - **Deux jeux de notes, deux usages.** Celles construites sur les BUTS donnent
    le meilleur ecart entre les deux equipes ; celles construites sur les xG
    donnent le meilleur TOTAL. Les xG lissent la reussite devant le but : ils
    disent mieux combien d'occasions on se procure, moins bien laquelle des deux
    convertit. Voir `lambdas_attendus`.

  - **Rien n'est declare, tout est mesure.** La moyenne de reference, l'avantage
    du terrain et le pas d'apprentissage sont estimes sur le corpus.

Mesure en walk-forward sur 5 360 matchs, chaque match prevu avec les notes
d'AVANT lui :

    forme recente seule      Brier 0.6196     <- ce que faisait le modele
    Elo (ecart seul)         Brier 0.6047     t = -4.8 contre la forme
    attaque / defense        Brier 0.5916     t = -11.7 contre la forme
    uniforme                 Brier 0.6667

Et, contrairement a Elo, elles ameliorent aussi le TOTAL : erreur absolue 1.396
contre 1.437, t = -5.9. Elo ne savait que departager deux equipes ; il a donc
ete remplace par ce modele, qui dit aussi combien de buts attendre.

Les notes se construisent a partir des historiques deja en cache -- douze mille
matchs de 2003 a aujourd'hui, sans une requete de plus.

    python forces.py     # reconstruit forces.json
"""

from __future__ import annotations

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


#: Pas d'apprentissage. Regle par balayage : 0.01 -> 0.6083, 0.02 -> 0.5967,
#: 0.04 -> 0.5916, 0.08 -> 0.5971. Minimum interieur net.
PAS = 0.04

#: Bornes des notes, en logarithme. e^1.2 = 3.3 : aucune equipe ne marque plus
#: de trois fois la moyenne, et sans borne une serie de matchs aberrants
#: enverrait une note a l'infini.
PLAFOND = 1.2


#: Fichier des notes attaque / defense.
CHEMIN = pathlib.Path(__file__).parent / "forces.json"


class Forces:
    """Notes attaque / defense d'un ensemble d'equipes, mises a jour en ligne.

    `cible` extrait du match ce que les notes doivent predire : les buts, ou les
    xG. Les deux jeux de notes ne servent pas a la meme chose -- voir
    `lambdas_attendus`.
    """

    def __init__(
        self,
        base: float,
        avantage: float,
        pas: float = PAS,
        attaque: dict[str, float] | None = None,
        defense: dict[str, float] | None = None,
        joues: dict[str, int] | None = None,
    ) -> None:
        self.base = base
        self.avantage = avantage
        self.pas = pas
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

    def apprendre(
        self, domicile: str, exterieur: str, cible_dom: float | None,
        cible_ext: float | None
    ) -> None:
        """Une passe de gradient sur un match, puis bornage des notes."""
        if cible_dom is not None and cible_ext is not None:
            lam_dom, lam_ext = self.lambdas(domicile, exterieur)
            ecart_dom = cible_dom - lam_dom
            ecart_ext = cible_ext - lam_ext
            self.attaque[domicile] = self.attaque.get(domicile, 0.0) + self.pas * ecart_dom
            self.defense[exterieur] = self.defense.get(exterieur, 0.0) - self.pas * ecart_dom
            self.attaque[exterieur] = self.attaque.get(exterieur, 0.0) + self.pas * ecart_ext
            self.defense[domicile] = self.defense.get(domicile, 0.0) - self.pas * ecart_ext
            for table in (self.attaque, self.defense):
                for nom in (domicile, exterieur):
                    table[nom] = max(-PLAFOND, min(PLAFOND, table[nom]))
        self.joues[domicile] = self.joues.get(domicile, 0) + 1
        self.joues[exterieur] = self.joues.get(exterieur, 0) + 1


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


def _reperes(matchs: Sequence[dict[str, Any]]) -> tuple[float, float]:
    """Moyenne de reference et avantage du terrain, en logarithme."""
    total = sum(m["buts_domicile"] + m["buts_exterieur"] for m in matchs)
    a_domicile = sum(m["buts_domicile"] for m in matchs)
    a_exterieur = sum(m["buts_exterieur"] for m in matchs)
    if not matchs or total <= 0 or a_domicile <= 0 or a_exterieur <= 0:
        return 0.0, 0.0
    return (
        math.log(total / (2 * len(matchs))),
        0.5 * math.log(a_domicile / a_exterieur),
    )


def construire(
    matchs: Sequence[dict[str, Any]], cle_dom: str, cle_ext: str, pas: float = PAS
) -> Forces:
    """Notes attaque / defense, du plus ancien match au plus recent.

    L'ordre chronologique est impose : une note est un etat qui se construit.
    """
    base, avantage = _reperes(matchs)
    forces = Forces(base, avantage, pas)
    for match in sorted(matchs, key=lambda m: m.get("kickoff_utc") or ""):
        domicile, exterieur = match.get("domicile"), match.get("exterieur")
        if not domicile or not exterieur or domicile == exterieur:
            continue
        forces.apprendre(
            domicile, exterieur, match.get(cle_dom), match.get(cle_ext)
        )
    return forces


def rafraichir(chemin: pathlib.Path = CHEMIN) -> dict[str, Any]:
    """Reconstruit les deux jeux de notes depuis le cache et les ecrit."""
    matchs = corpus_depuis_cache()
    joindre_xg(matchs)
    groupes = composantes(matchs)
    buts = construire(matchs, "buts_domicile", "buts_exterieur")
    xg = construire(matchs, "xg_domicile", "xg_exterieur")
    paquet = {
        "matchs": len(matchs),
        "matchs_xg": sum(1 for m in matchs if m.get("xg_domicile") is not None),
        # Le style sert a decrire une equipe, pas a la prevoir. Il vit
        # ici pour que l'API n'ait rien a relire du cache a chaque appel.
        "profils": profils_de_style(matchs),
        "base": buts.base,
        "avantage": buts.avantage,
        "groupes": {nom: numero for nom, numero in groupes.items()},
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
        paquet.get("base", 0.0), paquet.get("avantage", 0.0), PAS,
        bloc.get("attaque"), bloc.get("defense"), bloc.get("joues"),
    )


def lambdas_attendus(
    domicile: str, exterieur: str, paquet: dict[str, Any] | None = None
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

    xg = _jeu(paquet, "xg")
    if xg is None or not xg.etablies(domicile, exterieur):
        # Sans notes de xG, les notes sur les buts font les deux moities.
        return lam_buts

    lam_xg = xg.lambdas(domicile, exterieur)
    total = lam_xg[0] + lam_xg[1]
    ecart = lam_buts[0] - lam_buts[1]
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
